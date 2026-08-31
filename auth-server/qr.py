# -*- coding: utf-8 -*-
"""
二维码生成（纯 SVG 输出）
=========================
直接用 qrcode 的模块矩阵手绘 SVG，不经过它的 image 工厂：
  * 不需要 Pillow（服务端省一个重依赖）；
  * 输出可直接内联进 HTML，无需额外一次图片请求，也不会被缓存；
  * 同色相邻模块合并成横向长条，SVG 体积约为逐格绘制的 1/3。
"""
import qrcode


def qr_matrix(text: str, ec=qrcode.constants.ERROR_CORRECT_M):
    qr = qrcode.QRCode(version=None, error_correction=ec,
                       box_size=1, border=0)
    qr.add_data(text)
    qr.make(fit=True)
    return qr.get_matrix()


def qr_svg(text: str, box: int = 6, quiet: int = 3,
           dark: str = "#111827", light: str = "#ffffff") -> str:
    """返回完整的 <svg> 字符串。box=单模块边长(px)，quiet=静默区模块数。"""
    m = qr_matrix(text)
    n = len(m)
    size = (n + quiet * 2) * box

    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
        'viewBox="0 0 %d %d" shape-rendering="crispEdges" '
        'role="img" aria-label="登录二维码">' % (size, size, size, size),
        '<rect width="%d" height="%d" fill="%s"/>' % (size, size, light),
        '<g fill="%s">' % dark,
    ]

    for y, row in enumerate(m):
        x = 0
        while x < n:
            if not row[x]:
                x += 1
                continue
            run = 1
            while x + run < n and row[x + run]:
                run += 1
            parts.append('<rect x="%d" y="%d" width="%d" height="%d"/>' % (
                (x + quiet) * box, (y + quiet) * box, run * box, box))
            x += run

    parts.append("</g></svg>")
    return "".join(parts)
