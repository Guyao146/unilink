package com.unilink.app.auth

import org.junit.Assert.*
import org.junit.Test
import java.io.IOException
import java.util.concurrent.CountDownLatch
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

class AuthTasksTest {
    private class Harness(idleMs: Long = 10_000) : AutoCloseable {
        val callbacks = LinkedBlockingQueue<Runnable>()
        val cancellations = AtomicInteger()
        val executor = AuthTasks.newExecutor(idleMs)
        val tasks = AuthTasks({ cancellations.incrementAndGet() },
            { callbacks.add(it) }, { callbacks.clear() }, executor)

        fun next(): Runnable = callbacks.poll(3, TimeUnit.SECONDS)
            ?: throw AssertionError("No result callback")

        override fun close() {
            tasks.close()
            assertTrue("Worker must terminate", executor.awaitTermination(3, TimeUnit.SECONDS))
        }
    }

    @Test
    fun `one worker is reused and duplicate work is rejected until result is consumed`() {
        Harness().use { h ->
            val entered = CountDownLatch(1)
            val release = CountDownLatch(1)
            val workers = mutableListOf<Thread>()
            assertTrue(h.tasks.submit({
                entered.countDown()
                release.await()
                Thread.currentThread()
            }, { workers.add(it) }, { throw AssertionError(it) }))
            try {
                assertTrue(entered.await(3, TimeUnit.SECONDS))
                assertFalse(h.tasks.submit({ error("duplicate ran") }, {}, {}))
            } finally {
                release.countDown()
            }
            val first = h.next()
            assertFalse(h.tasks.submit({ error("duplicate ran") }, {}, {}))
            first.run()
            assertTrue(h.tasks.submit({ Thread.currentThread() },
                { workers.add(it) }, { throw AssertionError(it) }))
            h.next().run()
            assertSame(workers[0], workers[1])
            assertTrue(workers[0].name.startsWith("unilink-auth-"))
            assertEquals(1, h.executor.largestPoolSize)
        }
    }

    @Test
    fun `close interrupts active work cancels requests once and rejects later work`() {
        Harness().use { h ->
            val entered = CountDownLatch(1)
            val interrupted = CountDownLatch(1)
            h.tasks.submit({
                entered.countDown()
                try { CountDownLatch(1).await() }
                catch (e: InterruptedException) { interrupted.countDown(); throw e }
            }, { fail("late success") }, { fail("late failure") })
            assertTrue(entered.await(3, TimeUnit.SECONDS))
            h.tasks.close()
            h.tasks.close()
            assertTrue(interrupted.await(3, TimeUnit.SECONDS))
            assertTrue(h.executor.awaitTermination(3, TimeUnit.SECONDS))
            assertEquals(1, h.cancellations.get())
            assertTrue(h.callbacks.isEmpty())
            assertTrue(h.executor.queue.isEmpty())
            assertFalse(h.tasks.submit({ fail("closed task ran") }, {}, {}))
        }
    }

    @Test
    fun `close clears queued callbacks and suppresses already dequeued results`() {
        Harness().use { h ->
            h.tasks.submit({ 42 }, { fail("late UI update") }, { fail("late error") })
            val stale = h.next()
            h.callbacks.add(stale)
            h.tasks.close()
            assertTrue(h.callbacks.isEmpty())
            stale.run()
        }
    }

    @Test
    fun `failure is delivered and the next task can finish the scope from its callback`() {
        Harness().use { h ->
            val errors = AtomicInteger()
            h.tasks.submit({ throw IOException("offline") }, {}, { errors.incrementAndGet() })
            h.next().run()
            assertEquals(1, errors.get())
            assertTrue(h.tasks.submit({ 1 }, { h.tasks.close() }, { throw AssertionError(it) }))
            h.next().run()
            assertEquals(1, h.cancellations.get())
        }
    }

    @Test
    fun `idle worker exits and a later task starts a new worker`() {
        Harness(idleMs = 50).use { h ->
            var first: Thread? = null
            h.tasks.submit({ Thread.currentThread() }, { first = it }, { throw AssertionError(it) })
            h.next().run()
            first!!.join(3_000)
            assertFalse("Idle worker leaked", first!!.isAlive)
            h.tasks.submit({ Thread.currentThread() },
                { assertNotSame(first, it) }, { throw AssertionError(it) })
            h.next().run()
            assertEquals(1, h.executor.largestPoolSize)
        }
    }

    @Test
    fun `repeated page exits leave no workers or callbacks`() {
        repeat(30) {
            Harness().use { h ->
                h.tasks.submit({ Thread.currentThread() }, {}, { throw AssertionError(it) })
                val lateResult = h.next()
                h.tasks.close()
                lateResult.run()
                assertEquals(1, h.cancellations.get())
                assertTrue(h.callbacks.isEmpty())
            }
        }
    }
}
