package com.unilink.app.ui

import android.content.Context
import android.text.method.ScrollingMovementMethod
import android.util.AttributeSet
import android.view.MotionEvent
import android.widget.TextView

/**
 * UniLink 活动日志视图
 * ====================
 *
 * 这是一个固定高度、可独立滚动的日志窗口，而不是普通 TextView：
 *
 *  - 新内容到达且用户在底部：自动跟随最新日志；
 *  - 用户向上查看历史：保留当前位置，不被新日志抢回底部；
 *  - 用户重新滑到底部：恢复自动跟随；
 *  - 触摸时禁止外层页面 ScrollView 抢走手势，内外滚动互不打架。
 *
 * 不使用 NestedScrollView：同步页本身已经是 ScrollView，嵌套滚动容器
 * 在 Android 不同厂商实现上很容易出现滑动跳跃。TextView + MovementMethod
 * 足够承载日志，且层级更简单。
 */
class LogView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0
) : TextView(context, attrs, defStyleAttr) {

    private val bottomTolerancePx: Int = (8 * resources.displayMetrics.density).toInt()

    init {
        // TextView 自带的垂直滚动实现，避免引入新的滚动容器
        movementMethod = ScrollingMovementMethod.getInstance()
        isVerticalScrollBarEnabled = true
        isScrollbarFadingEnabled = true
        setHorizontallyScrolling(false)
        overScrollMode = OVER_SCROLL_IF_CONTENT_SCROLLS
    }

    /** 更新文本并按用户当前位置决定是否跟随到底部 */
    fun updateLogText(value: CharSequence) {
        val follow = isAtBottom()
        if (text.toString() == value.toString()) return

        setText(value, BufferType.NORMAL)
        if (follow) {
            // 新文本完成 layout 后再计算高度，否则 layout 还是旧的
            post { scrollTo(0, maxOf(0, layout?.height.orZero() - height)) }
        }
    }

    /** 是否位于日志末尾；允许少量像素误差，避免手指松开后状态抖动 */
    fun isAtBottom(): Boolean {
        val contentHeight = layout?.height ?: return true
        return scrollY + height >= contentHeight - bottomTolerancePx
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN,
            MotionEvent.ACTION_MOVE -> parent?.requestDisallowInterceptTouchEvent(true)
            MotionEvent.ACTION_UP,
            MotionEvent.ACTION_CANCEL -> parent?.requestDisallowInterceptTouchEvent(false)
        }
        return super.onTouchEvent(event)
    }

    private fun Int?.orZero(): Int = this ?: 0
}
