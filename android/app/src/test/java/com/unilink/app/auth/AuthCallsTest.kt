package com.unilink.app.auth

import okhttp3.Call
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Request
import okhttp3.Response
import okhttp3.ResponseBody
import okio.Buffer
import okio.ForwardingSource
import okio.buffer
import org.junit.Assert.*
import org.junit.Test
import java.io.IOException
import java.io.InterruptedIOException
import java.util.concurrent.atomic.AtomicInteger

class AuthCallsTest {
    private val request = Request.Builder().url("http://localhost/test").build()

    private class Body : ResponseBody() {
        var closed = false
        private val input = object : ForwardingSource(Buffer().writeUtf8("{}")) {
            override fun close() { closed = true; super.close() }
        }.buffer()
        override fun contentType() = null
        override fun contentLength() = 2L
        override fun source() = input
    }

    @Test
    fun `responses close on success and parse failure and completed calls are unregistered`() {
        for (failParsing in listOf(false, true)) {
            val body = Body()
            val http = OkHttpClient.Builder().addInterceptor { chain ->
                Response.Builder().request(chain.request()).protocol(Protocol.HTTP_1_1)
                    .code(200).message("OK").body(body).build()
            }.build()
            val created = mutableListOf<Call>()
            val scope = AuthCalls(object : Call.Factory {
                override fun newCall(request: Request): Call =
                    http.newCall(request).also { created.add(it) }
            })
            try {
                scope.execute(request) {
                    if (failParsing) throw IOException("invalid JSON")
                    assertEquals("{}", it.body!!.string())
                }
                assertFalse(failParsing)
            } catch (e: IOException) {
                assertTrue(failParsing)
                assertEquals("invalid JSON", e.message)
            } finally {
                scope.close()
            }
            assertTrue(body.closed)
            assertEquals(1, created.size)
            assertFalse("Completed call retained by scope", created.single().isCanceled())
        }
    }

    @Test
    fun `closed scope refuses new calls before touching the shared client`() {
        val count = AtomicInteger()
        val http = OkHttpClient()
        val scope = AuthCalls(object : Call.Factory {
            override fun newCall(request: Request): Call {
                count.incrementAndGet()
                return http.newCall(request)
            }
        })
        scope.close()
        scope.close()
        try {
            scope.execute(request) { fail("must not execute") }
            fail("must throw cancellation")
        } catch (_: InterruptedIOException) {
            assertEquals(0, count.get())
        }
    }
}
