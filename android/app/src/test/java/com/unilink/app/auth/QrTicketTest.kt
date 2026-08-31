package com.unilink.app.auth

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 二维码解析与授权 URL 的单元测试（JVM 本地运行，无需设备）
 *
 * 重点覆盖安全判定：拒绝公网 http、拒绝非本项目的二维码、
 * PKCE 参数必须齐备。这些是"错了不会报错、只会静默降低安全性"的地方。
 *
 * 运行：cd android && ./gradlew testDebugUnitTest
 */
class QrTicketTest {

    private fun qr(srv: String, tk: String = "abc123", typ: String = "unilink-login") =
        JSONObject()
            .put("v", 1)
            .put("typ", typ)
            .put("srv", srv)
            .put("tk", tk)
            .toString()

    // ---------------- 正常解析 ----------------

    @Test
    fun `解析标准 https 二维码`() {
        val t = QrTicket.parse(qr("https://qr.example.com"))
        assertEquals("https://qr.example.com", t?.server)
        assertEquals("abc123", t?.ticket)
    }

    @Test
    fun `服务器地址尾部斜杠被规范化`() {
        assertEquals("https://qr.example.com",
            QrTicket.parse(qr("https://qr.example.com/"))?.server)
    }

    // ---------------- 拒绝不安全地址 ----------------

    @Test
    fun `拒绝公网 http`() {
        assertNull(QrTicket.parse(qr("http://qr.example.com")))
        assertNull(QrTicket.parse(qr("http://8.8.8.8")))
        assertNull(QrTicket.parse(qr("http://172.15.0.1")))   // 刚好在私有段之外
        assertNull(QrTicket.parse(qr("http://172.32.0.1")))
    }

    @Test
    fun `允许私有网段 http 便于内网自建`() {
        assertTrue(QrTicket.isAcceptableServer("http://192.168.1.100:8790"))
        assertTrue(QrTicket.isAcceptableServer("http://10.0.0.5:8790"))
        assertTrue(QrTicket.isAcceptableServer("http://172.16.0.1"))
        assertTrue(QrTicket.isAcceptableServer("http://172.31.255.254"))
        assertTrue(QrTicket.isAcceptableServer("http://127.0.0.1:8790"))
        assertTrue(QrTicket.isAcceptableServer("http://localhost:8790"))
    }

    @Test
    fun `拒绝把私有网段塞进主机名的伪装地址`() {
        // 真实主机是 evil.com，不能因为字符串里出现 192.168. 就放行
        assertFalse(QrTicket.isAcceptableServer("http://192.168.1.1.evil.com"))
        assertFalse(QrTicket.isAcceptableServer("http://10.0.0.1.evil.com"))
    }

    @Test
    fun `拒绝非 http 或 https 协议`() {
        assertNull(QrTicket.parse(qr("ftp://qr.example.com")))
        assertNull(QrTicket.parse(qr("javascript:alert(1)")))
        assertNull(QrTicket.parse(qr("file:///etc/passwd")))
    }

    // ---------------- 拒绝格式不符 ----------------

    @Test
    fun `拒绝其它类型的二维码`() {
        assertNull(QrTicket.parse(qr("https://a.test", typ = "wechat-login")))
        assertNull(QrTicket.parse("https://example.com"))        // 纯 URL
        assertNull(QrTicket.parse("这是一段普通文本"))
        assertNull(QrTicket.parse("{不是合法 JSON"))
        assertNull(QrTicket.parse(""))
        assertNull(QrTicket.parse(null))
    }

    @Test
    fun `拒绝缺字段的二维码`() {
        assertNull(QrTicket.parse(qr("https://a.test", tk = "")))
        assertNull(QrTicket.parse(qr("")))
        assertNull(QrTicket.parse(JSONObject().put("typ", "unilink-login").toString()))
    }

    // ---------------- 授权 URL ----------------

    @Test
    fun `授权 URL 含全部必需参数且强制 S256`() {
        val url = QrTicket.buildAuthorizeUrl(
            authorizeUrl = "https://auth.test/application/o/authorize/",
            clientId = "unilink-mobile",
            redirectUri = "unilink://auth/callback",
            scopes = "openid profile email offline_access",
            state = "st-abc",
            codeChallenge = "chal-xyz"
        )
        assertTrue(url.startsWith("https://auth.test/application/o/authorize/?"))
        assertTrue(url.contains("response_type=code"))
        assertTrue(url.contains("client_id=unilink-mobile"))
        assertTrue(url.contains("code_challenge=chal-xyz"))
        assertTrue(url.contains("code_challenge_method=S256"))
        assertTrue(url.contains("state=st-abc"))
        // scope 里的空格与 redirect_uri 里的冒号斜杠必须被转义
        assertTrue(url.contains("scope=openid+profile+email+offline_access"))
        assertTrue(url.contains("redirect_uri=unilink%3A%2F%2Fauth%2Fcallback"))
    }

    @Test
    fun `授权端点已带查询串时用 & 拼接`() {
        val url = QrTicket.buildAuthorizeUrl(
            "https://auth.test/authorize?foo=bar", "cid", "app://cb",
            "openid", "st", "chal")
        assertTrue(url.contains("?foo=bar&client_id=cid"))
    }
}
