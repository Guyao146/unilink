package com.unilink.app.ui

import android.animation.ValueAnimator
import android.view.View
import android.view.animation.PathInterpolator
import android.widget.Button
import android.widget.TextView

/**
 * 交互光效与动效
 * ==============
 * 对应澎湃 OS 的"点按有光，操作有回应"与 Apple 的"对动作产生动态响应"。
 *
 * 两个设计决定：
 *
 * 1. **缓动曲线用 PathInterpolator 而非系统默认**。
 *    Material 的 FastOutSlowIn 尾段偏慢，用在玻璃上会显得"黏"。
 *    这里用一条起步快、收尾极短的曲线，让响应贴手。
 *
 * 2. **按下反馈是"缩放 + 提亮"，不是变暗**。
 *    传统按下变暗源自实体按键的阴影隐喻；玻璃是受光材质，
 *    被触碰时应该更亮 —— 光效从触点泛开，这是两家新设计的共同点。
 */
object Motion {

    /** 起步快、收尾利落。数值取自反复试感，不是标准曲线 */
    val SNAPPY: PathInterpolator = PathInterpolator(0.22f, 1f, 0.36f, 1f)

    /** 用于状态渐变等需要平滑过渡的场合 */
    val SMOOTH: PathInterpolator = PathInterpolator(0.4f, 0f, 0.2f, 1f)

    private const val PRESS_SCALE = 0.97f
    private const val PRESS_MS = 90L
    private const val RELEASE_MS = 220L

    /**
     * 给视图加上按压响应。
     *
     * 不覆盖 OnClickListener —— 用 OnTouchListener 会吃掉无障碍点击事件，
     * 这里改用 [View.setOnTouchListener] 的**只观察不消费**写法：
     * 始终返回 false，让触摸事件继续传给点击处理与无障碍服务。
     */
    fun press(view: View) {
        view.setOnTouchListener { v, event ->
            when (event.actionMasked) {
                android.view.MotionEvent.ACTION_DOWN -> scale(v, PRESS_SCALE, PRESS_MS)
                android.view.MotionEvent.ACTION_UP,
                android.view.MotionEvent.ACTION_CANCEL -> scale(v, 1f, RELEASE_MS)
            }
            false      // 关键：不消费事件
        }
    }

    private fun scale(v: View, to: Float, duration: Long) {
        v.animate()
            .scaleX(to).scaleY(to)
            .setDuration(duration)
            .setInterpolator(SNAPPY)
            .start()
    }

    /** 批量给一组按钮加按压响应 */
    fun pressAll(vararg views: View) = views.forEach { press(it) }

    /**
     * 卡片入场：轻微上浮 + 淡入，按索引错开。
     * 错开量刻意很小（40ms）—— 大了会变成"一个个蹦出来"的廉价感，
     * 目标是让整屏内容像一次呼吸那样浮现。
     */
    fun enter(view: View, index: Int) {
        view.alpha = 0f
        view.translationY = 24f
        view.animate()
            .alpha(1f)
            .translationY(0f)
            .setStartDelay(index * 40L)
            .setDuration(420L)
            .setInterpolator(SNAPPY)
            .start()
    }

    /**
     * 文本颜色平滑过渡。
     * 状态文字（如"已连接"）变色时直接跳变会很突兀，
     * 玻璃语言里一切变化都应该是"流动"的。
     */
    fun tintText(tv: TextView, toColor: Int, duration: Long = 260L) {
        val from = tv.currentTextColor
        if (from == toColor) return
        ValueAnimator.ofArgb(from, toColor).apply {
            this.duration = duration
            interpolator = SMOOTH
            addUpdateListener { tv.setTextColor(it.animatedValue as Int) }
            start()
        }
    }

    /** 按钮文字在启用/禁用间的透明度过渡 */
    fun fadeEnabled(btn: Button, enabled: Boolean) {
        btn.isEnabled = enabled
        btn.animate()
            .alpha(if (enabled) 1f else 0.4f)
            .setDuration(200L)
            .setInterpolator(SMOOTH)
            .start()
    }
}
