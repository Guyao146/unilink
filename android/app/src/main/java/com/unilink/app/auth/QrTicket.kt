package com.unilink.app.auth

import org.json.JSONObject

/**
 * 二维码解析与授权 URL 构造 —— 纯逻辑，无任何 Android 依赖。
 *
 * 刻意从 [AuthClient] 里拆出来：这两块是安全判定的核心
 *  （拒绝可疑地址、强制 PKCE 参数齐备），必须能在 JVM 上直接跑单元测试，
 * 不能因为需要 Context / 真机而变成"只能靠人肉验证"。
 */
object QrTicket {

    data class Ticket(val server: String, val ticket: String)

    /** 二维码里的类型标记，防止把别家的二维码当成登录码 */
    private const val TYP = "unilink-login"

    /**
     * 解析二维码文本。返回 null 表示不接受。
     *
     * 只接受本项目自己的格式，且 srv 必须是 https 或私有网段 http ——
     * 否则恶意二维码可以把 authentik 令牌引到攻击者控制的服务器上。
     */
    fun parse(text: String?): Ticket? {
        if (text.isNullOrBlank()) return null
        val o = try {
            JSONObject(text)
        } catch (_: Throwable) {
            return null
        }
        if (o.optString("typ") != TYP) return null
        val srv = o.optString("srv").trim().trimEnd('/')
        val tk = o.optString("tk").trim()
        if (srv.isBlank() || tk.isBlank()) return null
        if (!isAcceptableServer(srv)) return null
        return Ticket(srv, tk)
    }

    /** https 一律放行；http 仅放行环回与 RFC 1918 私有网段 */
    fun isAcceptableServer(url: String): Boolean {
        if (url.startsWith("https://")) return true
        if (!url.startsWith("http://")) return false
        val host = url.removePrefix("http://")
            .substringBefore('/')
            .substringBefore(':')
        // 必须整串匹配。用 startsWith 判断会被 192.168.1.1.evil.com 这类
        // 把私有地址塞进主机名前缀的域名骗过去。
        return host == "localhost" ||
                PRIVATE_HOST.matches(host)
    }

    private val PRIVATE_HOST = Regex(
        """^(?:127\.\d{1,3}\.\d{1,3}\.\d{1,3}""" +
        """|10\.\d{1,3}\.\d{1,3}\.\d{1,3}""" +
        """|192\.168\.\d{1,3}\.\d{1,3}""" +
        """|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})$"""
    )

    /** 构造 authentik 授权页 URL（Authorization Code + PKCE S256） */
    fun buildAuthorizeUrl(
        authorizeUrl: String,
        clientId: String,
        redirectUri: String,
        scopes: String,
        state: String,
        codeChallenge: String
    ): String {
        val q = listOf(
            "client_id" to clientId,
            "redirect_uri" to redirectUri,
            "response_type" to "code",
            "scope" to scopes,
            "state" to state,
            "code_challenge" to codeChallenge,
            "code_challenge_method" to "S256"
        ).joinToString("&") { (k, v) ->
            k + "=" + java.net.URLEncoder.encode(v, "UTF-8")
        }
        return authorizeUrl + (if (authorizeUrl.contains("?")) "&" else "?") + q
    }
}
