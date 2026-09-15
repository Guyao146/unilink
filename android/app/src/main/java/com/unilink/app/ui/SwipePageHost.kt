package com.unilink.app.ui

import android.content.Context
import android.util.AttributeSet
import android.util.TypedValue
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.view.ViewConfiguration
import android.widget.FrameLayout
import android.widget.ScrollView
import android.widget.Space
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

    /** 内容级联淡入的纵向位移（13dp）与节奏 */
    private val cascadeTranslation = with(context.resources.displayMetrics) {
        TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, 13f, this)
    }

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
            page(index)?.let { resetCascade(it) }
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
            resetCascade(old)
            return
        }
        next.visibility = View.VISIBLE
        next.translationX = 0f
        next.scaleX = 1f
        next.scaleY = 1f
        next.alpha = 0f
        resetCascade(next)
        old.translationX = 0f
        old.scaleX = 1f
        old.scaleY = 1f
        old.alpha = 1f
        resetCascade(old)
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
            old.animate().alpha(1f).setDuration(240L)
                .setInterpolator(Motion.SMOOTH).withEndAction {
                    next?.apply { visibility = View.GONE; alpha = 1f; resetCascade(this) }
                }.start()
        } else {
            // 旧页整体淡出，新页内容级联淡入；两页始终同层同位、不缩放
            currentPage = target
            onSwipePage?.invoke(target)   // Dock 立即同步，内容随后逐个跟上
            fadeOut(old)
            next.alpha = 1f
            cascadeIn(next)
        }
        dragging = false
        parent?.requestDisallowInterceptTouchEvent(false)
    }

    /** Dock 点击与滑动松手共用同一套过渡：旧页淡出 + 新页内容级联淡入 */
    private fun transition(old: View, next: View) {
        for (i in 0 until childCount) {
            page(i)?.apply {
                visibility = if (this == old || this == next) View.VISIBLE else View.GONE
                translationX = 0f
                scaleX = 1f
                scaleY = 1f
            }
        }
        currentPage = indexOfChild(next)
        fadeOut(old)
        next.alpha = 1f
        cascadeIn(next)
    }

    /** 旧页淡出并隐藏。隐藏前校验状态，避免被中途打断后误藏当前页 */
    private fun fadeOut(view: View) {
        view.alpha = 1f
        view.animate().alpha(0f).setDuration(200L)
            .setInterpolator(Motion.SMOOTH)
            .withEndAction { hideIfStale(view) }
            .start()
        view.postDelayed({ hideIfStale(view) }, 260L)
    }

    private fun hideIfStale(view: View) {
        if (currentPage != indexOfChild(view)) {
            view.visibility = View.GONE
            view.alpha = 1f
        }
    }

    /**
     * 页面内的信息单元（眉标题、大标题、状态岛、各张卡片），按视觉顺序收集。
     * 取内容列的直接子 view；页面结构不符合预期时回退为整页淡入。
     */
    private fun collectCascadeTargets(page: View): List<View> {
        val column = when (page) {
            is ScrollView -> if (page.childCount > 0) page.getChildAt(0) else return listOf(page)
            is ViewGroup -> if (page.childCount > 0) page.getChildAt(0) else return listOf(page)
            else -> return listOf(page)
        }
        if (column !is ViewGroup) return listOf(page)
        val out = ArrayList<View>()
        for (i in 0 until column.childCount) {
            val v = column.getChildAt(i)
            if (v is Space || v.visibility == View.GONE) continue
            out.add(v)
        }
        return if (out.isNotEmpty()) out else listOf(page)
    }

    /** 把页面内容恢复到静止状态，拖动与直接切页前调用，避免残留级联偏移 */
    private fun resetCascade(page: View) {
        page.animate().cancel()
        page.alpha = 1f
        page.translationY = 0f
        for (v in collectCascadeTargets(page)) {
            if (v === page) continue
            v.animate().cancel()
            v.alpha = 1f
            v.translationY = 0f
        }
    }

    /** 新页内容从下方逐个淡入，每个单元错开 40ms；页面本身保持不透明 */
    private fun cascadeIn(page: View) {
        val targets = collectCascadeTargets(page)
        var maxDelay = 60L
        for ((index, v) in targets.withIndex()) {
            val delay = 60L + index * 40L
            maxDelay = maxOf(maxDelay, delay)
            v.alpha = 0f
            v.translationY = if (v === page) 0f else cascadeTranslation
            v.animate().alpha(1f).translationY(0f)
                .setStartDelay(delay)
                .setDuration(300L)
                .setInterpolator(Motion.SMOOTH)
                .withEndAction {
                    v.alpha = 1f
                    v.translationY = 0f
                }.start()
        }
        // 兜底：动画被中途取消（用户快速再滑动）时仍把内容归位
        page.postDelayed({
            page.alpha = 1f
            for (v in targets) { v.alpha = 1f; v.translationY = 0f }
        }, maxDelay + 340L)
    }

    private fun stopAnimations() {
        for (i in 0 until childCount) page(i)?.animate()?.cancel()
    }
}

