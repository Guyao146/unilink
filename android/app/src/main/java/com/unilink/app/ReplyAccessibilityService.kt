package com.unilink.app

import android.accessibilityservice.AccessibilityService
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo

/**
 * 无障碍自动回复服务（Pushbullet 同款思路）
 *
 * 流程：
 *   记录当前前台应用 → 拉下通知栏 → 定位目标通知的回复入口
 *   （通用关键词 / [AppRules] 应用专属规则 / 点通知正文）
 *   → 填入文本 → 点「发送」
 *   → 【还原阶段】收起通知栏、连续返回并校验前台包名，回到触发前的原应用
 *     （超时则保底拉起原应用）→ 通过 WebSocket 回报结果。
 *
 * 说明：
 *  - Android 安全模型不允许第三方直接触发其它应用通知里的 RemoteInput 动作，
 *    无障碍模拟点击是唯一通用的自动化途径；应用差异见 [AppRules] 适配规则表，
 *    未适配的应用走中英文关键词 + viewId 启发式匹配。
 *  - 全程失败（未开无障碍/锁屏/识别失败）时由 LinkService 回落为
 *    「复制到剪贴板 + 打开来 App」方案。
 *  - 采用 350ms 轮询驱动而非事件驱动，时序更稳；
 *    主流程超时 14 秒，还原阶段 5 秒。
 */
class ReplyAccessibilityService : AccessibilityService() {

    private val ui = Handler(Looper.getMainLooper())

    private var cur: ReplyTask? = null
    private var rule: AppRule = AppRules.DEFAULT

    private var step = STEP_FIND_NOTIF
    private var deadlineMs = 0L            // 主流程（找/填/发）总时限
    private var restoreDeadlineMs = 0L     // 还原阶段时限

    // 回复结果暂存：还原动作完成后统一回报给 PC
    private var pendingOk = false
    private var pendingMsg = ""

    // ── 还原现场状态 ──
    private var origPkg: String? = null    // 触发回复前的前台应用包名
    private var origWasShade = false       // 触发前是否本来就停在系统 UI（通知栏等）
    private var restoreTries = 0
    private var lastWindowPkg: String? = null   // 最近一次窗口切换的包名

    // ── 过程标记（防止重复点击） ──
    private var bodyClicked = false        // 是否已点过通知本体（微信模式）
    private var preClicked = false         // 是否已点过“切换键盘”类前置按钮

    companion object {
        private const val STEP_FIND_NOTIF = 0   // 找到回复入口并点击
        private const val STEP_FILL = 1         // 填入文本
        private const val STEP_SEND = 2         // 点“发送”
        private const val STEP_RESTORE = 3      // 收起通知栏并回到原应用

        private const val POLL_MS = 350L
        private const val TIMEOUT_MS = 14_000L
        private const val RESTORE_MS = 5_000L
        private const val MAX_BACKS = 3

        private val REPLY_WORDS =
            listOf("回复", "回覆", "答复", "Reply", "reply", "REPLY")
        private val SEND_WORDS =
            listOf("发送", "發送", "傳送", "Send", "SEND", "send")
        private val EXPAND_HINTS =
            listOf("expand", "expander", "group_toggle")
        private val EXPAND_WORDS =
            listOf("展开", "展開", "更多", "Expand")
        private const val SYSUI = "com.android.systemui"
    }

    // ================= 生命周期 =================

    override fun onServiceConnected() {
        super.onServiceConnected()
        ReplyBus.service = this
        Hub.a11y = true
        Hub.log("🛠 无障碍服务已连接：电脑回复可自动发送")
        Hub.broadcastCapabilities()   // 无障碍状态变化，同步给 PC
    }

    override fun onUnbind(intent: Intent?): Boolean {
        ReplyBus.service = null
        Hub.a11y = false
        Hub.log("⚠ 无障碍服务已断开，自动回复将退化为“复制+打开应用”")
        Hub.broadcastCapabilities()   // 无障碍状态变化，同步给 PC
        return super.onUnbind(intent)
    }

