package com.unilink.app.auth

import okhttp3.Call
import okhttp3.Request
import okhttp3.Response
import java.io.Closeable
import java.io.InterruptedIOException

/** 按页面持有 Call，共享的是 HTTP 客户端，不是取消状态。 */
internal class AuthCalls(private val factory: Call.Factory) : Closeable {
    private val lock = Any()
    private val active = HashSet<Call>()
    private var closed = false

    fun <T> execute(request: Request, read: (Response) -> T): T {
        val call = synchronized(lock) {
            checkOpen()
            factory.newCall(request).also { active.add(it) }
        }
        try {
            return call.execute().use { response ->
                val result = read(response)
                ensureActive()
                result
            }
        } finally {
            synchronized(lock) { active.remove(call) }
        }
    }

    fun ensureActive() = synchronized(lock) { checkOpen() }

    private fun checkOpen() {
        if (closed || Thread.currentThread().isInterrupted) {
            throw InterruptedIOException("Auth request cancelled")
        }
    }

    override fun close() {
        val calls = synchronized(lock) {
            if (closed) return
            closed = true
            active.toList().also { active.clear() }
        }
        calls.forEach { it.cancel() }
    }
}
