package com.unilink.app.auth

import android.content.Context
import okhttp3.FormBody
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * authentik OIDC 客户端 + 扫码登录 API 客户端
 * ===========================================
 * 全部方法都是**阻塞式**的，必须在工作线程调用（沿用项目里已有的
 * OkHttp + 手写线程风格，不引入协程 / AppAuth 等新依赖）。
 *
 * 端点来自扫码服务的 /api/app/config，避免在手机上手输一堆 URL。
 */
class AuthClient(private val ctx: Context) {

    class ApiError(message: String, val status: Int = 0) : IOException(message)

    /** authentik 端点集合 */
    data class Endpoints(
        val authorizeUrl: String,
        val tokenUrl: String,
        val userinfoUrl: String,
        val clientId: String,
        val redirectUri: String,
        val scopes: String
    )

    private val http = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(15, TimeUnit.SECONDS)
        .build()

    private val JSON = "application/json; charset=utf-8".toMediaType()

    // ================= 通用 =================

    private fun getJson(url: String, bearer: String? = null): JSONObject {
        val b = Request.Builder().url(url).get()
        if (bearer != null) b.header("Authorization", "Bearer $bearer")
        http.newCall(b.build()).execute().use { r ->
            val body = r.body?.string().orEmpty()
            if (!r.isSuccessful) throw ApiError(describe(url, r.code, body), r.code)
            return parseJson(url, body)
        }
    }

    private fun postForm(url: String, form: Map<String, String>): JSONObject {
        val fb = FormBody.Builder()
        form.forEach { (k, v) -> fb.add(k, v) }
        http.newCall(Request.Builder().url(url).post(fb.build()).build()).execute().use { r ->
            val body = r.body?.string().orEmpty()
            if (!r.isSuccessful) throw ApiError(describe(url, r.code, body), r.code)
            return parseJson(url, body)
        }
    }

    private fun postJson(url: String, payload: JSONObject): JSONObject {
        val rb = payload.toString().toRequestBody(JSON)
        http.newCall(Request.Builder().url(url).post(rb).build()).execute().use { r ->
            val body = r.body?.string().orEmpty()
            if (!r.isSuccessful) throw ApiError(describe(url, r.code, body), r.code)
            return if (body.isBlank()) JSONObject() else parseJson(url, body)
        }
    }

    /**
     * 解析 JSON。HTTP 200 但内容不是 JSON 的情况很常见 ——
     * 典型是地址写错、打到了别的服务（返回 HTML 首页）或代理的错误页。
     * 这时给出"不是 JSON"的明确提示，比抛一个裸 JSONException 有用得多。
     */
    private fun parseJson(url: String, body: String): JSONObject = try {
        JSONObject(body)
    } catch (t: Throwable) {
        throw ApiError(
            "${shortUrl(url)} 返回的不是 JSON —— 请确认地址指向 UniLink 扫码服务" +
                    "（而不是 authentik 或其它站点）", 0)
    }

    /**
     * 把服务端返回的错误提炼成可定位的信息。
     *
     * 500 之类的错误往往由反向代理或 authentik 返回、响应体是 HTML 而非 JSON，
     * 此时必须带上**请求的 URL 与响应体片段**，否则日志里只剩一句 "HTTP 500"，
     * 根本分不清是打错了地址、代理没通、还是服务端真的崩了。
     */
    private fun describe(url: String, code: Int, body: String): String {
        val msg = try {
            val o = JSONObject(body)
            o.optString("error_description")
                .ifBlank { o.optString("detail") }
                .ifBlank { o.optString("error") }
        } catch (_: Throwable) {
            ""
        }
        if (msg.isNotBlank()) return "$msg (HTTP $code)"

        // 非 JSON 响应：截一段正文，去掉标签与空白，便于识别是谁在应答
        val snippet = body.replace(Regex("<[^>]*>"), " ")
            .replace(Regex("\\s+"), " ")
            .trim()
            .take(120)
        val where = shortUrl(url)
        return if (snippet.isBlank()) "HTTP $code（$where 无响应内容）"
               else "HTTP $code（$where）：$snippet"
    }

