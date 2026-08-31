package com.unilink.app.auth

import android.content.Context
import android.util.Base64
import org.json.JSONObject
import java.security.MessageDigest
import java.security.SecureRandom

/**
 * authentik 登录会话（本地持久化）
 * ================================
 * 保存 App 自身登录 authentik 后拿到的令牌与用户信息。
 * 令牌经 [SecureStore] 用 Keystore 硬件密钥加密后落盘，重启 App 仍然登录。
 *
 * 字段说明：
 *  - accessToken   调 userinfo / 扫码确认时出示的凭据（短期，默认几分钟到 1 小时）
 *  - refreshToken  access 过期后静默续期用（authentik 需授予 offline_access scope）
 *  - expiresAt     accessToken 过期时刻（毫秒），提前 60s 视为过期以规避时钟漂移
 */
object AuthSession {

    private const val K_ACCESS = "access_token"
    private const val K_REFRESH = "refresh_token"
    private const val K_EXPIRES = "expires_at"
    private const val K_SUB = "sub"
    private const val K_USER = "username"
    private const val K_EMAIL = "email"
    private const val K_NAME = "name"
    private const val K_SERVER = "qr_server"

    /** accessToken 剩余寿命小于此值时视为需要刷新 */
    private const val SKEW_MS = 60_000L

    @Volatile private var store: SecureStore? = null

    private fun s(ctx: Context): SecureStore =
        store ?: synchronized(this) {
            store ?: SecureStore(ctx.applicationContext).also { store = it }
        }

    // ---------------- 读 ----------------

    val loggedIn: Boolean get() = cachedSub != null

    @Volatile var cachedSub: String? = null
        private set
    @Volatile var cachedUser: String = ""
        private set

    fun load(ctx: Context) {
        val st = s(ctx)
        // 空串要归一成 null，否则 loggedIn 会把"存过但为空"误判成已登录
        cachedSub = st.getPlain(K_SUB, "").orEmpty().ifBlank { null }
        cachedUser = st.getPlain(K_USER, "").orEmpty()
    }

    fun accessToken(ctx: Context): String? = s(ctx).getSecret(K_ACCESS)

    fun refreshToken(ctx: Context): String? = s(ctx).getSecret(K_REFRESH)

    fun expired(ctx: Context): Boolean =
        System.currentTimeMillis() + SKEW_MS >= s(ctx).getLong(K_EXPIRES, 0L)

    fun displayName(ctx: Context): String {
        val st = s(ctx)
        return st.getPlain(K_NAME, "").orEmpty().ifBlank {
            st.getPlain(K_USER, "").orEmpty().ifBlank {
                st.getPlain(K_EMAIL, "").orEmpty()
            }
        }
    }

    /** 扫码登录服务地址（由二维码携带，或用户预先填写） */
    fun qrServer(ctx: Context): String = s(ctx).getPlain(K_SERVER, "").orEmpty()

    fun setQrServer(ctx: Context, url: String) = s(ctx).putPlain(K_SERVER, url.trimEnd('/'))

    // ---------------- 写 ----------------

    /** 保存令牌端点返回的结果。access_token 缺失视为失败。 */
    fun saveTokens(ctx: Context, tokenResp: JSONObject) {
        val at = tokenResp.optString("access_token").trim()
        if (at.isBlank()) {
            throw IllegalStateException("authentik 未返回 access_token")
        }
        val st = s(ctx)
        st.putSecret(K_ACCESS, at)
        // authentik 刷新时不一定回传新的 refresh_token，缺失时保留旧值
        val rt = tokenResp.optString("refresh_token")
        if (rt.isNotBlank()) st.putSecret(K_REFRESH, rt)
        val ttl = tokenResp.optLong("expires_in", 300L)
        st.putLong(K_EXPIRES, System.currentTimeMillis() + ttl * 1000L)
    }

    /** 保存 userinfo 返回的身份。sub 为空视为失败，不写入登录态。 */
    fun saveIdentity(ctx: Context, info: JSONObject) {
        val sub = info.optString("sub").trim()
        if (sub.isBlank()) {
            throw IllegalStateException("authentik 未返回 sub，无法确定身份")
        }
        val st = s(ctx)
        st.putPlain(K_SUB, sub)
        st.putPlain(K_USER, info.optString("preferred_username"))
        st.putPlain(K_EMAIL, info.optString("email"))
        st.putPlain(K_NAME, info.optString("name"))
        cachedSub = sub
        cachedUser = info.optString("preferred_username")
            .ifBlank { info.optString("email") }
    }

    fun logout(ctx: Context) {
        val server = qrServer(ctx)      // 服务器地址不属于凭据，保留以便下次登录
        s(ctx).clearAll()
        cachedSub = null
        cachedUser = ""
        if (server.isNotBlank()) setQrServer(ctx, server)
    }

    // ---------------- PKCE ----------------

    /**
     * 生成 PKCE 参数。手机 App 是 public client（无法保管 client_secret），
     * 必须用 PKCE 防止授权码被同设备恶意应用通过抢注 scheme 截获。
     */
    class Pkce(val verifier: String, val challenge: String, val state: String)

    fun newPkce(): Pkce {
        val rnd = SecureRandom()
        fun rand(n: Int): String {
            val b = ByteArray(n)
            rnd.nextBytes(b)
            return Base64.encodeToString(b, Base64.URL_SAFE or Base64.NO_PADDING or Base64.NO_WRAP)
        }
        val verifier = rand(48)
        val digest = MessageDigest.getInstance("SHA-256")
            .digest(verifier.toByteArray(Charsets.US_ASCII))
        val challenge = Base64.encodeToString(
            digest, Base64.URL_SAFE or Base64.NO_PADDING or Base64.NO_WRAP)
        return Pkce(verifier, challenge, rand(16))
    }
}
