package com.unilink.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.ClipData
import android.content.ClipboardManager
import android.content.ContentValues
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.provider.MediaStore
import android.widget.Toast
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.File
import java.util.concurrent.TimeUnit

/**
 * 前台服务：维持与中继服务器的 WebSocket 长连接，
 * 收发消息 / 通知镜像 / 剪贴板 / 文件。
 */
class LinkService : Service() {

    private lateinit var prefs: Prefs
    private var ws: WebSocket? = null
    private var wantRun = false
    private var backoffMs = 1000L
    private var retryCount = 0
    private var reconnectScheduled = false
    private var permWarned = false
    private val ui = Handler(Looper.getMainLooper())
    private val reconnectRunnable = Runnable {
        reconnectScheduled = false
        connect()
    }
    private val incoming = HashMap<String, Incoming>()

    class Incoming(val name: String, val size: Long, val mime: String, val total: Int) {
        val buf = ByteArrayOutputStream()
        val marks = HashSet<Int>()
        var got = 0
    }

    private val http = OkHttpClient.Builder()
        .pingInterval(20, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .build()

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        prefs = Prefs(this)
        ensureChannels()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            wantRun = false
            reconnectScheduled = false
            ui.removeCallbacks(reconnectRunnable)
            Hub.status = "已断开"
            Hub.send = null
            ws?.close(1000, "bye")
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
            return START_NOT_STICKY
        }
        wantRun = true
        // 用户主动点击连接时，重新开始计算失败次数并取消旧的等待任务。
        retryCount = 0
        backoffMs = 1000L
        reconnectScheduled = false
        ui.removeCallbacks(reconnectRunnable)
        Hub.deviceName = prefs.deviceName
        CryptoHolder.ensure(prefs.room, prefs.token)
        startForeground(NOTIF_ID, serviceNotif("UniLink 运行中", "正在连接 ${prefs.server}"))
        connect()
        return START_STICKY
    }

    override fun onDestroy() {
        wantRun = false
        Hub.send = null
        ws?.cancel()
        ui.removeCallbacks(reconnectRunnable)
        super.onDestroy()
    }

    // ================= 连接 =================

    private fun connect() {
        if (!wantRun) return
        val url = prefs.server.trim()
        if (url.isBlank()) {
            Hub.status = "未配置服务器地址"
            return
        }
        Hub.status = "连接中…"
        Hub.log("连接 $url")
        val req = Request.Builder().url(url).build()
        ws = http.newWebSocket(req, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                backoffMs = 1000
                retryCount = 0
                val hello = JSONObject()
                    .put("type", "hello")
                    .put("room", prefs.room)
                    .put("token", prefs.token)
                    .put("name", prefs.deviceName)
                    .put("platform", "android")
                    .put("crypto_cap", true)
                webSocket.send(hello.toString())
                Hub.status = "已连接，等待服务器确认…"
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                try {
                    handle(text)
                } catch (t: Throwable) {
                    Hub.log("处理消息异常: ${t.message}")
                }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                Hub.send = null
                Hub.status = "连接断开"
                Hub.log("连接失败: ${t.message}")
                scheduleReconnect()
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                Hub.send = null
                Hub.status = "连接关闭"
                if (wantRun) scheduleReconnect()
            }
        })
    }

    private fun scheduleReconnect() {
        if (!wantRun || reconnectScheduled) return
        val maxRetries = prefs.retryAttempts
        if (maxRetries > 0 && retryCount >= maxRetries) {
            Hub.status = "连接失败（已停止重试）"
            Hub.log("已达到重试上限（$maxRetries 次），停止连接")
            return
        }
        retryCount++
        reconnectScheduled = true
        Hub.log("将在 ${backoffMs / 1000} 秒后重试（第 $retryCount 次）")
        ui.postDelayed(reconnectRunnable, backoffMs)
        backoffMs = (backoffMs * 2).coerceAtMost(30_000)
    }

    // ================= 帧处理 =================

    private fun handle(text: String) {
        val m = JSONObject(text)
        when (m.optString("type")) {
            "welcome" -> {
                Hub.myId = m.optString("you")
                Hub.cryptoEnabled = m.optBoolean("crypto")
                Hub.status = if (Hub.cryptoEnabled) "✅ 已连接（AES-GCM 加密）"
                             else "⚠ 已连接（明文）"
                val n = m.optJSONArray("peers")?.length() ?: 0
                Hub.log("已加入房间，在线设备 ${n + 1} 台")
                Hub.send = { s -> ws?.send(s) ?: false }
                Hub.broadcastCapabilities()   // 让 PC 拿到适配模式表
            }
            "peer_joined" -> {
                Hub.log("设备上线: " +
                        (m.optJSONObject("peer")?.optString("name") ?: "?"))
                Hub.broadcastCapabilities()   // 新上线的 PC 错过了之前的广播，补发
            }
            "peer_left" -> Hub.log("设备离线: ${m.optString("peer_id")}")
            "crypto_changed" -> {
                Hub.cryptoEnabled = m.optBoolean("crypto")
                Hub.log("加密模式切换: ${if (Hub.cryptoEnabled) "开启" else "关闭"}")
            }
            "error" -> {
                Hub.log("服务器: ${m.optString("message")}")
                Hub.status = "错误：${m.optString("message")}"
            }
            "msg" -> onMsg(m)
        }
    }

    private fun onMsg(env: JSONObject) {
        val from = env.optJSONObject("from")
        if (from?.optString("id") == Hub.myId) return
        // 定向消息只处理发给自己的
        val to = env.optString("to", "all")
        if (to != "all" && to != Hub.myId) return
        val fromName = from?.optString("name") ?: "?"
        val payload = Proto.decrypt(env) ?: run {
            Hub.log("⚠ 一条消息解密失败")
            return
        }
        when (env.optString("kind")) {
            "text" -> {
                val t = payload.optString("text")
                Hub.log("💬 $fromName: $t")
                if (prefs.recvNotify) notifyUser(CH_MSG, "💬 $fromName", t)
            }
            "notify" -> if (prefs.recvNotify) {
                val app = payload.optString("app")
                val t = payload.optString("title")
                val b = payload.optString("body")
                Hub.log("🔔 [$app] $t | ${b.replace("\n", " / ")}")
                notifyUser(CH_MIRROR, "💻 $fromName · $app",
                    if (t.isBlank()) b else "$t\n$b")
            }
            "clipboard" -> if (prefs.recvClip) {
                val t = payload.optString("text")
                val cm = getSystemService(CLIPBOARD_SERVICE) as ClipboardManager
                cm.setPrimaryClip(ClipData.newPlainText("unilink", t))
                Hub.log("📋 已复制 $fromName 的剪贴板（${t.length} 字）")
                ui.post {
                    Toast.makeText(this, "剪贴板已同步（${t.length} 字）",
                        Toast.LENGTH_SHORT).show()
                }
            }
            "file-meta" -> {
                val fid = payload.optString("fid")
                val size = payload.optLong("size")
                if (size > MAX_FILE) {
                    Hub.log("⚠ 文件超过 200MB，放弃接收")
                    return
                }
                incoming[fid] = Incoming(payload.optString("name"), size,
                    payload.optString("mime"), payload.optInt("chunks", 1))
                Hub.log("📥 $fromName 发来文件：${payload.optString("name")} ($size 字节)")
            }
            "file-chunk" -> {
                val f = incoming[payload.optString("fid")] ?: return
                try {
                    val data = android.util.Base64.decode(
                        payload.optString("data"), android.util.Base64.NO_WRAP)
                    f.buf.write(data)
                    f.got++
                    val pct = if (f.total > 0) f.got * 100 / f.total else 0
                    if (pct % 20 == 0 && pct > 0 && f.marks.add(pct)) {
                        Hub.log("   ↓ ${f.name} $pct%")
                    }
                } catch (t: Throwable) {
                    Hub.log("⚠ 分块解码失败")
                }
            }
            "file-end" -> {
                val f = incoming.remove(payload.optString("fid")) ?: return
                val msg = saveFile(f)
                Hub.log("✅ $msg")
                notifyUser(CH_MSG, "文件接收完成", msg)
            }
            "notify-action" -> onReplyAction(payload, from?.optString("id"))
        }
    }

    // ================= 电脑回复手机通知 =================

    /**
     * PC 端对某条手机通知发起回复。
     * 优先走无障碍服务全自动发送；未开启无障碍 / 屏幕锁定 / 服务忙时，
     * 回落为「复制到剪贴板 + 打开来 App」，由用户粘贴发送，并回报结果。
     */
    private fun onReplyAction(p: JSONObject, fromId: String?) {
        if (p.optString("act") != "reply") return
        val text = p.optString("text").trim()
        if (text.isEmpty()) return
        val t = ReplyTask(
            rid = p.optString("rid"),
            key = p.optString("key").ifBlank { null },
            pkg = p.optString("pkg").ifBlank { null },
            app = p.optString("app").ifBlank { null },
            title = p.optString("title"),
            text = text,
            originId = fromId
        )
        val km = getSystemService(KEYGUARD_SERVICE) as android.app.KeyguardManager
        if (km.isKeyguardLocked) {
            fallbackQuickReply(t, "手机屏幕已锁定")
            return
        }
        val svc = ReplyBus.service
        if (svc != null && svc.dispatch(t)) {
            Hub.log("⌨ 正在本机自动回复『${t.title}』…")
        } else {
            fallbackQuickReply(t, null)
        }
    }

    /** 回落方案：复制回复内容并打开来源应用 */
    private fun fallbackQuickReply(t: ReplyTask, why: String?) {
        val cm = getSystemService(CLIPBOARD_SERVICE) as ClipboardManager
        cm.setPrimaryClip(ClipData.newPlainText("unilink-reply", t.text))
        var opened = false
        if (!t.pkg.isNullOrBlank()) {
            val launch = packageManager.getLaunchIntentForPackage(t.pkg!!)
            if (launch != null) {
                launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                try {
                    startActivity(launch)
                    opened = true
                } catch (_: Exception) {
                }
            }
        }
        notifyUser(
            CH_MSG, "快捷回复待发送",
            ("回复内容已复制。" +
                    if (opened) "已打开 ${t.app}，粘贴后点发送。"
                    else "请打开 ${t.app} 粘贴后发送。")
        )
        val prefix = if (why.isNullOrBlank()) "" else "$why；"
        val detail = (prefix + "已复制回复内容" +
                (if (opened) "并打开应用，请粘贴后手动发送" else "，请打开应用粘贴发送"))
        Hub.reportReplyResult(t, false, detail)
    }

    // ================= 文件保存 =================

    private fun saveFile(f: Incoming): String {
        val safe = f.name.substringAfterLast('/').ifBlank { "file" }
        return if (Build.VERSION.SDK_INT >= 29) {
            val cv = ContentValues().apply {
                put(MediaStore.MediaColumns.DISPLAY_NAME, safe)
                put(MediaStore.MediaColumns.MIME_TYPE,
                    f.mime.ifBlank { "application/octet-stream" })
                put(MediaStore.MediaColumns.IS_PENDING, 1)
            }
            val uri = contentResolver.insert(
                MediaStore.Downloads.EXTERNAL_CONTENT_URI, cv)
            if (uri == null) return "保存失败（无法创建文件）"
            contentResolver.openOutputStream(uri)?.use { it.write(f.buf.toByteArray()) }
            cv.clear()
            cv.put(MediaStore.MediaColumns.IS_PENDING, 0)
            contentResolver.update(uri, cv, null, null)
            "已保存到「下载/UniLink」：$safe"
        } else {
            val dir = getExternalFilesDir(android.os.Environment.DIRECTORY_DOWNLOADS)
                ?: filesDir
            val out = File(dir, safe)
            out.writeBytes(f.buf.toByteArray())
            "已保存：${out.absolutePath}"
        }
    }

    // ================= 通知 =================

    private fun ensureChannels() {
        if (Build.VERSION.SDK_INT >= 26) {
            val nm = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
            nm.createNotificationChannel(
                NotificationChannel(CH_SRV, "UniLink 后台服务",
                    NotificationManager.IMPORTANCE_MIN))
            nm.createNotificationChannel(
                NotificationChannel(CH_MSG, "聊天消息",
                    NotificationManager.IMPORTANCE_HIGH))
            nm.createNotificationChannel(
                NotificationChannel(CH_MIRROR, "电脑通知镜像",
                    NotificationManager.IMPORTANCE_DEFAULT))
        }
    }

    private fun serviceNotif(title: String, text: String): Notification {
        val pi = PendingIntent.getActivity(this, 0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        val b = if (Build.VERSION.SDK_INT >= 26)
            Notification.Builder(this, CH_SRV) else Notification.Builder(this)
        return b.setSmallIcon(R.drawable.ic_stat)
            .setContentTitle(title)
            .setContentText(text)
            .setContentIntent(pi)
            .setOngoing(true)
            .build()
    }

    private fun notifyUser(channel: String, title: String, body: String) {
        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS)
            != PackageManager.PERMISSION_GRANTED
        ) {
            if (!permWarned) {
                permWarned = true
                Hub.log("⚠ 未授予通知权限，无法弹出提醒（点 App 内「允许弹出通知」）")
            }
            return
        }
        val pi = PendingIntent.getActivity(this, 0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        val b = if (Build.VERSION.SDK_INT >= 26)
            Notification.Builder(this, channel) else Notification.Builder(this)
        val n = b.setSmallIcon(R.drawable.ic_stat)
            .setContentTitle(title.take(64))
            .setContentText(body.take(120))
            .setStyle(Notification.BigTextStyle().bigText(body.take(2000)))
            .setAutoCancel(true)
            .setContentIntent(pi)
            .setWhen(System.currentTimeMillis())
            .build()
        try {
            (getSystemService(NOTIFICATION_SERVICE) as NotificationManager)
                .notify((System.currentTimeMillis() and 0x7FFFFFFF).toInt(), n)
        } catch (_: Exception) {
        }
    }

    companion object {
        const val ACTION_START = "com.unilink.START"
        const val ACTION_STOP = "com.unilink.STOP"
        private const val NOTIF_ID = 1
        private const val CH_SRV = "srv"
        private const val CH_MSG = "msg"
        private const val CH_MIRROR = "mirror"
        private const val MAX_FILE = 200L * 1024 * 1024
    }
}
