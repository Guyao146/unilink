package com.unilink.app

import android.content.Context

/** 轻量配置存取 */
class Prefs(ctx: Context) {
    private val sp = ctx.getSharedPreferences("unilink", Context.MODE_PRIVATE)

    var server: String
        get() = sp.getString("server", "ws://192.168.1.100:8765/ws") ?: ""
        set(v) = sp.edit().putString("server", v).apply()

    var room: String
        get() = sp.getString("room", "88888888") ?: ""
        set(v) = sp.edit().putString("room", v).apply()

    var token: String
        get() = sp.getString("token", "") ?: ""
        set(v) = sp.edit().putString("token", v).apply()

    var deviceName: String
        get() = sp.getString("device", android.os.Build.MODEL ?: "Android") ?: "Android"
        set(v) = sp.edit().putString("device", v).apply()

    /** 是否把本机状态栏通知镜像给电脑 */
    var mirrorOut: Boolean
        get() = sp.getBoolean("mirror_out", true)
        set(v) = sp.edit().putBoolean("mirror_out", v).apply()

    /** 是否接收电脑发来的消息并弹通知 */
    var recvNotify: Boolean
        get() = sp.getBoolean("recv_notify", true)
        set(v) = sp.edit().putBoolean("recv_notify", v).apply()

    /** 是否自动接收电脑剪贴板 */
    var recvClip: Boolean
        get() = sp.getBoolean("recv_clip", true)
        set(v) = sp.edit().putBoolean("recv_clip", v).apply()

    /** 连接失败后的重试次数；0 表示持续重试。 */
    var retryAttempts: Int
        get() = sp.getInt("retry_attempts", 0).coerceAtLeast(0)
        set(v) = sp.edit().putInt("retry_attempts", v.coerceAtLeast(0)).apply()

    /** 外观模式：system / light / dark */
    var themeMode: String
        get() = sp.getString("theme_mode", "system") ?: "system"
        set(v) = sp.edit().putString("theme_mode", v).apply()
}
