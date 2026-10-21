"""從 packaging/morandi/app-icon.png 產生 PWA 圖示到 frontend/public/icons/。

maskable 版縮到 80% 置中: Android 遮罩只保證中央安全區可見。
邊距填原圖外緣平均色, 圖示本身是滿版深藍底, 遮罩邊緣才不會多一圈色框。
運行: cd backend; uv run python ../packaging/generate_pwa_icons.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "packaging" / "morandi" / "app-icon.png"
OUT_DIR = ROOT / "frontend" / "public" / "icons"
MASKABLE_SIZE = 512
MASKABLE_CONTENT_RATIO = 0.8


def _edge_color(img: Image.Image) -> tuple[int, int, int]:
    w, h = img.size
    edge = [img.getpixel((x, y)) for x in range(w) for y in (0, h - 1)]
    edge += [img.getpixel((x, y)) for y in range(h) for x in (0, w - 1)]
    return tuple(round(sum(px[i] for px in edge) / len(edge)) for i in range(3))


def main() -> None:
    src = Image.open(SOURCE).convert("RGB")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for size in (192, 512):
        src.resize((size, size), Image.Resampling.LANCZOS).save(OUT_DIR / f"icon-{size}.png", optimize=True)

    inner = round(MASKABLE_SIZE * MASKABLE_CONTENT_RATIO)
    offset = (MASKABLE_SIZE - inner) // 2
    maskable = Image.new("RGB", (MASKABLE_SIZE, MASKABLE_SIZE), _edge_color(src))
    maskable.paste(src.resize((inner, inner), Image.Resampling.LANCZOS), (offset, offset))
    maskable.save(OUT_DIR / f"icon-maskable-{MASKABLE_SIZE}.png", optimize=True)


if __name__ == "__main__":
    main()
