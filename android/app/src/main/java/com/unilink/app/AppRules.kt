package com.unilink.app

/**
 * 针对特定 IM 的无障碍控件适配规则表。
 *
 * 背景：各应用通知与聊天界面的控件差异很大：
 *  - **微信**：通知没有“回复”动作按钮，必须点通知本体进入会话；输入框默认可能是
 *    语音模式，需要先点“键盘”图标切回文本输入；
 *  - **QQ / TIM**：通知自带「快速回复」RemoteInput 动作；
 *  - **Telegram**：标准内联回复（纸飞机发送按钮是图标，依赖 contentDescription 匹配）。
 *
 * 如需适配其它应用，往 [rules] 里追加一条 [AppRule] 即可；未命中的包名一律走通用规则。
 */
data class AppRule(
    val pkgs: Set<String>,
    val label: String = "",
    /** 追加的“回复”按钮关键词（与全局词库合并匹配） */
    val replyWords: List<String> = emptyList(),
    /** 追加的“发送”按钮关键词 */
    val sendWords: List<String> = emptyList(),
    /** 追加的“展开通知”关键词 */
    val expandWords: List<String> = emptyList(),
    /** 填入文本前需要先点击的按钮关键词（如微信的“键盘”切换） */
    val preEditClickWords: List<String> = emptyList(),
    /** 无“回复”动作时，允许点击通知正文打开会话（微信模式） */
    val openByClickingBody: Boolean = false
)

object AppRules {

    val DEFAULT = AppRule(pkgs = emptySet())

    private val rules = listOf(
        // ─────────────── 微信 ───────────────
        AppRule(
            pkgs = setOf("com.tencent.mm"),
            label = "微信",
            openByClickingBody = true,
            preEditClickWords = listOf("键盘", "切换键盘", "切換鍵盤",
                                       "Keyboard", "keyboard"),
            sendWords = listOf("发送", "發送")
        ),
        // ─────────────── QQ / TIM ───────────────
        AppRule(
            pkgs = setOf(
                "com.tencent.mobileqq",   // 手机 QQ
                "com.tencent.tim",        // TIM
                "com.tencent.qqlite"      // QQ 极速版
            ),
            label = "QQ",
            replyWords = listOf("快速回复", "快速回覆"),
            sendWords = listOf("发送", "發送")
        ),
        // ─────────────── Telegram 及其常见分支 ───────────────
        AppRule(
            pkgs = setOf(
                "org.telegram.messenger",         // Telegram 正式版
                "org.telegram.messenger.web",     // Telegram Web/A 系列
                "org.telegram.plus",              // Plus Messenger
                "nekox.messenger",                // NekoX
                "tw.nekomimi.nekogram"            // Nekogram
            ),
            label = "Telegram",
            sendWords = listOf("发送", "傳送")
        )
    )

    private val index: Map<String, AppRule> =
        rules.flatMap { r -> r.pkgs.map { it to r } }.toMap()

    fun forPkg(pkg: String?): AppRule =
        if (pkg.isNullOrBlank()) DEFAULT else index[pkg] ?: DEFAULT

    /** 导出规则表（供 capability 帧同步给 PC，PC 回复弹窗据此显示适配模式） */
    fun export(): org.json.JSONArray {
        val arr = org.json.JSONArray()
        for (r in rules) {
            for (p in r.pkgs) {
                arr.put(org.json.JSONObject()
                    .put("pkg", p)
                    .put("label", r.label)
                    .put("open_body", r.openByClickingBody))
            }
        }
        return arr
    }
}
