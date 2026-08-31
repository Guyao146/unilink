package com.unilink.app

import android.util.Base64
import java.security.SecureRandom
import javax.crypto.Cipher
import javax.crypto.SecretKeyFactory
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.PBEKeySpec
import javax.crypto.spec.SecretKeySpec

/**
 * 端到端加密盒（与 PC 端 cryptobox.py 算法完全一致）
 *
 * 密钥派生: PBKDF2WithHmacSHA256(口令=令牌, 盐="unilink|"+房间码, 120000 次, 256 位)
 * 加密    : AES/GCM/NoPadding，随机 12 字节 IV，128 位 Tag
 * 数据格式: Base64( IV[12] || 密文 || Tag )
 */
object CryptoBox {

    fun deriveKey(room: String, token: String): ByteArray {
        val salt = ("unilink|" + room).toByteArray(Charsets.UTF_8)
        val spec = PBEKeySpec((token ?: "").toCharArray(), salt, 120_000, 256)
        return SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256")
            .generateSecret(spec).encoded
    }

    fun seal(key: ByteArray, plain: ByteArray): String {
        val iv = ByteArray(12).also { SecureRandom().nextBytes(it) }
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, SecretKeySpec(key, "AES"), GCMParameterSpec(128, iv))
        val ct = cipher.doFinal(plain)
        return Base64.encodeToString(iv + ct, Base64.NO_WRAP)
    }

    fun open(key: ByteArray, blob: String): ByteArray {
        val raw = Base64.decode(blob, Base64.NO_WRAP)
        require(raw.size > 12) { "密文过短" }
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, SecretKeySpec(key, "AES"),
            GCMParameterSpec(128, raw.copyOfRange(0, 12)))
        return cipher.doFinal(raw.copyOfRange(12, raw.size))
    }
}

/** 会话级密钥缓存 */
object CryptoHolder {
    @Volatile
    var key: ByteArray? = null
        private set

    fun ensure(room: String, token: String) {
        if (key == null) {
            key = try {
                CryptoBox.deriveKey(room, token.ifBlank { "unilink" })
            } catch (t: Throwable) {
                null
            }
        }
    }

    fun reset() { key = null }
}
