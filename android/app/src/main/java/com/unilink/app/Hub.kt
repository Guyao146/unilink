package com.unilink.app

import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.UUID

/** 一次“电脑回复手机通知”任务 */
class ReplyTask(
    val rid: String,        // 本次回复请求 ID（PC 用于匹配 ACK）
    val key: String?,       // 原通知的 StatusBarNotification.key
    val pkg: String?,       // 来源应用包名
    val app: String?,       // 来源应用名
    val title: String,      // 原通知标题
    val text: String,       // 要发送的回复内容
    val originId: String?   // 发起回复的设备（PC）ID
)

/**
 * 全局总线：UI 轮询这里拿状态与日志；
 * NotifCaptureService 通过 emitNotify() 把状态栏通知推给 LinkService 的 WebSocket；
 * LinkService 收到回复指令后交给 ReplyAccessibilityService 执行（或走回落方案）。
 */
object Hub {
    @Volatile var send: ((String) -> Boolean)? = null   // 发送原始帧；null 表示未连接
    @Volatile var cryptoEnabled = false                 // 房间当前是否启用端到端加密
    @Volatile var status = "未启动"
    @Volatile var myId = ""
    @Volatile var deviceName = android.os.Build.MODEL ?: "Android"
    @Volatile var listenerBound = false                 // 通知监听服务是否已被系统绑定
    @Volatile var a11y = false                          // 无障碍（自动回复）服务是否在线

    val logs = ArrayDeque<String>()

    private val rates = HashMap<String, Long>()         // 相同内容通知的限流表

    fun log(line: String) {
        val ts = SimpleDateFormat("HH:mm:ss", Locale.US).format(Date())
        synchronized(logs) {
            logs.addLast("[$ts] $line")
            while (logs.size > 400) logs.removeFirst()
        }
    }

    fun submit(env: JSONObject) {
        val s = send
        if (s == null) {
            log("⚠ 尚未连接，发送失败")
            return
        }
        if (s(env.toString())) log("↑ ${env.optString("kind")} 已发送")
        else log("⚠ 发送失败")
    }

    /** 把一条状态栏通知封装成 notify 帧发给电脑（带 600ms 同内容限流） */
    fun emitNotify(appName: String, pkg: String, title: String,
                   body: String, postTime: Long, key: String = "") {
        if (send == null) return
        val k = "$pkg|$title|$body"
        val now = System.currentTimeMillis()
        synchronized(rates) {
            val last = rates[k]
            if (last != null && now - last < 600) return
            rates[k] = now
            if (rates.size > 800) rates.clear()
        }
        val payload = JSONObject()
            .put("app", appName)
            .put("package", pkg)
            .put("title", title)
            .put("body", body)
            .put("time", postTime)
            .put("key", key)          // 供电脑定向回复使用
        submit(Proto.envelope("notify", payload))
    }

    /** 把自动回复的执行结果回报给发起的电脑 */
    fun reportReplyResult(t: ReplyTask, ok: Boolean, msg: String) {
        val payload = JSONObject()
            .put("act", "reply")
            .put("rid", t.rid)
            .put("ok", ok)
            .put("msg", msg)
        submit(Proto.envelope("notify-action-ack", payload, t.originId ?: "all"))
        log(if (ok) "✅ $msg（${t.title}）" else "⚠ 自动回复未完成：$msg")
    }

    /**
     * 向房间内广播本机能力（适配规则表 + 无障碍状态）。
     * PC 端回复弹窗据此显示「微信模式/QQ模式/...」及降级警告。
     * 触发时机：welcome 后、新设备加入时、无障碍服务连接/断开时。
     */
    fun broadcastCapabilities() {
        val s = send ?: return
        val modes = AppRules.export()
        val payload = JSONObject()
            .put("platform", "android")
            .put("a11y", a11y)
            .put("modes", modes)
        s(Proto.envelope("capability", payload).toString())
        log("已同步能力信息（无障碍=${if (a11y) "开" else "关"}，适配 ${modes.length()} 个包名）")
    }
}

/** 应用层协议：信封构造 / 解密 */
object Proto {

    fun envelope(kind: String, payload: JSONObject, to: String = "all"): JSONObject {
        val env = JSONObject()
            .put("type", "msg")
            .put("id", newId())
            .put("ts", System.currentTimeMillis())
            .put("from", JSONObject()
                .put("id", Hub.myId)
                .put("name", Hub.deviceName)
                .put("platform", "android"))
            .put("to", to)
            .put("kind", kind)
        val key = CryptoHolder.key
        if (Hub.cryptoEnabled && key != null) {
            env.put("enc", true)
            env.put("payload_enc",
                CryptoBox.seal(key, payload.toString().toByteArray(Charsets.UTF_8)))
        } else {
            env.put("enc", false).put("payload", payload)
        }
        return env
    }

    /** 返回 null 表示解密失败或无 payload */
    fun decrypt(env: JSONObject): JSONObject? {
        return if (env.optBoolean("enc")) {
            val key = CryptoHolder.key ?: return null
            try {
                JSONObject(String(CryptoBox.open(key, env.optString("payload_enc")),
                    Charsets.UTF_8))
            } catch (t: Throwable) {
                null
            }
        } else {
            env.optJSONObject("payload")
        }
    }

    private fun newId(): String =
        UUID.randomUUID().toString().replace("-", "").take(12)
}
