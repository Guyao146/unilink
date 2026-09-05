package com.unilink.app

import android.Manifest
import android.content.ClipboardManager
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.view.View
import android.view.ViewGroup
import android.widget.EditText
import android.widget.ImageView
import android.widget.Switch
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.app.AppCompatDelegate
import com.unilink.app.auth.AuthSession
import com.unilink.app.auth.LoginActivity
import com.unilink.app.auth.QrTicket
import com.unilink.app.auth.ScanLoginActivity
import com.unilink.app.ui.LogView
import com.unilink.app.ui.Motion
import com.unilink.app.ui.SwipePageHost
import org.json.JSONObject

class MainActivity : AppCompatActivity() {

    companion object {
        private const val THEME_SYSTEM = "system"
        private const val THEME_LIGHT = "light"
        private const val THEME_DARK = "dark"

        /** 在 Activity 创建前应用已保存的外观，确保首帧就是正确主题。 */
        private fun applyTheme(mode: String) {
            AppCompatDelegate.setDefaultNightMode(when (mode) {
                THEME_LIGHT -> AppCompatDelegate.MODE_NIGHT_NO
                THEME_DARK -> AppCompatDelegate.MODE_NIGHT_YES
                else -> AppCompatDelegate.MODE_NIGHT_FOLLOW_SYSTEM
            })
        }
    }

    private lateinit var prefs: Prefs
    private lateinit var etServer: EditText
    private lateinit var etRoom: EditText
    private lateinit var etToken: EditText
    private lateinit var etName: EditText
    private lateinit var etRetryCount: EditText
    private lateinit var swMirror: Switch
    private lateinit var swRecvNotify: Switch
    private lateinit var swRecvClip: Switch
    private lateinit var etQrServer: EditText

    // 首页状态区
    private lateinit var tvStatus: TextView
    private lateinit var tvStatusHint: TextView
    private lateinit var tvIsland: TextView
    private lateinit var dotStatus: View

    // 连接页
    private lateinit var tvAuth: TextView

    // 同步页
    private lateinit var tvLog: LogView
    private lateinit var tvVersion: TextView

    // 设置页：每项权限一个圆点 + 一行说明
    private lateinit var tvListener: TextView
    private lateinit var tvA11y: TextView
    private lateinit var dotNotifAccess: View
    private lateinit var dotNotifPost: View
    private lateinit var dotA11y: View

    // 设置页外观选择
    private lateinit var appearanceSystem: TextView
    private lateinit var appearanceLight: TextView
    private lateinit var appearanceDark: TextView

    /** 四个页面与对应的 Dock 项，索引一一对应 */
    private lateinit var pages: List<View>
    private lateinit var tabs: List<ViewGroup>
    private lateinit var pageHost: SwipePageHost
    private lateinit var dock: View
    private lateinit var dockIndicator: View
    private var currentTab = 0
    private var showingAbout = false

    private val handler = Handler(Looper.getMainLooper())

    private val tick = object : Runnable {
        override fun run() {
            renderStatus()
            renderPermissions()
            refreshAuthLine()
            renderLog()
            handler.postDelayed(this, 600)
        }
    }

    // ================= 渲染 =================

    /**
     * 首页状态区：圆点 + 大字 + 一句说明。
     *
     * 状态从"一行小字"升级成整页主角 —— 用户打开 App 最想知道的
     * 就是"现在通不通"，这个信息值得占据最大的字号。
     */
    private fun renderStatus() {
        val s = Hub.status
        if (tvStatus.text != s) tvStatus.text = s

        val (colorRes, hint) = when {
            s.contains("已连接") || s.contains("加密") ->
                R.color.ac_success to "已与电脑互通。通知、剪贴板与文件可双向传输。"
            s.contains("连接中") || s.contains("等待") ->
                R.color.ac_warn to "正在建立连接…"
            s.contains("错误") || s.contains("失败") ->
                R.color.ac_danger to "连接出错。检查服务器地址与令牌，详情见「同步」页的日志。"
            s.contains("断开") ->
                R.color.ink_tertiary to "已断开。点下方按钮重新连接。"
            else ->
                R.color.ink_tertiary to getString(R.string.status_idle_hint)
        }
        val color = getColor(colorRes)
        Motion.tintText(tvStatus, color)
        tintDot(dotStatus, color)
        if (tvStatusHint.text != hint) tvStatusHint.text = hint

        val island = when {
            s.contains("已连接") || s.contains("加密") -> R.string.island_connected
            s.contains("连接中") || s.contains("等待") -> R.string.island_connecting
            s.contains("错误") || s.contains("失败") -> R.string.island_error
            else -> R.string.island_idle
        }
        if (tvIsland.text.toString() != getString(island)) tvIsland.setText(island)
        Motion.tintText(tvIsland, color)
    }

