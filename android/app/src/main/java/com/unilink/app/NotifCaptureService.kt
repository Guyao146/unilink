package com.unilink.app

import android.app.Notification
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification

/**
 * 状态栏通知捕获服务。
 *
 * 首次使用需在系统设置中授权：
 *   设置 → 通知 → 设备与应用程序的通知使用权（各厂商路径略有差异）
 *   或 App 内点击「授予通知使用权」按钮直达。
 *
 * 授权后，手机状态栏出现的每一条通知都会被转发到电脑。
 */
class NotifCaptureService : NotificationListenerService() {

    override fun onListenerConnected() {
        super.onListenerConnected()
        Hub.listenerBound = true
        Hub.log("✅ 通知读取权限已生效，开始同步状态栏消息")
    }

    override fun onListenerDisconnected() {
        super.onListenerDisconnected()
        Hub.listenerBound = false
        Hub.log("⚠ 通知读取权限已断开，请到系统设置重新开启")
    }

    override fun onNotificationPosted(sbn: StatusBarNotification?) {
        if (sbn == null) return
        if (sbn.packageName == packageName) return          // 不转发自己的通知，防止回环
        if (!Prefs(this).mirrorOut) return                  // 用户关闭了镜像开关
        if (Hub.send == null) return                        // 未连接时不处理

        val ex = sbn.notification?.extras
        val title = ex?.getCharSequence(Notification.EXTRA_TITLE)?.toString()?.trim().orEmpty()

        // 优先取长文本，其次普通文本
        var text = ex?.getCharSequence(Notification.EXTRA_BIG_TEXT)?.toString()?.trim().orEmpty()
        if (text.isEmpty()) {
            text = ex?.getCharSequence(Notification.EXTRA_TEXT)?.toString()?.trim().orEmpty()
        }
        val sub = ex?.getCharSequence(Notification.EXTRA_SUB_TEXT)?.toString()?.trim().orEmpty()

        if (title.isEmpty() && text.isEmpty()) return       // 纯进度条等无声通知，跳过

        val appName = try {
            packageManager.getApplicationLabel(
                packageManager.getApplicationInfo(sbn.packageName, 0)).toString()
        } catch (e: Exception) {
            sbn.packageName ?: "unknown"
        }

        val body = listOf(text, sub).filter { it.isNotEmpty() }.joinToString(" · ")
        Hub.emitNotify(appName, sbn.packageName ?: "", title, body, sbn.postTime,
                       key = sbn.key ?: "")
    }
}
