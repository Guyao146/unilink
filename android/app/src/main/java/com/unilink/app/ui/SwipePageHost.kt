package com.unilink.app.ui

import android.content.Context
import android.util.AttributeSet
import android.view.MotionEvent
import android.view.View
import android.view.ViewConfiguration
import android.widget.FrameLayout
import kotlin.math.abs

/**
 * UniLink 横向分页容器。
 * 横向位移超过 touchSlop 且明显大于纵向位移时接管手势，
 * 否则交给页面内部 ScrollView，避免上下滚动被误识别为切页。
 */
class SwipePageHost @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null
) : FrameLayout(context, attrs) {

    private val slop = ViewConfiguration.get(context).scaledTouchSlop
    private var downX = 0f
    private var downY = 0f
    private var dragging = false
    private var activeTarget = -1
    private var activeDirection = 0

    var currentPage: Int = 0
        private set

    /** 关于页等非主导航页不参与横向切换 */
    var swipePageCount: Int = 4

    /** Activity 用于同步 Dock */
    var onSwipePage: ((Int) -> Unit)? = null

    private fun page(index: Int): View? =
        if (index in 0 until childCount) getChildAt(index) else null

    /** 显示指定页面；direction=-1 左滑方向，direction=1 右滑方向 */
    fun showPage(index: Int, animate: Boolean = true, direction: Int = 0) {
        if (index !in 0 until childCount) return
        val old = page(currentPage)
        val next = page(index) ?: return
        if (!animate || old == null || old == next || width <= 0) {
            for (i in 0 until childCount) page(i)?.apply {
                visibility = if (i == index) View.VISIBLE else View.GONE
                translationX = 0f
                alpha = 1f
            }
            currentPage = index
            return
        }
        val dir = if (direction == 0) if (index > currentPage) -1 else 1 else direction
        transition(old, next, dir)
    }

    override fun onInterceptTouchEvent(event: MotionEvent): Boolean {
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                stopAnimations()
                downX = event.x
                downY = event.y
                dragging = false
                activeTarget = -1
                activeDirection = 0
            }
            MotionEvent.ACTION_MOVE -> {
                val dx = event.x - downX
                val dy = event.y - downY
                if (!dragging && abs(dx) > slop && abs(dx) > abs(dy) * 1.15f) {
                    dragging = true
                    activeDirection = if (dx < 0f) -1 else 1
                    activeTarget = currentPage - activeDirection
                    prepareDrag(activeTarget, activeDirection)
                    parent?.requestDisallowInterceptTouchEvent(true)
                    return true
                }
            }
            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                dragging = false
                parent?.requestDisallowInterceptTouchEvent(false)
            }
        }
        return dragging
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        if (!dragging && event.actionMasked != MotionEvent.ACTION_DOWN) return true
        when (event.actionMasked) {
            MotionEvent.ACTION_MOVE -> dragTo(event.x - downX)
            MotionEvent.ACTION_UP -> finishDrag(event.x - downX)
            MotionEvent.ACTION_CANCEL -> finishDrag(0f)
        }
        return true
    }

    private fun prepareDrag(target: Int, direction: Int) {
        val old = page(currentPage) ?: return
        val next = page(target)
        if (next == null || target !in 0 until swipePageCount) {
            old.translationX = 0f
            return
        }
        next.visibility = View.VISIBLE
        next.alpha = 1f
        next.translationX = direction * width.toFloat()
        old.translationX = 0f
    }

    private fun dragTo(dx: Float) {
        val old = page(currentPage) ?: return
        if (activeTarget !in 0 until swipePageCount) {
            // 边界页使用阻尼拖动，松手后回弹
            old.translationX = dx * 0.18f
            old.alpha = 1f - (abs(dx) / width.coerceAtLeast(1) * 0.08f)
            return
        }
        val next = page(activeTarget) ?: return
        val w = width.toFloat().coerceAtLeast(1f)
        val progress = (abs(dx) / w).coerceIn(0f, 1f)
        // 页面不再整屏横飞，只做轻微视差；主变化来自透明度与缩放
        old.translationX = dx * 0.18f
        old.scaleX = 1f - progress * 0.035f
        old.scaleY = 1f - progress * 0.035f
        old.alpha = 1f - progress * 0.28f
        next.translationX = activeDirection * w * 0.18f + dx * 0.18f
        next.scaleX = 0.965f + progress * 0.035f
        next.scaleY = 0.965f + progress * 0.035f
        next.alpha = 0.72f + progress * 0.28f
    }

    private fun finishDrag(dx: Float) {
        val old = page(currentPage) ?: return
        val target = activeTarget
        val threshold = width * 0.22f
        val complete = target in 0 until swipePageCount &&
            abs(dx) >= threshold &&
            ((activeDirection < 0 && dx < 0f) || (activeDirection > 0 && dx > 0f))

        val next = page(target)
        if (!complete || next == null) {
            // 只回到手势开始前的状态，不做突然的横向跳回
            old.animate().translationX(0f).scaleX(1f).scaleY(1f).alpha(1f)
                .setDuration(240L).setInterpolator(Motion.SMOOTH).withEndAction {
                    next?.apply {
                        visibility = View.GONE
                        translationX = 0f
                        scaleX = 1f
                        scaleY = 1f
                        alpha = 1f
                    }
                }.start()
        } else {
            // 当前拖动已经完成一部分；只补完剩余距离，避免重新从屏外飞入
            old.animate().translationX(0f).scaleX(0.965f).scaleY(0.965f).alpha(0f)
                .setDuration(260L).setInterpolator(Motion.SMOOTH).start()
            next.animate().translationX(0f).scaleX(1f).scaleY(1f).alpha(1f)
                .setDuration(260L).setInterpolator(Motion.SMOOTH).withEndAction {
                    old.visibility = View.GONE
                    old.translationX = 0f
                    old.scaleX = 1f
                    old.scaleY = 1f
                    old.alpha = 1f
                    currentPage = target
                    onSwipePage?.invoke(target)
                }.start()
        }
        dragging = false
        parent?.requestDisallowInterceptTouchEvent(false)
    }

    /** Dock/其它代码调用的动画切页：与手势保持同一套渐进式视觉语言。 */
    private fun transition(old: View, next: View, direction: Int) {
        for (i in 0 until childCount) {
            page(i)?.visibility = if (page(i) == old || page(i) == next) View.VISIBLE else View.GONE
        }
        val w = width.toFloat().coerceAtLeast(1f)
        val offset = w * 0.18f
        next.translationX = direction * offset
        next.scaleX = 0.965f
        next.scaleY = 0.965f
        next.alpha = 0.72f
        old.translationX = 0f
        old.scaleX = 1f
        old.scaleY = 1f
        old.alpha = 1f

        old.animate().translationX(if (direction < 0) -offset else offset)
            .scaleX(0.965f).scaleY(0.965f).alpha(0.72f)
            .setDuration(260L).setInterpolator(Motion.SMOOTH).start()
        next.animate().translationX(0f).scaleX(1f).scaleY(1f).alpha(1f)
            .setDuration(260L).setInterpolator(Motion.SMOOTH).withEndAction {
                currentPage = indexOfChild(next)
                for (i in 0 until childCount) if (page(i) != next) {
                    page(i)?.apply {
                        visibility = View.GONE
                        translationX = 0f
                        scaleX = 1f
                        scaleY = 1f
                        alpha = 1f
                    }
                }
            }.start()
    }

    private fun stopAnimations() {
        for (i in 0 until childCount) page(i)?.animate()?.cancel()
    }
}