    /** 给圆点着色。圆点是共享 drawable，必须 mutate 后再改，否则会串色 */
    private fun tintDot(dot: View, color: Int) {
        val bg = dot.background?.mutate() ?: return
        bg.setTint(color)
        dot.background = bg
    }

    /** 设置页的权限行：圆点表示状态，文字只写结论不写指引 */
    private fun renderPermissions() {
        val granted = getColor(R.color.ac_success)
        val missing = getColor(R.color.ac_danger)
        val optional = getColor(R.color.ink_tertiary)

        val notifOk = notifListenerEnabled()
        tintDot(dotNotifAccess, if (notifOk) granted else missing)
        val listenerText = when {
            notifOk && Hub.listenerBound -> "已授予，正在同步"
            notifOk -> "已授予"
            else -> "未授予，无法同步通知"
        }
        if (tvListener.text != listenerText) tvListener.text = listenerText

        val postOk = Build.VERSION.SDK_INT < 33 ||
                checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) ==
                PackageManager.PERMISSION_GRANTED
        tintDot(dotNotifPost, if (postOk) granted else missing)

        tintDot(dotA11y, if (Hub.a11y) granted else optional)
        val a11yText = if (Hub.a11y) "已开启，电脑回复可自动发送"
                       else getString(R.string.perm_a11y_hint)
        if (tvA11y.text != a11yText) tvA11y.text = a11yText
    }

    private fun renderLog() {
        val text = synchronized(Hub.logs) {
            Hub.logs.toList().joinToString("\n")
        }
        val shown = text.ifBlank { getString(R.string.log_empty) }
        tvLog.updateLogText(shown)
    }

    private fun clearLogs() {
        synchronized(Hub.logs) {
            Hub.logs.clear()
        }
        renderLog()
    }


    override fun onCreate(savedInstanceState: Bundle?) {
        applyTheme(Prefs(this).themeMode)
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        prefs = Prefs(this)

        bindViews()
        Hub.logObserver = { handler.post { renderLog() } }
        loadPrefs()
        bindActions()
        setupDock()

        handler.post(tick)
    }

    private fun bindViews() {
        etServer = findViewById(R.id.etServer)
        etRoom = findViewById(R.id.etRoom)
        etToken = findViewById(R.id.etToken)
        etName = findViewById(R.id.etName)
        etRetryCount = findViewById(R.id.etRetryCount)
        etQrServer = findViewById(R.id.etQrServer)
        swMirror = findViewById(R.id.swMirror)
        swRecvNotify = findViewById(R.id.swRecvNotify)
        swRecvClip = findViewById(R.id.swRecvClip)

        tvStatus = findViewById(R.id.tvStatus)
        tvStatusHint = findViewById(R.id.tvStatusHint)
        tvIsland = findViewById(R.id.tvIsland)
        dotStatus = findViewById(R.id.dotStatus)

        tvAuth = findViewById(R.id.tvAuth)
        tvLog = findViewById(R.id.tvLog)
        tvVersion = findViewById(R.id.tvVersion)
        tvVersion.text = BuildConfig.VERSION_NAME

        tvListener = findViewById(R.id.tvListener)
        tvA11y = findViewById(R.id.tvA11y)
        dotNotifAccess = findViewById(R.id.dotNotifAccess)
        dotNotifPost = findViewById(R.id.dotNotifPost)
        dotA11y = findViewById(R.id.dotA11y)

        appearanceSystem = findViewById(R.id.appearanceSystem)
        appearanceLight = findViewById(R.id.appearanceLight)
        appearanceDark = findViewById(R.id.appearanceDark)

        pages = listOf(
            findViewById(R.id.pageHome),
            findViewById(R.id.pageLink),
            findViewById(R.id.pageSync),
            findViewById(R.id.pageSettings),
            findViewById(R.id.pageAbout)
        )
        tabs = listOf(
            findViewById(R.id.tabHome),
            findViewById(R.id.tabLink),
            findViewById(R.id.tabSync),
            findViewById(R.id.tabSettings)
        )
        pageHost = findViewById(R.id.pageHost)
        pageHost.swipePageCount = tabs.size
        pageHost.onSwipePage = { index ->
            // 回调已经发生在动画结束时；只同步 Dock，不再次触发页面动画
            selectTab(index, animate = false, fromSwipe = true)
        }
        dock = findViewById(R.id.dock)
        dockIndicator = findViewById(R.id.dockIndicator)
        dockIndicator.isSelected = true
    }

    private fun loadPrefs() {
        etServer.setText(prefs.server)
        etRoom.setText(prefs.room)
        etToken.setText(prefs.token)
        etName.setText(prefs.deviceName)
        swMirror.isChecked = prefs.mirrorOut
        swRecvNotify.isChecked = prefs.recvNotify
        swRecvClip.isChecked = prefs.recvClip
        etRetryCount.setText(prefs.retryAttempts.toString())
        refreshAppearance()

        AuthSession.load(this)
        etQrServer.setText(AuthSession.qrServer(this))
        refreshAuthLine(force = true)
    }

    private fun bindActions() {
        click(R.id.btnStart) { save(); start() }
        click(R.id.btnStop) {
            startService(Intent(this, LinkService::class.java)
                .setAction(LinkService.ACTION_STOP))
        }
        click(R.id.btnAccess) { openSettings(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS) }
        click(R.id.btnPerm) { reqNotifPerm() }
        click(R.id.btnA11y) {
            openSettings(Settings.ACTION_ACCESSIBILITY_SETTINGS)
            toast("在列表中找到 UniLink → 开启「UniLink 自动回复」")
        }
        click(R.id.btnShareClip) { shareClip() }
        click(R.id.btnClear) { clearLogs() }
        click(R.id.btnLogin) { doLogin() }
        click(R.id.btnScanLogin) { doScanLogin() }
        click(R.id.btnLogout) { doLogout() }
        click(R.id.btnAboutBottom) { showAbout() }
        click(R.id.btnAboutBack) { hideAbout() }
        click(R.id.btnWebsite) { openWebsite() }

        click(R.id.appearanceSystem) { setAppearance(THEME_SYSTEM) }
        click(R.id.appearanceLight) { setAppearance(THEME_LIGHT) }
        click(R.id.appearanceDark) { setAppearance(THEME_DARK) }
    }

    /** 绑定点击并附带按压光效，省掉每处重复两行 */
    private inline fun click(id: Int, crossinline action: () -> Unit) {
        val v = findViewById<View>(id)
        v.setOnClickListener { action() }
        Motion.press(v)
    }

    private fun openSettings(action: String) {
        try {
            startActivity(Intent(action))
        } catch (t: Throwable) {
            toast("无法打开系统设置页，请手动前往")
        }
    }

    private fun toast(s: String) = Toast.makeText(this, s, Toast.LENGTH_SHORT).show()

    /** 外观选择使用整行选中态，避免原生 RadioButton 把页面拉回 Material 默认风格。 */
    private fun refreshAppearance() {
        val selected = prefs.themeMode
        val rows = listOf(
            THEME_SYSTEM to appearanceSystem,
            THEME_LIGHT to appearanceLight,
            THEME_DARK to appearanceDark
        )
        rows.forEach { (mode, row) ->
            row.isSelected = mode == selected
            row.setTextColor(getColor(if (row.isSelected) R.color.ac_primary else R.color.ink_primary))
            row.contentDescription = if (row.isSelected) {
                "${row.text}，已选择"
            } else {
                "${row.text}，未选择"
            }
        }
    }

    private fun setAppearance(mode: String) {
        if (prefs.themeMode == mode) return
        prefs.themeMode = mode
        AppCompatDelegate.setDefaultNightMode(when (mode) {
            THEME_LIGHT -> AppCompatDelegate.MODE_NIGHT_NO
            THEME_DARK -> AppCompatDelegate.MODE_NIGHT_YES
            else -> AppCompatDelegate.MODE_NIGHT_FOLLOW_SYSTEM
        })
    }

    // ================= Dock 导航 =================

    /**
     * Dock 切页。
     *
     * 页面用可见性切换而非 ViewPager：只有 4 页且状态需要保留
     * （输入框内容、日志滚动位置），Fragment 生命周期反而是负担。
     */
    private fun setupDock() {
        tabs.forEachIndexed { i, tab ->
            tab.setOnClickListener { selectTab(i) }
            Motion.press(tab)
        }
        selectTab(0, animate = false)
    }

    private fun selectTab(index: Int, animate: Boolean = true, fromSwipe: Boolean = false) {
        if (index !in tabs.indices) return
        currentTab = index
        showingAbout = false
        dock.visibility = View.VISIBLE

        if (!fromSwipe) {
            pageHost.showPage(index, animate = animate)
        }

        updateDockSelection(index)
        moveDockIndicator(index, animate)
    }

    private fun updateDockSelection(index: Int) {
        tabs.forEachIndexed { i, tab ->
            val on = i == index
            tab.isSelected = on
            // 选中项提亮，未选中项退到次级色，不使用沉重的实色块
            val color = getColor(if (on) R.color.ac_primary else R.color.ink_tertiary)
            for (j in 0 until tab.childCount) {
                when (val child = tab.getChildAt(j)) {
                    is ImageView -> child.setColorFilter(color)
                    is TextView -> child.setTextColor(color)
                }
            }
        }
    }

    /** Dock 选中底板在项目间连续滑动，而非突兀地逐项切换。 */
    private fun moveDockIndicator(index: Int, animate: Boolean) {
        dock.post {
            val available = dock.width - dock.paddingStart - dock.paddingEnd
            if (available <= 0) return@post
            val itemWidth = available / tabs.size
            dockIndicator.layoutParams = dockIndicator.layoutParams.apply { width = itemWidth }
            // FrameLayout 已将子视图放在 paddingStart 内；再加一次会令高亮右偏。
            val target = (itemWidth * index).toFloat()
            if (animate) {
                dockIndicator.animate().translationX(target)
                    .setDuration(320L).setInterpolator(Motion.SNAPPY).start()
            } else {
                dockIndicator.translationX = target
            }
        }
    }

    private fun showAbout() {
        showingAbout = true
        pages.forEachIndexed { i, page ->
            page.visibility = if (i == 4) View.VISIBLE else View.GONE
            page.translationX = 0f
            page.scaleX = 1f
            page.scaleY = 1f
            page.alpha = if (i == 4) 0f else 1f
        }
        dock.animate().alpha(0f).setDuration(160L).withEndAction {
            dock.visibility = View.GONE
            dock.alpha = 1f
        }.start()
        val about = pages[4]
        // 关于页与主页面保持同一平面，只做柔和淡化
        about.alpha = 0f
        about.translationX = 0f
        about.animate().alpha(1f).setDuration(260L)
            .setInterpolator(Motion.SMOOTH).start()
    }

    private fun hideAbout() = selectTab(3)

    private fun openWebsite() {
        try {
            startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("https://mcylyr.cn")))
        } catch (_: Throwable) {
            toast("无法打开官网，请检查浏览器是否可用")
        }
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
        Hub.logObserver = null
        super.onDestroy()
        handler.removeCallbacksAndMessages(null)
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        if (showingAbout) hideAbout() else super.onBackPressed()
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
        prefs.retryAttempts = etRetryCount.text.toString().toIntOrNull()?.coerceAtLeast(0) ?: 0
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
            "已登录：$name"
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
