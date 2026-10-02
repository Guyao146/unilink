package com.unilink.app.auth

import java.io.Closeable
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.RejectedExecutionException
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

/** 一个页面一个串行任务域；不依赖 Android，便于在 JVM 验证取消竞态。 */
internal class AuthTasks(
    private val cancelRequests: () -> Unit,
    private val post: (Runnable) -> Unit,
    private val clearCallbacks: () -> Unit,
    private val executor: ThreadPoolExecutor = newExecutor()
) : Closeable {
    private val lock = Any()
    private var closed = false
    private var busy = false

    companion object {
        private val sequence = AtomicInteger()

        internal fun newExecutor(idleMs: Long = 10_000): ThreadPoolExecutor =
            ThreadPoolExecutor(1, 1, idleMs, TimeUnit.MILLISECONDS,
                ArrayBlockingQueue<Runnable>(1), { task ->
                    Thread(task, "unilink-auth-${sequence.incrementAndGet()}").apply {
                        isDaemon = true
                    }
                }).apply { allowCoreThreadTimeOut(true) }
    }

    /** busy 包括结果等待主线程消费的时间，防止同一操作重复提交。 */
    fun <T> submit(work: () -> T, success: (T) -> Unit, failure: (Exception) -> Unit): Boolean =
        synchronized(lock) {
            if (closed || busy) return false
            busy = true
            try {
                executor.execute {
                    synchronized(lock) { if (closed) return@execute }
                    val deliver: () -> Unit = try {
                        val value = work()
                        ({ success(value) })
                    } catch (e: Exception) {
                        ({ failure(e) })
                    }
                    synchronized(lock) {
                        if (!closed) {
                            // 与 close 的清理操作互斥，不能在清理后再塞入旧回调。
                            post(Runnable {
                                synchronized(lock) {
                                    if (!closed) {
                                        busy = false
                                        deliver()
                                    }
                                }
                            })
                        }
                    }
                }
                true
            } catch (_: RejectedExecutionException) {
                busy = false
                false
            }
        }

    override fun close() {
        synchronized(lock) {
            if (closed) return
            closed = true
            busy = false
            clearCallbacks()
        }
        try {
            // interrupt 不能保证打断 socket 读操作，必须同时取消实际 Call。
            cancelRequests()
        } finally {
            executor.shutdownNow()
            executor.queue.clear()
        }
    }
}