    override fun onInterrupt() {}

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        // 跟踪前台窗口包名，供还原阶段判断“是否已回到原应用”
        if (event?.eventType == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) {
            val p = event.packageName?.toString()
            if (!p.isNullOrBlank()) lastWindowPkg = p
        }
    }

    // ================= 任务调度 =================

    /** 返回 false 表示当前正忙 */
    fun dispatch(t: ReplyTask): Boolean {
        if (cur != null) return false
        cur = t
        rule = AppRules.forPkg(t.pkg)

        // 重置过程状态
        bodyClicked = false
        preClicked = false
        pendingOk = false
        pendingMsg = ""
        restoreTries = 0

        // 记录触发前的前台应用，结束后回到它
        origPkg = try {
            rootInActiveWindow?.packageName?.toString()
        } catch (_: Throwable) { null }
        if (origPkg.isNullOrBlank()) origPkg = lastWindowPkg
        origWasShade = origPkg != null &&
                (origPkg == SYSUI || origPkg!!.contains("systemui"))

        step = STEP_FIND_NOTIF
        deadlineMs = SystemClock.elapsedRealtime() + TIMEOUT_MS
        performGlobalAction(GLOBAL_ACTION_NOTIFICATIONS)   // 拉下通知栏
        ui.postDelayed(poller, 600)
        return true
    }

    private val poller = object : Runnable {
        override fun run() {
            val t = cur ?: return
            try {
                if (step == STEP_RESTORE) {
                    if (SystemClock.elapsedRealtime() > restoreDeadlineMs) completeRestore()
                    else tryRestore()
                } else {
                    if (SystemClock.elapsedRealtime() > deadlineMs) {
                        startRestore(false, "操作超时（未能定位到可回复的通知控件）")
                    } else when (step) {
                        STEP_FIND_NOTIF -> findAndClickReply(t)
                        STEP_FILL -> fillText(t)
                        STEP_SEND -> clickSend(t)
                    }
                }
            } catch (_: Throwable) {
                // 控件树瞬变是常态，下轮轮询重试
            }
            if (cur === t) ui.postDelayed(this, POLL_MS)
        }
    }

    // ================= 还原：收起通知栏并回到原应用 =================

    private fun startRestore(ok: Boolean, msg: String) {
        pendingOk = ok
        pendingMsg = msg
        step = STEP_RESTORE
        restoreDeadlineMs = SystemClock.elapsedRealtime() + RESTORE_MS
        restoreTries = 0
    }

    private fun tryRestore() {
        val curPkg = (try {
            rootInActiveWindow?.packageName?.toString()
        } catch (_: Throwable) { null })
            ?.takeIf { it.isNotBlank() } ?: lastWindowPkg

        // 触发前就停在系统界面：只需收起通知栏即可
        if (origWasShade || origPkg.isNullOrBlank()) {
            if (restoreTries == 0) {
                performGlobalAction(GLOBAL_ACTION_BACK)
                restoreTries++
            } else {
                completeRestore()
            }
            return
        }

        // 已回到原应用 → 完成
        if (curPkg != null && curPkg == origPkg) {
            completeRestore()
            return
        }

        // 连续 BACK 仍回不去（会话页叠了多层等）→ 保底直接拉起原应用
        if (restoreTries >= MAX_BACKS) {
            launchOriginal()
            completeRestore()
            return
        }
        performGlobalAction(GLOBAL_ACTION_BACK)
        restoreTries++
    }

    private fun launchOriginal() {
        val p = origPkg ?: return
        if (p == SYSUI || p.contains("systemui")) return
        try {
            packageManager.getLaunchIntentForPackage(p)?.let {
                it.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                startActivity(it)
            }
        } catch (_: Throwable) {
        }
    }

    private fun completeRestore() {
        val t = cur ?: return
        cur = null
        ui.removeCallbacks(poller)
        Hub.reportReplyResult(t, pendingOk, pendingMsg)
    }

    // ================= 步骤实现 =================

    private fun findAndClickReply(t: ReplyTask) {
        val root = rootInActiveWindow ?: return

        // 1) 通用词库 + 应用专属词库，找「回复」动作按钮
        findClickableByText(root, REPLY_WORDS + rule.replyWords,
                            listOf("reply"), t.pkg)?.let { n ->
            if (n.performAction(AccessibilityNodeInfo.ACTION_CLICK)) {
                step = STEP_FILL
            }
            return
        }

        // 2) 确认目标通知在屏幕上
        if (t.title.isBlank() ||
            findByTextContaining(root, t.title, 80) == null) return

        // 尝试先展开通知（部分机型折叠时不显示动作按钮）
        findClickableByText(root, EXPAND_WORDS + rule.expandWords,
                            EXPAND_HINTS, t.pkg)?.let {
            it.performAction(AccessibilityNodeInfo.ACTION_CLICK)
        }

        // 3) 应用专属（微信模式）：没有“回复”动作 → 点通知本体直接进入会话
        if (rule.openByClickingBody && !bodyClicked) {
            findByTextContaining(root, t.title, 80)?.let { n ->
                var v = n
                var d = 0
                while (!v.isClickable && d++ < 5) {
                    v = v.parent ?: break
                }
                if (v.isClickable &&
                    v.performAction(AccessibilityNodeInfo.ACTION_CLICK)) {
                    bodyClicked = true
                    step = STEP_FILL
                }
            }
        }
    }

    private fun fillText(t: ReplyTask) {
        val root = rootInActiveWindow ?: return

        // 应用专属前置：语音模式下先点「键盘」切回文字输入（微信常见）
        if (rule.preEditClickWords.isNotEmpty()) {
            if (findEditable(root, t.pkg) == null && !preClicked) {
                findClickableByText(root, rule.preEditClickWords,
                                    listOf("keyboard"), t.pkg)?.let {
                    if (it.performAction(AccessibilityNodeInfo.ACTION_CLICK)) {
                        preClicked = true
                    }
                }
                return   // 等下一轮轮询再尝试填写
            }
        }

        val edit = findEditable(root, t.pkg) ?: return
        val args = Bundle().apply {
            putCharSequence(
                AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, t.text)
        }
        if (edit.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)) {
            step = STEP_SEND
        }
    }

    private fun clickSend(t: ReplyTask) {
        val root = rootInActiveWindow ?: return
        val send = findClickableByText(root, SEND_WORDS + rule.sendWords,
                                       listOf("send"), t.pkg)
        if (send != null && send.performAction(AccessibilityNodeInfo.ACTION_CLICK)) {
            startRestore(true, successMsg())
        }
        // 找不到发送按钮则继续轮询直到超时（部分应用回车即发送）
    }

    private fun successMsg(): String {
        val base = "已在手机上自动发送"
        return if (rule.label.isBlank()) base else "$base（${rule.label}）"
    }

    // ================= 节点查找工具 =================

    /** 深度优先查找文本包含 needle 的节点 */
    private fun findByTextContaining(
        root: AccessibilityNodeInfo, needle: String, maxNodes: Int
    ): AccessibilityNodeInfo? {
        var count = 0
        fun dfs(n: AccessibilityNodeInfo): AccessibilityNodeInfo? {
            if (count++ > maxNodes) return null
            if (needle.isNotEmpty() && n.text?.contains(needle) == true) return n
            for (i in 0 until n.childCount) {
                val c = n.getChild(i) ?: continue
                val r = dfs(c)
                if (r != null) return r
            }
            return null
        }
        return dfs(root)
    }

    /**
     * 查找 text / contentDescription / viewId 命中关键词的可点击节点；
     * 目标本身不可点时向上回溯最多 4 层父容器。
     * [preferPkg]: 优先返回该包名下的节点（避免误点输入法/系统悬浮窗上的同名按钮），
     *              找不到时退回任意命中节点。
     */
    private fun findClickableByText(
        root: AccessibilityNodeInfo,
        words: List<String>,
        idHints: List<String>,
        preferPkg: String? = null
    ): AccessibilityNodeInfo? {
        var count = 0
        var fallback: AccessibilityNodeInfo? = null

        fun hit(n: AccessibilityNodeInfo): Boolean {
            val txt = n.text?.toString() ?: ""
            val des = n.contentDescription?.toString() ?: ""
            val vid = (n.viewIdResourceName ?: "").substringAfterLast('/')
            for (w in words) {
                if (txt.contains(w)) return true
                if (des.contains(w, ignoreCase = true)) return true
            }
            for (h in idHints) {
                if (vid.contains(h, ignoreCase = true)) return true
            }
            return false
        }

        fun resolve(n: AccessibilityNodeInfo): AccessibilityNodeInfo? {
            var v = n
            var d = 0
            while (!v.isClickable && d++ < 4) {
                v = v.parent ?: break
            }
            return if (v.isClickable) v else null
        }

        fun dfs(n: AccessibilityNodeInfo): AccessibilityNodeInfo? {
            if (count++ > 2500) return null
            if (hit(n)) {
                val v = resolve(n)
                if (v != null) {
                    if (preferPkg == null || v.packageName?.toString() == preferPkg) return v
                    if (fallback == null) fallback = v
                }
            }
            for (i in 0 until n.childCount) {
                val c = n.getChild(i) ?: continue
                val r = dfs(c)
                if (r != null) return r
            }
            return null
        }
        return dfs(root) ?: fallback
    }

    /**
     * 查找可编辑文本框（RemoteInput 输入框 / 会话输入框）；
     * [preferPkg]: 优先返回该包名下的输入框，找不到时退回任意输入框。
     */
    private fun findEditable(
        root: AccessibilityNodeInfo, preferPkg: String? = null
    ): AccessibilityNodeInfo? {
        var count = 0
        var fallback: AccessibilityNodeInfo? = null
        fun dfs(n: AccessibilityNodeInfo): AccessibilityNodeInfo? {
            if (count++ > 2500) return null
            if (n.className == android.widget.EditText::class.java.name || n.isEditable) {
                if (preferPkg == null || n.packageName?.toString() == preferPkg) return n
                if (fallback == null) fallback = n
            }
            for (i in 0 until n.childCount) {
                val c = n.getChild(i) ?: continue
                val r = dfs(c)
                if (r != null) return r
            }
            return null
        }
        return dfs(root) ?: fallback
    }
}

/** 全局桥：让 LinkService 在不绑定 Service 的情况下拿到实例 */
object ReplyBus {
    @Volatile var service: ReplyAccessibilityService? = null
    val ready: Boolean get() = service != null
}
