package com.unilink.app

import android.Manifest
import android.app.Activity
import android.content.ClipboardManager
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.ScrollView
import android.widget.Switch
import android.widget.TextView
import android.widget.Toast
import com.unilink.app.auth.AuthSession
import com.unilink.app.auth.LoginActivity
import com.unilink.app.auth.QrTicket
import com.unilink.app.auth.ScanLoginActivity
import org.json.JSONObject

class MainActivity : Activity() {

    private lateinit var prefs: Prefs
    private lateinit var etServer: EditText
    private lateinit var etRoom: EditText
    private lateinit var etToken: EditText
    private lateinit var etName: EditText
    private lateinit var swMirror: Switch
    private lateinit var swRecvNotify: Switch
    private lateinit var swRecvClip: Switch
    private lateinit var tvStatus: TextView
    private lateinit var tvListener: TextView
    private lateinit var tvAuth: TextView
    private lateinit var etQrServer: EditText
    private lateinit var tvLog: TextView
    private lateinit var svLog: ScrollView

    private val handler = Handler(Looper.getMainLooper())

    private val tick = object : Runnable {
        override fun run() {
            tvStatus.text = Hub.status
            val nl = if (notifListenerEnabled())
                "🟢 通知读取权限：已授予${if (Hub.listenerBound) "（工作中）" else ""}"
            else
                "🔴 通知读取权限：未授予 —— 请点「授予通知使用权」"
            val al = if (Hub.a11y) "  |  🟢 无障碍自动回复：已开启"
                     else "  |  ⚪ 无障碍自动回复：未开启（可选，点下方按钮开启）"
            tvListener.text = nl + al
            refreshAuthLine()
            val sb = StringBuilder()
            synchronized(Hub.logs) {
                for (l in Hub.logs) sb.append(l).append('\n')
            }
            if (sb.isNotEmpty()) {
                tvLog.text = sb.toString()
                svLog.post { svLog.fullScroll(View.FOCUS_DOWN) }
            }
            handler.postDelayed(this, 600)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        prefs = Prefs(this)

        etServer = findViewById(R.id.etServer)
        etRoom = findViewById(R.id.etRoom)
        etToken = findViewById(R.id.etToken)
        etName = findViewById(R.id.etName)
        swMirror = findViewById(R.id.swMirror)
        swRecvNotify = findViewById(R.id.swRecvNotify)
        swRecvClip = findViewById(R.id.swRecvClip)
        tvStatus = findViewById(R.id.tvStatus)
        tvListener = findViewById(R.id.tvListener)
        tvAuth = findViewById(R.id.tvAuth)
        etQrServer = findViewById(R.id.etQrServer)
        tvLog = findViewById(R.id.tvLog)
        svLog = findViewById(R.id.svLog)

        etServer.setText(prefs.server)
        etRoom.setText(prefs.room)
        etToken.setText(prefs.token)
        etName.setText(prefs.deviceName)
        swMirror.isChecked = prefs.mirrorOut
        swRecvNotify.isChecked = prefs.recvNotify
        swRecvClip.isChecked = prefs.recvClip

        AuthSession.load(this)
        etQrServer.setText(AuthSession.qrServer(this))
        refreshAuthLine(force = true)

        findViewById<Button>(R.id.btnStart).setOnClickListener { save(); start() }
        findViewById<Button>(R.id.btnStop).setOnClickListener {
            startService(Intent(this, LinkService::class.java)
                .setAction(LinkService.ACTION_STOP))
        }
        findViewById<Button>(R.id.btnAccess).setOnClickListener {
            try {
                startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))
            } catch (t: Throwable) {
                Toast.makeText(this, "无法打开设置页，请手动前往", Toast.LENGTH_SHORT).show()
            }
        }
        findViewById<Button>(R.id.btnPerm).setOnClickListener { reqNotifPerm() }
        findViewById<Button>(R.id.btnA11y).setOnClickListener {
            try {
                startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
                Toast.makeText(this,
                    "在列表中找到 UniLink → 开启「UniLink 自动回复」",
                    Toast.LENGTH_LONG).show()
            } catch (t: Throwable) {
                Toast.makeText(this, "无法打开无障碍设置，请手动前往", Toast.LENGTH_SHORT).show()
            }
        }
        findViewById<Button>(R.id.btnShareClip).setOnClickListener { shareClip() }
        findViewById<Button>(R.id.btnClear).setOnClickListener {
            synchronized(Hub.logs) { Hub.logs.clear() }
        }

        findViewById<Button>(R.id.btnLogin).setOnClickListener { doLogin() }
        findViewById<Button>(R.id.btnScanLogin).setOnClickListener { doScanLogin() }
        findViewById<Button>(R.id.btnLogout).setOnClickListener { doLogout() }