    /** 只保留 host + path，避免把查询串里的敏感参数写进日志 */
    private fun shortUrl(url: String): String =
        url.substringBefore('?').removePrefix("https://").removePrefix("http://")

    // ================= 配置发现 =================

    fun fetchEndpoints(qrServer: String): Endpoints {
        val o = getJson("${qrServer.trimEnd('/')}/api/app/config")
        // 缺字段说明打到的不是本服务（或版本不匹配）—— 明确报出来，
        // 而不是抛一个只写着字段名的 JSONException
        fun need(k: String): String {
            val v = o.optString(k)
            if (v.isBlank()) {
                throw ApiError("${shortUrl(qrServer)} 的响应缺少 $k 字段，" +
                        "该地址可能不是 UniLink 扫码服务", 0)
            }
            return v
        }
        return Endpoints(
            authorizeUrl = need("authorize_url"),
            tokenUrl = need("token_url"),
            userinfoUrl = need("userinfo_url"),
            clientId = need("client_id"),
            redirectUri = need("redirect_uri"),
            scopes = o.optString("scopes", "openid profile email offline_access")
        )
    }

    // ================= App 自身登录 authentik =================

    /** 构造浏览器要打开的授权 URL（Authorization Code + PKCE） */
    fun buildAuthorizeUrl(ep: Endpoints, pkce: AuthSession.Pkce): String =
        QrTicket.buildAuthorizeUrl(
            authorizeUrl = ep.authorizeUrl,
            clientId = ep.clientId,
            redirectUri = ep.redirectUri,
            scopes = ep.scopes,
            state = pkce.state,
            codeChallenge = pkce.challenge
        )

    /** 用授权码换令牌并写入本地会话；返回 userinfo */
    fun exchangeCode(ep: Endpoints, code: String, verifier: String): JSONObject {
        val tok = postForm(ep.tokenUrl, mapOf(
            "grant_type" to "authorization_code",
            "code" to code,
            "redirect_uri" to ep.redirectUri,
            "client_id" to ep.clientId,
            "code_verifier" to verifier
        ))
        AuthSession.saveTokens(ctx, tok)
        val info = getJson(ep.userinfoUrl, tok.optString("access_token"))
        AuthSession.saveIdentity(ctx, info)
        return info
    }

    /** 用 refresh_token 静默续期；返回 false 表示需要重新登录 */
    fun refresh(ep: Endpoints): Boolean {
        val rt = AuthSession.refreshToken(ctx) ?: return false
        return try {
            val tok = postForm(ep.tokenUrl, mapOf(
                "grant_type" to "refresh_token",
                "refresh_token" to rt,
                "client_id" to ep.clientId
            ))
            AuthSession.saveTokens(ctx, tok)
            true
        } catch (e: ApiError) {
            // 400/401 表示 refresh_token 已失效（被吊销或过期）→ 清理本地会话
            if (e.status == 400 || e.status == 401) AuthSession.logout(ctx)
            false
        }
    }

    /** 取一个当前可用的 access_token，必要时自动刷新 */
    fun validAccessToken(ep: Endpoints): String? {
        if (!AuthSession.expired(ctx)) return AuthSession.accessToken(ctx)
        if (!refresh(ep)) return null
        return AuthSession.accessToken(ctx)
    }

    // ================= 扫码登录 =================

    /** 二维码内容解析结果（类型别名，实现见 [QrTicket]） */
    fun parseQr(text: String): QrTicket.Ticket? = QrTicket.parse(text)

    /** 通知服务端"已扫到码"，取回将要登录的目标应用名 */
    fun preview(t: QrTicket.Ticket, deviceName: String): JSONObject =
        postJson("${t.server}/api/scan/preview",
            JSONObject().put("ticket", t.ticket).put("device", deviceName))

    /** 确认登录：把本机 authentik 令牌交给扫码服务换取授权码 */
    fun approve(t: QrTicket.Ticket, accessToken: String): JSONObject =
        postJson("${t.server}/api/scan/approve",
            JSONObject().put("ticket", t.ticket).put("access_token", accessToken))

    fun deny(t: QrTicket.Ticket): JSONObject =
        postJson("${t.server}/api/scan/deny", JSONObject().put("ticket", t.ticket))
}
