"""生成應用圖標 — 白色 squircle 背景 + 紫色 Logo。

設計 (與 frontend Logo.tsx / favicon.svg 一致):
  背景: 白色 squircle (macOS Big Sur+ 要求不透明 squircle 背景, 用白色填充)
  內容: 紫色 #5B21B6 方括號 + K線 (上影短/下影長, bullish 站穩)

蠟燭幾何 (32x32 viewBox):
  wick: y=7 ~ y=25
  body: y=9 ~ y=19 (偏上)
  → 上影 = 2 (短), 下影 = 6 (長)

每尺寸獨立繪製: 線條佔比全尺寸統一 (~9.4%), 小尺寸微調補償, 不隨尺寸遞減。
運行: python packaging/generate_icon.py
產物:
  packaging/icon.ico   — Windows (16/32/48/64/128/256)
  packaging/icon.icns  — macOS   (16/32/64/128/256/512, 含 @2x)
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT_ICO = Path(__file__).parent / "icon.ico"
OUT_ICNS = Path(__file__).parent / "icon.icns"

# 背景: 白色 squircle (不透明, macOS 規範)
BG = (255, 255, 255, 255)
# logo 線條色: 紫色 #5B21B6 (與 Logo.tsx / favicon.svg 一致)
LOGO = (91, 33, 182, 255)       # #5B21B6
# wick 影線: 同色不透明 (半透明在小尺寸會糊掉)
LOGO_WICK = (91, 33, 182, 255)


def _draw(size: int, sw_b: float, sw_w: float, body_w: float) -> Image.Image:
    """繪製單尺寸圖標 (像素直接映射 32-viewBox)。"""
    factor = max(8, 2048 // size)
    s = size * factor
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 品牌色 squircle 背景 (圓角方塊, macOS Big Sur+ 規範形狀, 不透明)
    # 純色填充, 不用漸變 — 漸變在 16px 小尺寸下看不出, 反而易引入 alpha 合成 bug
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.22), fill=BG)

    def p(v):
        return v * s / 32

    swb = max(1, int(round(sw_b * factor)))
    sww = max(1, int(round(sw_w * factor)))

    # 方括號 [ ]
    for pts in [[(10, 4), (4, 4), (4, 28), (10, 28)], [(22, 4), (28, 4), (28, 28), (22, 28)]]:
        scaled = [(p(x), p(y)) for x, y in pts]
        for i in range(len(scaled) - 1):
            d.line([scaled[i], scaled[i + 1]], fill=LOGO, width=swb, joint="curve")

    # wick 影線 (上短下長)
    d.line([(p(16), p(7)), (p(16), p(25))], fill=LOGO_WICK, width=sww)
    wcap = sww // 2 + 1
    for cy in [7, 25]:
        d.ellipse([p(16) - wcap, p(cy) - wcap, p(16) + wcap, p(cy) + wcap], fill=LOGO_WICK)

    # body 實體 (偏上 → 上影短下影長)
    d.rounded_rectangle(
        [p(16 - body_w / 2), p(9), p(16 + body_w / 2), p(19)],
        radius=p(0.5), fill=LOGO,
    )

    return img.resize((size, size), Image.LANCZOS)


def draw_logo(size: int) -> Image.Image:
    """按尺寸選參數, 保證各尺寸視覺粗細一致。

    核心原則: 所有尺寸線條佔圖標的視覺比例統一, 不能出現"大尺寸反而顯細"。
    基準定為 sw=4.0/32 (12.5%) — 經多輪對比選定, Dock 放大時線條仍清晰有力。
    小尺寸在基準上略加粗, 抵消像素化糊掉; 大尺寸統一用基準, 不遞減。
    """
    if size <= 16:
        return _draw(size, sw_b=4.5, sw_w=3.8, body_w=9)
    elif size <= 32:
        return _draw(size, sw_b=4.2, sw_w=3.6, body_w=8)
    else:
        # ≥48px 統一用基準 4.0, 不隨尺寸遞減
        return _draw(size, sw_b=4.0, sw_w=3.4, body_w=8)


def _save_ico(images_by_size: dict[int, Image.Image]) -> None:
    """Windows .ico (16/32/48/64/128/256)。主圖 256 優先, 資源管理器大圖清晰。"""
    sizes = [16, 32, 48, 64, 128, 256]
    images = [images_by_size[s] for s in sizes]
    images[-1].save(
        OUT_ICO, format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=images[:-1],
    )
    print(f"生成: {OUT_ICO} (尺寸 {sizes})")


def _save_icns(images_by_size: dict[int, Image.Image]) -> None:
    """macOS .icns (16/32/64/128/256/512)。

    Apple 要求 .icns 至少含一張大圖 (512 或 1024) 才合法, 否則 Finder/Dock
    不顯示。這裡用 512 作主圖 (Pillow 12 寫 1024 需額外編碼, 512 已覆蓋
    Retina @2x)。@2x 高清由系統從大圖自動縮放, 無需單獨提供。
    """
    sizes = [16, 32, 64, 128, 256, 512]
    images = [images_by_size[s] for s in sizes]
    images[-1].save(
        OUT_ICNS, format="ICNS",
        append_images=images[:-1],
    )
    print(f"生成: {OUT_ICNS} (尺寸 {sizes})")


def main() -> None:
    # 一次繪製所有用到的尺寸 (ico 和 icns 取並集), 避免重複繪製
    all_sizes = {16, 32, 48, 64, 128, 256, 512}
    images_by_size = {sz: draw_logo(sz) for sz in all_sizes}
    _save_ico(images_by_size)
    _save_icns(images_by_size)


if __name__ == "__main__":
    main()
