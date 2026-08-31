package com.unilink.app.auth

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.widget.Toast
import androidx.browser.customtabs.CustomTabsIntent
import com.unilink.app.Hub

/**
 * authentik 登录 Activity
 * ========================
 * 只做两件事，没有自己的界面：
 *  1. 首次进入 → 生成 PKCE 参数，用系统浏览器打开 authentik 授权页；
 *  2. 浏览器回跳 `unilink://auth/callback?code=…&state=…` → 用 code 换令牌。
 *
 * 为什么用系统浏览器（Custom Tabs）而不是 WebView：
 *  - OAuth 2.0 for Native Apps（RFC 8252）明确要求用外部用户代理，
 *    WebView 内嵌登录页无法让用户确认地址栏，钓鱼风险高；
 *  - 可复用浏览器里已有的 authentik 会话，多数情况下点一下就登录完了；
 *  - 支持 passkey / 硬件密钥等 WebView 里跑不通的认证方式。
 *
 * PKCE verifier 保存在内存静态字段而非 Intent 里 —— 避免被其它应用
 * 通过 recent tasks 或 Intent 嗅探读到。进程被杀则登录流程作废（用户重试即可）。
 */
class LoginActivity : Activity() {

    /** 已经把用户送去浏览器了吗？用于识别"用户按返回键放弃登录"的情况 */
    private var browserLaunched = false

    companion object {
        private const val EXTRA_SERVER = "qr_server"

        /** 跨 Activity 生命周期保存的一次性 PKCE 状态 */
        @Volatile private var pending: AuthSession.Pkce? = null
        @Volatile private var pendingEp: AuthClient.Endpoints? = null

        fun start(ctx: Context, qrServer: String) {
            ctx.startActivity(Intent(ctx, LoginActivity::class.java)
                .putExtra(EXTRA_SERVER, qrServer)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // 浏览器回跳会以新 Intent 进入本 Activity（singleTask）
        val data = intent?.data
        if (data != null && data.scheme == "unilink") {
            handleCallback(data)
        } else {
            beginAuthorize(intent?.getStringExtra(EXTRA_SERVER).orEmpty())
        }
    }

    override fun onNewIntent(intent: Intent?) {
        super.onNewIntent(intent)
        // 必须替换掉旧 Intent，否则 onResume 里读到的还是最初那个（data 为空）
        setIntent(intent)
        val data = intent?.data
        if (data != null && data.scheme == "unilink") handleCallback(data)
        else finish()
    }

    // ---------------- 第 1 步：打开授权页 ----------------

    private fun beginAuthorize(server: String) {
        val qrServer = server.ifBlank { AuthSession.qrServer(this) }
        if (qrServer.isBlank()) {
            toast("请先在主界面填写扫码登录服务地址")
            finish()
            return
        }
        AuthSession.setQrServer(this, qrServer)

        Thread {
            val client = AuthClient(this)
            try {
                val ep = client.fetchEndpoints(qrServer)
                val pkce = AuthSession.newPkce()
                pending = pkce
                pendingEp = ep
                val url = client.buildAuthorizeUrl(ep, pkce)
                runOnUiThread { openBrowser(url) }
            } catch (t: Throwable) {
                Hub.log("⚠ 无法获取登录配置：${t.message}")
                runOnUiThread {
                    toast("无法连接扫码登录服务：${t.message}")
                    finish()
                }
            }
        }.start()
    }

    private fun openBrowser(url: String) {
        try {
            CustomTabsIntent.Builder()
                .setShowTitle(true)
                .build()
                .launchUrl(this, Uri.parse(url))
            browserLaunched = true
        } catch (t: Throwable) {
            // 没装支持 Custom Tabs 的浏览器 → 退回普通 VIEW Intent
            try {
                startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
                browserLaunched = true
            } catch (t2: Throwable) {
                toast("没有可用的浏览器，无法登录")
                finish()
            }
        }
    }

    /**
     * 用户在浏览器里按了返回键 / 关掉了标签页时会回到本 Activity，
     * 此时既没有回调 Intent 也不会再有 —— 直接收尾，避免留一个
     * 透明空窗口挡在界面上，也顺手清掉悬空的 PKCE 状态。
     */
    override fun onResume() {
        super.onResume()
        if (browserLaunched && intent?.data == null) {
            pending = null
            pendingEp = null
            finish()
        }
    }

    // ---------------- 第 2 步：处理回跳 ----------------

    private fun handleCallback(data: Uri) {
        val pkce = pending
        val ep = pendingEp
        pending = null
        pendingEp = null

        val err = data.getQueryParameter("error")
        if (err != null) {
            val desc = data.getQueryParameter("error_description") ?: err
            Hub.log("⚠ authentik 拒绝登录：$desc")
            toast("登录被拒绝：$desc")
            finish()
            return
        }

        val code = data.getQueryParameter("code")
        val state = data.getQueryParameter("state")
        if (pkce == null || ep == null || code.isNullOrBlank()) {
            toast("登录流程已失效，请重试")
            finish()
            return
        }
        // state 必须与本次请求一致，否则可能是 CSRF 或串号的回跳
        if (state != pkce.state) {
            Hub.log("⚠ 登录回调 state 不匹配，已丢弃")
            toast("登录校验失败（state 不匹配），请重试")
            finish()
            return
        }

        Thread {
            try {
                val info = AuthClient(this).exchangeCode(ep, code, pkce.verifier)
                val name = info.optString("preferred_username")
                    .ifBlank { info.optString("email") }
                Hub.log("🔑 已登录 authentik：$name")
                runOnUiThread {
                    toast("登录成功：$name")
                    finish()
                }
            } catch (t: Throwable) {
                Hub.log("⚠ 换取令牌失败：${t.message}")
                runOnUiThread {
                    toast("登录失败：${t.message}")
                    finish()
                }
            }
        }.start()
    }

    private fun toast(s: String) =
        Toast.makeText(this, s, Toast.LENGTH_LONG).show()
}
