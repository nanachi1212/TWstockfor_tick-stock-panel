"""圖標設計方案預覽 — 生成多個精緻方案供挑選。

每個方案以 1024x1024 超清繪製 (4x 超採樣抗鋸齒), 輸出 PNG 預覽。
選定後, 用 generate_icon.py 產出最終多尺寸 .ico。

品牌色系 (從 favicon #5B21B6 延伸):
  深紫 #2E1065  主紫 #5B21B6  亮紫 #7C3AED  淺紫 #A78BFA
  強調金 #FBBF24 (金融上漲感)
"""
from __future__ import annotations

from pathlib import Path
import math

from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).parent / "designs"
OUT.mkdir(exist_ok=True)

# 品牌色
DEEP = (46, 16, 101)        # #2E1065
MAIN = (91, 33, 182)        # #5B21B6
BRIGHT = (124, 58, 237)     # #7C3AED
LIGHT = (167, 139, 250)     # #A78BFA
GOLD = (251, 191, 36)       # #FBBF24
WHITE = (255, 255, 255)


def _supersample(size: int, factor: int = 4):
    """返回 (大畫布, 縮放比, 最終尺寸)。繪製後縮放回 size, 抗鋸齒。"""
    s = size * factor
    return Image.new("RGBA", (s, s), (0, 0, 0, 0)), factor, s


def _finish(img: Image.Image, size: int) -> Image.Image:
    """超採樣縮小到目標尺寸。"""
    return img.resize((size, size), Image.LANCZOS)


