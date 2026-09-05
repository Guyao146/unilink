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

    /** 显示指定页面；采用同一平面的交叉淡化 */
    fun showPage(index: Int, animate: Boolean = true) {
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
        transition(old, next)
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
                    prepareDrag(activeTarget)
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

    private fun prepareDrag(target: Int) {
        val old = page(currentPage) ?: return
        val next = page(target)
        if (next == null || target !in 0 until swipePageCount) {
            old.alpha = 1f
            return
        }
        // 两页重叠在同一位置：手势只改变透明度，不改变几何层级
        next.visibility = View.VISIBLE
        next.translationX = 0f
        next.scaleX = 1f
        next.scaleY = 1f
        next.alpha = 0f
        old.translationX = 0f
        old.scaleX = 1f
        old.scaleY = 1f
        old.alpha = 1f
    }

    private fun dragTo(dx: Float) {
        val old = page(currentPage) ?: return
        if (activeTarget !in 0 until swipePageCount) {
            // 边界页只做轻微透明度反馈，松手后恢复
            old.alpha = 1f - (abs(dx) / width.coerceAtLeast(1) * 0.08f)
            return
        }
        val next = page(activeTarget) ?: return
        val progress = (abs(dx) / width.toFloat().coerceAtLeast(1f)).coerceIn(0f, 1f)
        // 同一平面的渐进交叉淡化：位置、大小和层级几何关系完全不变
        old.alpha = 1f - progress
        next.alpha = progress
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
            // 只恢复透明度，不做位移/缩放回弹
            old.animate().alpha(1f).setDuration(240L)
                .setInterpolator(Motion.SMOOTH).withEndAction {
                    next?.apply { visibility = View.GONE; alpha = 1f }
                }.start()
        } else {
            // 两页始终重叠同层，只补完透明度差值
            old.animate().alpha(0f).setDuration(260L)
                .setInterpolator(Motion.SMOOTH).start()
            next.animate().alpha(1f).setDuration(260L)
                .setInterpolator(Motion.SMOOTH).withEndAction {
                    old.visibility = View.GONE
                    old.alpha = 1f
                    next.alpha = 1f
                    currentPage = target
                    onSwipePage?.invoke(target)
                }.start()
        }
        dragging = false
        parent?.requestDisallowInterceptTouchEvent(false)
    }

    /** Dock 点击也使用同一平面的交叉淡化，不改变页面位置或大小。 */
    private fun transition(old: View, next: View) {
        for (i in 0 until childCount) {
            page(i)?.apply {
                visibility = if (this == old || this == next) View.VISIBLE else View.GONE
                translationX = 0f
                scaleX = 1f
                scaleY = 1f
            }
        }
        old.alpha = 1f
        next.alpha = 0f
        old.animate().alpha(0f).setDuration(260L)
            .setInterpolator(Motion.SMOOTH).start()
        next.animate().alpha(1f).setDuration(260L)
            .setInterpolator(Motion.SMOOTH).withEndAction {
                currentPage = indexOfChild(next)
                old.visibility = View.GONE
                old.alpha = 1f
                next.alpha = 1f
            }.start()
    }

    private fun stopAnimations() {
        for (i in 0 until childCount) page(i)?.animate()?.cancel()
    }
}