        handler.post(tick)
    }

    override fun onPause() {
        super.onPause()
        save()
    }

    override fun onResume() {
        super.onResume()
        // 从浏览器 / 扫码页返回时刷新登录态显示
        AuthSession.load(this)
        refreshAuthLine(force = true)
    }

    override fun onDestroy() {
        super.onDestroy()
        handler.removeCallbacksAndMessages(null)
    }

    // ------------------------------------------------------------------

    private fun save() {
        prefs.server = etServer.text.toString().trim()
        prefs.room = etRoom.text.toString().trim()
        prefs.token = etToken.text.toString()
        prefs.deviceName = etName.text.toString().trim()
            .ifBlank { Build.MODEL ?: "Android" }
        prefs.mirrorOut = swMirror.isChecked
        prefs.recvNotify = swRecvNotify.isChecked
        prefs.recvClip = swRecvClip.isChecked
        val qr = etQrServer.text.toString().trim()
        if (qr.isNotBlank()) AuthSession.setQrServer(this, qr)
    }

    // ================= 扫码登录 authentik =================

    /** 上一次渲染的登录态，避免 600ms 心跳里反复读 SharedPreferences */
    private var authLineShown: String? = null

    private fun refreshAuthLine(force: Boolean = false) {
        val key = AuthSession.cachedSub ?: ""
        if (!force && key == authLineShown) return
        authLineShown = key
        tvAuth.text = if (AuthSession.loggedIn) {
            val name = AuthSession.displayName(this).ifBlank { AuthSession.cachedUser }
            "🔑 已登录 authentik：$name —— 可扫码登录任意接入 authentik 的项目"
        } else {
            getString(R.string.login_none)
        }
    }

    /**
     * 校验扫码服务地址。要求 https 或私有网段 http ——
     * 公网 http 会让 authentik 令牌以明文过网。
     * 判定逻辑与二维码解析共用 [QrTicket.isAcceptableServer]，避免两处规则漂移。
     */
    private fun validQrServer(): String? {
        val url = etQrServer.text.toString().trim().trimEnd('/')
        if (url.isBlank()) {
            Toast.makeText(this, "请先填写扫码登录服务地址", Toast.LENGTH_SHORT).show()
            return null
        }
        if (!QrTicket.isAcceptableServer(url)) {
            Toast.makeText(this,
                "地址必须是 https（局域网 http 亦可）——\n公网 http 会让账号令牌明文传输",
                Toast.LENGTH_LONG).show()
            return null
        }
        AuthSession.setQrServer(this, url)
        return url
    }

    private fun doLogin() {
        val url = validQrServer() ?: return
        LoginActivity.start(this, url)
    }

    private fun doScanLogin() {
        if (!AuthSession.loggedIn) {
            Toast.makeText(this, "请先点「登录 authentik」完成账号登录",
                Toast.LENGTH_LONG).show()
            return
        }
        if (validQrServer() == null) return
        ScanLoginActivity.start(this)
    }

    private fun doLogout() {
        if (!AuthSession.loggedIn) {
            Toast.makeText(this, "当前未登录", Toast.LENGTH_SHORT).show()
            return
        }
        AuthSession.logout(this)
        Hub.log("🔒 已退出 authentik 登录（本地令牌已清除）")
        refreshAuthLine(force = true)
        Toast.makeText(this, "已退出登录", Toast.LENGTH_SHORT).show()
    }

    private fun start() {
        if (prefs.server.isBlank() || prefs.room.isBlank()) {
            Toast.makeText(this, "请填写服务器地址与房间码", Toast.LENGTH_SHORT).show()
            return
        }
        reqNotifPerm(silent = true)
        Hub.deviceName = prefs.deviceName
        CryptoHolder.reset()   // 房间码/令牌可能已修改，重建密钥
        val i = Intent(this, LinkService::class.java).setAction(LinkService.ACTION_START)
        if (Build.VERSION.SDK_INT >= 26) startForegroundService(i) else startService(i)
        Toast.makeText(this, "正在连接…", Toast.LENGTH_SHORT).show()
    }

    private fun reqNotifPerm(silent: Boolean = false) {
        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)
            != PackageManager.PERMISSION_GRANTED
        ) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        } else if (!silent) {
            Toast.makeText(this, "通知权限已授予", Toast.LENGTH_SHORT).show()
        }
    }

    @Suppress("DEPRECATION")
    private fun notifListenerEnabled(): Boolean {
        val raw = Settings.Secure.getString(
            contentResolver, "enabled_notification_listeners") ?: return false
        return raw.split(":").any { it.contains(packageName) }
    }

    private fun shareClip() {
        val cm = getSystemService(CLIPBOARD_SERVICE) as ClipboardManager
        val text = cm.primaryClip?.getItemAt(0)?.coerceToText(this)?.toString().orEmpty()
        if (text.isEmpty()) {
            Toast.makeText(this, "剪贴板为空", Toast.LENGTH_SHORT).show()
            return
        }
        Hub.submit(Proto.envelope("clipboard", JSONObject().put("text", text.take(100000))))
    }
}
