package com.unilink.app.ui

import android.graphics.RenderEffect
import android.graphics.Shader
import android.os.Build
import android.view.View
import android.view.ViewGroup

/**
 * 玻璃材质增强
 * ============
 * XML drawable 只能画出"半透明 + 高光边"，缺了玻璃最关键的一环：
 * **模糊背后的内容**。Android 12（API 31）起有了 [RenderEffect]，
 * 可以在不自己写 shader 的前提下拿到真实的背景虚化。
 *
 * 分级降级策略（与澎湃"柔光玻璃仅旗舰机型支持"同思路）：
 *  - API 31+ ：RenderEffect 真实模糊，观感最接近 Liquid Glass
 *  - API 24~30：退回纯半透明 + 高光边，仍有层次，只是不虚化
 *
 * 刻意不引入第三方模糊库：那类库多为"截图 + 缩放 + 高斯"的软件实现，
 * 在滚动时掉帧明显，代价高于收益。
 */
object Glass {

    val supportsBlur: Boolean
        get() = Build.VERSION.SDK_INT >= Build.VERSION_CODES.S

    /**
     * 给一组视图开启背景模糊。
     *
     * 注意 [RenderEffect] 模糊的是**视图自身及其子节点**，不是背后的窗口内容 ——
     * 因此不能直接作用在卡片上（会把卡片里的文字也糊掉）。
     * 正确用法是作用在一个专门的"背景层"视图上，卡片内容放在它之上。
     */
    fun applyBlur(view: View, radiusPx: Float) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S) return
        view.setRenderEffect(
            RenderEffect.createBlurEffect(radiusPx, radiusPx, Shader.TileMode.CLAMP)
        )
    }

    fun clearBlur(view: View) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S) return
        view.setRenderEffect(null)
    }

    /** 递归遍历视图树，对每个子节点执行 [action] */
    fun forEachDescendant(root: View, action: (View) -> Unit) {
        action(root)
        if (root is ViewGroup) {
            for (i in 0 until root.childCount) {
                forEachDescendant(root.getChildAt(i), action)
            }
        }
    }
}
