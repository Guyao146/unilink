package com.unilink.app.auth

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * 令牌安全存储
 * =============
 * authentik 的 access_token / refresh_token 属于长期凭据，
 * 明文写进 SharedPreferences 在 root 设备或被备份时会泄露，
 * 因此用 **Android Keystore 里的硬件密钥**做 AES-GCM 加密后再落盘。
 *
 * 设计要点：
 *  - 密钥别名固定，`setUserAuthenticationRequired(false)` —— 后台服务需要
 *    在无人操作时刷新令牌，不能要求解锁；
 *  - 密钥不可导出（Keystore 保证），卸载 App 即随之销毁；
 *  - 存储格式 Base64(IV[12] ‖ 密文 ‖ Tag)，与项目里 CryptoBox 保持一致的约定；
 *  - **写入**失败会抛异常（宁可登录不成功，也不明文落盘）；
 *  - **读取**失败静默返回 null（换机恢复备份导致密钥丢失属正常情况），
 *    上层按"未登录"处理，让用户重新登录，而不是崩溃。
 */
class SecureStore(ctx: Context) {

    private val sp = ctx.getSharedPreferences("unilink_auth", Context.MODE_PRIVATE)

    companion object {
        private const val KEY_ALIAS = "unilink_token_key"
        private const val KEYSTORE = "AndroidKeyStore"
        private const val TRANSFORM = "AES/GCM/NoPadding"
        private const val IV_LEN = 12
        private const val TAG_BITS = 128
    }

    private fun secretKey(): SecretKey {
        val ks = KeyStore.getInstance(KEYSTORE).apply { load(null) }
        (ks.getEntry(KEY_ALIAS, null) as? KeyStore.SecretKeyEntry)?.let { return it.secretKey }

        val gen = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, KEYSTORE)
        gen.init(
            KeyGenParameterSpec.Builder(
                KEY_ALIAS,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT
            )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .setUserAuthenticationRequired(false)
                .build()
        )
        return gen.generateKey()
    }

    private fun encrypt(plain: String): String? = try {
        val c = Cipher.getInstance(TRANSFORM)
        c.init(Cipher.ENCRYPT_MODE, secretKey())
        val ct = c.doFinal(plain.toByteArray(Charsets.UTF_8))
        Base64.encodeToString(c.iv + ct, Base64.NO_WRAP)
    } catch (t: Throwable) {
        null
    }

    private fun decrypt(blob: String): String? {
        return try {
            val raw = Base64.decode(blob, Base64.NO_WRAP)
            if (raw.size <= IV_LEN) return null
            val c = Cipher.getInstance(TRANSFORM)
            c.init(
                Cipher.DECRYPT_MODE, secretKey(),
                GCMParameterSpec(TAG_BITS, raw, 0, IV_LEN)
            )
            String(c.doFinal(raw, IV_LEN, raw.size - IV_LEN), Charsets.UTF_8)
        } catch (t: Throwable) {
            null
        }
    }

    /**
     * 敏感值：加密存储。
     * Keystore 不可用时抛异常而非降级为明文 —— 静默明文落盘是更坏的结果，
     * 上层会把失败呈现为"登录未完成"，让用户重试。
     */
    fun putSecret(key: String, value: String?) {
        if (value.isNullOrEmpty()) {
            sp.edit().remove(key).apply()
            return
        }
        val enc = encrypt(value)
            ?: throw IllegalStateException("系统密钥库不可用，无法安全保存登录凭据")
        sp.edit().putString(key, enc).apply()
    }

    fun getSecret(key: String): String? {
        val blob = sp.getString(key, null) ?: return null
        return decrypt(blob)
    }

    /** 非敏感值：明文存储（用户名、过期时间等） */
    fun putPlain(key: String, value: String?) {
        val e = sp.edit()
        if (value == null) e.remove(key) else e.putString(key, value)
        e.apply()
    }

    fun getPlain(key: String, def: String? = null): String? = sp.getString(key, def)

    fun putLong(key: String, value: Long) = sp.edit().putLong(key, value).apply()

    fun getLong(key: String, def: Long = 0L): Long = sp.getLong(key, def)

    fun clearAll() = sp.edit().clear().apply()
}
