package com.unilink.app.auth

import okhttp3.OkHttpClient
import okhttp3.Request
import org.junit.Assert.*
import org.junit.Test
import java.io.Closeable
import java.io.IOException
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/** 真实本机 socket：不依赖 MockWebServer，验证 cancel 能打断响应头/响应体读取。 */
class AuthCancellationTest {
    private class SlowServer(private val sendHeaders: Boolean) : Closeable {
        private val server = ServerSocket(0, 1, InetAddress.getByName("127.0.0.1"))
        private val release = CountDownLatch(1)
        val ready = CountDownLatch(1)
        @Volatile private var socket: Socket? = null
        val url = "http://127.0.0.1:${server.localPort}/"
        private val worker = Thread({
            try {
                server.accept().use { peer ->
                    socket = peer
                    peer.soTimeout = 5_000
                    val reader = peer.getInputStream().bufferedReader()
                    while (true) {
                        val line = reader.readLine() ?: break
                        if (line.isEmpty()) break
                    }
                    if (sendHeaders) {
                        peer.getOutputStream().apply {
                            write("HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n".toByteArray())
                            flush()
                        }
                    }
                    ready.countDown()
                    release.await(10, TimeUnit.SECONDS)
                }
            } catch (_: IOException) {
                // close() 主动关闭监听/已接受的 socket。
            }
        }, "auth-test-server").apply { isDaemon = true; start() }

        override fun close() {
            release.countDown()
            server.close()
            socket?.close()
            worker.join(3_000)
            assertFalse("Test server leaked", worker.isAlive)
        }
    }

    private fun cancelBlockedRead(sendHeaders: Boolean) {
        val http = OkHttpClient.Builder().readTimeout(20, TimeUnit.SECONDS).build()
        val scope = AuthCalls(http)
        val executor = Executors.newSingleThreadExecutor()
        try {
            SlowServer(sendHeaders).use { server ->
                val readingBody = CountDownLatch(1)
                val result = executor.submit<Boolean> {
                    try {
                        scope.execute(Request.Builder().url(server.url).build()) {
                            readingBody.countDown()
                            it.body!!.string()
                        }
                        false
                    } catch (_: IOException) { true }
                }
                assertTrue(server.ready.await(3, TimeUnit.SECONDS))
                if (sendHeaders) assertTrue(readingBody.await(3, TimeUnit.SECONDS))
                scope.close()
                assertTrue("Cancellation should not wait for read timeout", result.get(3, TimeUnit.SECONDS))
            }
        } finally {
            scope.close()
            executor.shutdownNow()
            assertTrue(executor.awaitTermination(3, TimeUnit.SECONDS))
            http.connectionPool.evictAll()
        }
    }

    @Test fun `cancel interrupts waiting for response headers`() = cancelBlockedRead(false)
    @Test fun `call remains cancellable while reading response body`() = cancelBlockedRead(true)

    @Test
    fun `cancelling one scope does not cancel another on the same client`() {
        val http = OkHttpClient.Builder().readTimeout(20, TimeUnit.SECONDS).build()
        val first = AuthCalls(http)
        val second = AuthCalls(http)
        val executor = Executors.newFixedThreadPool(2)
        try {
            SlowServer(false).use { a ->
                SlowServer(false).use { b ->
                    fun launch(scope: AuthCalls, url: String) = executor.submit<Boolean> {
                        try {
                            scope.execute(Request.Builder().url(url).build()) { it.body!!.string() }
                            false
                        } catch (_: IOException) { true }
                    }
                    val one = launch(first, a.url)
                    val two = launch(second, b.url)
                    assertTrue(a.ready.await(3, TimeUnit.SECONDS))
                    assertTrue(b.ready.await(3, TimeUnit.SECONDS))
                    first.close()
                    assertTrue(one.get(3, TimeUnit.SECONDS))
                    assertFalse("Other page was cancelled", two.isDone)
                    second.close()
                    assertTrue(two.get(3, TimeUnit.SECONDS))
                }
            }
        } finally {
            first.close()
            second.close()
            executor.shutdownNow()
            assertTrue(executor.awaitTermination(3, TimeUnit.SECONDS))
            http.connectionPool.evictAll()
        }
    }
}