def _v_gradient(size: int, c_top, c_bot, radius_ratio: float = 0.0):
    """豎直漸變填充 (可選圓角)。返回 RGBA Image。"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    px = img.load()
    for y in range(size):
        t = y / max(1, size - 1)
        r = int(c_top[0] + (c_bot[0] - c_top[0]) * t)
        g = int(c_top[1] + (c_bot[1] - c_top[1]) * t)
        b = int(c_top[2] + (c_bot[2] - c_top[2]) * t)
        for x in range(size):
            px[x, y] = (r, g, b, 255)
    if radius_ratio > 0:
        mask = Image.new("L", (size, size), 0)
        md = ImageDraw.Draw(mask)
        r = int(size * radius_ratio)
        md.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=255)
        img.putalpha(mask)
    return img


def _glow(size: int, draw_fn, color, blur_ratio: float = 0.04):
    """對繪製內容做柔和光暈: 先畫到獨立圖層, 模糊後疊加。"""
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    draw_fn(d)
    glow = layer.filter(ImageFilter.GaussianBlur(size * blur_ratio))
    return glow


# ── 方案 1: 漸變紫底 + 白色發光主體 (現代 App 風格) ──────────────
def design_1(size: int = 1024) -> Image.Image:
    img, f, s = _supersample(size)

    # 圓角漸變背景 (深紫 → 亮紫)
    bg = _v_gradient(s, BRIGHT, DEEP, radius_ratio=0.22)
    img.alpha_composite(bg)

    d = ImageDraw.Draw(img)
    u = s / 32  # 單位 (基於32基準)

    # 白色方括號 (圓頭粗線)
    sw = 2.8 * u
    def draw_brackets(dd):
        # 左 [
        dd.line([(11, 6), (5, 6)], fill=WHITE, width=int(sw), joint="curve")
        dd.line([(5, 6), (5, 26)], fill=WHITE, width=int(sw))
        dd.line([(5, 26), (11, 26)], fill=WHITE, width=int(sw))
        # 右 ]
        dd.line([(21, 6), (27, 6)], fill=WHITE, width=int(sw))
        dd.line([(27, 6), (27, 26)], fill=WHITE, width=int(sw))
        dd.line([(27, 26), (21, 26)], fill=WHITE, width=int(sw))

    # 白色 K 線 wick
    def draw_wick(dd):
        dd.line([(16, 8), (16, 24)], fill=LIGHT, width=int(1.6 * u))
    # 白色 K 線 body
    def draw_body(dd):
        dd.rounded_rectangle(
            [12.5 * u, 12 * u, 19.5 * u, 21 * u],
            radius=1.2 * u, fill=WHITE,
        )

    # 光暈層 (放大模糊)
    glow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    draw_body(gd)
    glow = glow.filter(ImageFilter.GaussianBlur(s * 0.025))
    # 光暈染色為亮紫
    tint = Image.new("RGBA", (s, s), BRIGHT + (0,))
    glow = Image.composite(tint, Image.new("RGBA", (s, s), (0, 0, 0, 0)),
                           glow.point(lambda a: min(255, int(a * 0.6))))
    img.alpha_composite(glow)

    draw_brackets(d)
    draw_wick(d)
    draw_body(d)

    return _finish(img, size)


# ── 方案 2: 透明底 + 漸變紫發光主體 (優雅線條) ──────────────────
def design_2(size: int = 1024) -> Image.Image:
    img, f, s = _supersample(size)
    d = ImageDraw.Draw(img)
    u = s / 32

    sw = 3.0 * u

    # 漸變括號: 用淺紫到亮紫。Pillow line 不支持漸變, 分段填色模擬。
    def gradient_brackets(dd):
        # 左括號三段, 從上(淺)到下(亮) — 簡化為整體亮紫, 配光暈顯層次
        col = BRIGHT
        # 左 [
        dd.line([(11, 5), (5, 5)], fill=col, width=int(sw))
        dd.line([(5, 5), (5, 27)], fill=col, width=int(sw))
        dd.line([(5, 27), (11, 27)], fill=col, width=int(sw))
        # 右 ]
        dd.line([(21, 5), (27, 5)], fill=col, width=int(sw))
        dd.line([(27, 5), (27, 27)], fill=col, width=int(sw))
        dd.line([(27, 27), (21, 27)], fill=col, width=int(sw))

    # 主體光暈 (亮紫大模糊)
    glow_layer = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow_layer)
    gradient_brackets(gd)
    gd.rounded_rectangle([12.5 * u, 12 * u, 19.5 * u, 21 * u], radius=1.2 * u, fill=BRIGHT)
    glow = glow_layer.filter(ImageFilter.GaussianBlur(s * 0.05))
    img.alpha_composite(glow)

    # 主體 (亮紫)
    gradient_brackets(d)
    d.line([(16, 8), (16, 24)], fill=LIGHT, width=int(1.8 * u))
    d.rounded_rectangle([12.5 * u, 12 * u, 19.5 * u, 21 * u], radius=1.2 * u, fill=BRIGHT)

    # 高光: body 頂部一道淺紫
    d.rounded_rectangle([13.5 * u, 12.8 * u, 18.5 * u, 14.5 * u], radius=0.8 * u, fill=LIGHT)

    return _finish(img, size)


# ── 方案 3: 深色底 + 金紫上漲 K 線柱 (金融圖表感) ────────────────
def design_3(size: int = 1024) -> Image.Image:
    img, f, s = _supersample(size)

    # 深紫黑圓角底
    bg = _v_gradient(s, (30, 27, 75), (15, 12, 41), radius_ratio=0.22)
    img.alpha_composite(bg)
    d = ImageDraw.Draw(img)
    u = s / 32

    # 三根上漲 K 線柱 (紫→亮紫→金), 從左到右升高
    bars = [
        # (x_center, body_top, body_bottom, wick_top, wick_bottom, color)
        (10, 17, 24, 14, 26, LIGHT),
        (16, 13, 20, 10, 22, BRIGHT),
        (22, 8, 15, 5, 17, GOLD),
    ]
    bw = 3.2 * u
    for cx, bt, bb, wt, wb, col in bars:
        # wick
        d.line([(cx * u, wt * u), (cx * u, wb * u)], fill=col, width=int(1.2 * u))
        # body (圓角)
        d.rounded_rectangle(
            [cx * u - bw / 2, bt * u, cx * u + bw / 2, bb * u],
            radius=0.8 * u, fill=col,
        )

    # 金色柱的光暈
    glow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.rounded_rectangle([22 * u - bw / 2, 8 * u, 22 * u + bw / 2, 15 * u], radius=0.8 * u, fill=GOLD)
    glow = glow.filter(ImageFilter.GaussianBlur(s * 0.03))
    img.alpha_composite(glow)

    # 上漲趨勢線 (淡, 連接柱頂)
    d.line([(10 * u, 17 * u), (16 * u, 13 * u), (22 * u, 8 * u)],
           fill=(255, 255, 255, 120), width=int(0.6 * u))

    return _finish(img, size)


# ── 方案 4: 漸變底 + K線被方括號"框選" (品牌強調) ────────────────
def design_4(size: int = 1024) -> Image.Image:
    img, f, s = _supersample(size)

    # 斜向漸變背景 (淺紫→主紫), 圓角
    bg = _v_gradient(s, (139, 92, 246), MAIN, radius_ratio=0.22)
    img.alpha_composite(bg)
    d = ImageDraw.Draw(img)
    u = s / 32

    # 白色粗括號 (圓角連接)
    sw = 2.2 * u
    def brackets(dd):
        dd.rounded_rectangle(
            [5 * u, 6 * u, 11 * u, 26 * u],
            radius=2.0 * u, width=int(sw), outline=WHITE,
        )  # 左括號外形
        dd.rounded_rectangle(
            [21 * u, 6 * u, 27 * u, 26 * u],
            radius=2.0 * u, width=int(sw), outline=WHITE,
        )  # 右括號外形
    # 上面的畫法畫出的是矩形框, 改回線條式括號但圓頭
    def brackets2(dd):
        cap = int(sw / 2)
        # 左 [
        dd.line([(10.5, 6), (6, 6)], fill=WHITE, width=int(sw))
        dd.rounded_rectangle([6 * u - cap, 6 * u - cap, 6 * u + cap, 26 * u + cap],
                             radius=cap, fill=WHITE)  # 豎幹
        dd.line([(6, 26), (10.5, 26)], fill=WHITE, width=int(sw))
        # 圓角補點
        for cx, cy in [(6, 6), (6, 26)]:
            dd.ellipse([cx * u - cap, cy * u - cap, cx * u + cap, cy * u + cap], fill=WHITE)
        # 右 ]
        dd.line([(21.5, 6), (26, 6)], fill=WHITE, width=int(sw))
        dd.rounded_rectangle([26 * u - cap, 6 * u - cap, 26 * u + cap, 26 * u + cap],
                             radius=cap, fill=WHITE)
        dd.line([(26, 26), (21.5, 26)], fill=WHITE, width=int(sw))
        for cx, cy in [(26, 6), (26, 26)]:
            dd.ellipse([cx * u - cap, cy * u - cap, cx * u + cap, cy * u + cap], fill=WHITE)

    # K 線光暈
    glow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.rounded_rectangle([12.8 * u, 11.5 * u, 19.2 * u, 21.5 * u], radius=1.4 * u, fill=WHITE)
    glow = glow.filter(ImageFilter.GaussianBlur(s * 0.02))
    img.alpha_composite(glow)

    brackets2(d)
    # wick
    d.line([(16, 8), (16, 24)], fill=(255, 255, 255, 200), width=int(1.5 * u))
    # body (實心白)
    d.rounded_rectangle([12.8 * u, 11.5 * u, 19.2 * u, 21.5 * u], radius=1.4 * u, fill=WHITE)

    return _finish(img, size)


def main() -> None:
    designs = [
        ("方案1_漸變紫底白色發光", design_1),
        ("方案2_透明底漸變紫線條", design_2),
        ("方案3_深色底金紫上漲K線", design_3),
        ("方案4_淺紫底框選K線", design_4),
    ]
    # 拼一張對比圖 (2x2)
    cell = 512
    grid = Image.new("RGBA", (cell * 2 + 60, cell * 2 + 60), (245, 245, 247, 255))
    gd = ImageDraw.Draw(grid)
    positions = [(20, 20), (cell + 40, 20), (20, cell + 40), (cell + 40, cell + 40)]
    for (name, fn), pos in zip(designs, positions):
        img = fn(cell)
        # 棋盤格背景透顯
        grid.alpha_composite(img, pos)
        # 單獨存
        fn(1024).save(OUT / f"{name}.png")
        print(f"  生成: {name}.png")

    grid.save(OUT / "對比圖_2x2.png")
    print(f"\n對比圖: {OUT / '對比圖_2x2.png'}")
    print(f"單圖目錄: {OUT}")


if __name__ == "__main__":
    main()
