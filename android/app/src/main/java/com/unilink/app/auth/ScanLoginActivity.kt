package com.unilink.app.auth

import android.app.Activity
import android.app.AlertDialog
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.widget.Toast
import com.google.zxing.integration.android.IntentIntegrator
import com.unilink.app.Hub
import com.unilink.app.R

/**
 * 扫码登录 Activity
 * =================
 * 流程（本 Activity 自身无界面，只弹系统对话框）：
 *   打开相机扫码 → 解析二维码 → 调 preview 取回"要登录到哪里"
 *   → 弹确认框 → 用户点确认 → 调 approve 把 authentik 令牌换成授权码
 *
 * 关键的安全考量：**必须有用户明示确认这一步**。
 * 否则任何人只要把一张二维码摆在用户面前，用户随手一扫就会
 * 静默登录到攻击者控制的会话里。确认框会显示目标应用名，
 * 让用户能识别出"我根本没在登录这个东西"。
 *
 * 关于 IntentIntegrator：新版 zxing 推荐 ScanContract + ActivityResultLauncher，
 * 但那需要 ComponentActivity 与 AppCompat 主题。本项目所有 Activity 都是
 * 朴素的 android.app.Activity，为保持一致这里沿用经典的 startActivityForResult
 * 路径（该类在 4.3.0 中仍然可用，仅被标记 deprecated）。
 */
@Suppress("DEPRECATION")
class ScanLoginActivity : Activity() {

    private var endpoints: AuthClient.Endpoints? = null

    companion object {
        fun start(ctx: Context) {
            ctx.startActivity(Intent(ctx, ScanLoginActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // 配置变更导致重建时不要再开一次相机（否则会叠两层扫码界面）
        if (savedInstanceState != null) return

        AuthSession.load(this)
        if (!AuthSession.loggedIn) {
            toast("请先登录 authentik 账号")
            finish()
            return
        }

        val server = AuthSession.qrServer(this)
        if (server.isBlank()) {
            toast("尚未配置扫码登录服务地址")
            finish()
            return
        }

        // 端点信息用于必要时刷新令牌；取不到也继续（可能只是暂时离线）
        Thread {
            endpoints = try {
                AuthClient(this).fetchEndpoints(server)
            } catch (t: Throwable) {
                null
            }
            runOnUiThread { launchScanner() }
        }.start()
    }

    private fun launchScanner() {
        if (isFinishing) return
        IntentIntegrator(this).apply {
            setDesiredBarcodeFormats(IntentIntegrator.QR_CODE)
            setPrompt(getString(R.string.scan_prompt))
            setBeepEnabled(false)
            setOrientationLocked(true)
            setBarcodeImageEnabled(false)
        }.initiateScan()
    }

    @Suppress("DEPRECATION")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != IntentIntegrator.REQUEST_CODE) {
            finish()
            return
        }
        val res = IntentIntegrator.parseActivityResult(resultCode, data)
        val text = res?.contents
        if (text.isNullOrBlank()) {      // 用户按返回键取消
            finish()
            return
        }

        val client = AuthClient(this)
        val ticket = client.parseQr(text)
        if (ticket == null) {
            Hub.log("⚠ 扫到的二维码不是 UniLink 登录码（或地址不是 https）")
            alert("无法识别",
                "这不是 UniLink 的登录二维码。\n\n" +
                        "若确实是登录码，请检查扫码服务是否已配置 https —— " +
                        "出于安全考虑，App 拒绝把账号令牌发往公网 http 地址。")
            return
        }

        // 校验二维码来源与本机登录时用的服务是否一致，防止跨服务器钓鱼
        val known = AuthSession.qrServer(this)
        if (known.isNotBlank() && !ticket.server.equals(known, ignoreCase = true)) {
            Hub.log("⚠ 已阻止指向其它服务器的登录二维码：${ticket.server}")
            alert("⚠ 服务器不一致",
                "二维码指向：\n${ticket.server}\n\n" +
                        "但你的账号登录在：\n$known\n\n" +
                        "这可能是钓鱼二维码，已阻止。")
            return
        }

        preview(client, ticket)
    }

    private fun alert(title: String, msg: String) {
        if (isFinishing) return
        AlertDialog.Builder(this)
            .setTitle(title)
            .setMessage(msg)
            .setPositiveButton("知道了") { _, _ -> finish() }
            .setOnCancelListener { finish() }
            .show()
    }

    // ---------------- 预览 → 确认 ----------------

    private fun preview(client: AuthClient, ticket: QrTicket.Ticket) {
        val device = Build.MODEL ?: "Android"
        Thread {
            try {
                val info = client.preview(ticket, device)
                val app = info.optString("app").ifBlank { "未知应用" }
                runOnUiThread { confirm(client, ticket, app) }
            } catch (t: Throwable) {
                Hub.log("⚠ 二维码校验失败：${t.message}")
                runOnUiThread {
                    toast("二维码已失效：${t.message}")
                    finish()
                }
            }
        }.start()
    }

    private fun confirm(client: AuthClient, ticket: QrTicket.Ticket, app: String) {
        if (isFinishing) return
        val who = AuthSession.displayName(this).ifBlank { "当前账号" }
        AlertDialog.Builder(this)
            .setTitle("确认登录")
            .setMessage("将以【$who】的身份登录：\n\n$app\n\n" +
                    "如果这不是你本人在电脑上发起的登录，请点「不是我」。")
            .setPositiveButton("确认登录") { _, _ -> approve(client, ticket) }
            .setNegativeButton("不是我") { _, _ -> deny(client, ticket) }
            .setCancelable(false)
            .show()
    }

    // ---------------- 确认 / 拒绝 ----------------

    private fun approve(client: AuthClient, ticket: QrTicket.Ticket) {
        Thread {
            try {
                // 本地令牌可能已过期，这里取一个保证可用的
                val ep = endpoints
                val token = if (ep != null) client.validAccessToken(ep)
                            else AuthSession.accessToken(this)
                if (token.isNullOrBlank()) {
                    runOnUiThread {
                        toast("登录状态已过期，请重新登录 authentik")
                        finish()
                    }
                    return@Thread
                }
                val r = client.approve(ticket, token)
                Hub.log("✅ 已确认扫码登录：${r.optString("app").ifBlank { "该应用" }}")
                runOnUiThread {
                    toast("已确认，电脑上即将完成登录")
                    finish()
                }
            } catch (t: Throwable) {
                Hub.log("⚠ 扫码登录确认失败：${t.message}")
                runOnUiThread {
                    toast("确认失败：${t.message}")
                    finish()
                }
            }
        }.start()
    }

    private fun deny(client: AuthClient, ticket: QrTicket.Ticket) {
        Thread {
            try {
                client.deny(ticket)
            } catch (_: Throwable) {
                // 拒绝属于尽力而为：即便网络失败，不给出授权码同样等于拒绝
            }
            Hub.log("已拒绝一次扫码登录请求")
            runOnUiThread {
                toast("已拒绝该登录请求")
                finish()
            }
        }.start()
    }

    private fun toast(s: String) =
        Toast.makeText(this, s, Toast.LENGTH_LONG).show()
}
