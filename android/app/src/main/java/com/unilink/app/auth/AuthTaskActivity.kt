package com.unilink.app.auth

import android.app.Activity
import android.os.Bundle
import android.os.Handler
import android.os.Looper

/** finish/onDestroy 都释放任务；跳到浏览器或扫码相机时的 onStop 不取消。 */
abstract class AuthTaskActivity : Activity() {
    protected lateinit var client: AuthClient
        private set
    private val callbacks = Handler(Looper.getMainLooper())
    private lateinit var tasks: AuthTasks

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        client = AuthClient(applicationContext)
        tasks = AuthTasks(
            cancelRequests = { client.close() },
            post = { callbacks.post(it) },
            clearCallbacks = { callbacks.removeCallbacksAndMessages(null) }
        )
    }

    protected fun <T> request(
        work: () -> T,
        success: (T) -> Unit,
        failure: (Exception) -> Unit
    ) {
        if (isFinishing || isDestroyed) return
        tasks.submit(work,
            { if (!isFinishing && !isDestroyed) success(it) },
            { if (!isFinishing && !isDestroyed) failure(it) })
    }

    override fun finish() {
        if (::tasks.isInitialized) tasks.close()
        super.finish()
    }

    override fun onDestroy() {
        if (::tasks.isInitialized) tasks.close()
        super.onDestroy()
    }
}
