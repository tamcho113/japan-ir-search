"""Generate OGP image (1200x630) for japan-ir-search LP."""
from __future__ import annotations
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "docs" / "landing" / "assets" / "og.png"

# Colors (from LP)
BG = (250, 250, 247)
INK = (26, 26, 26)
MUTED = (102, 102, 102)
ACCENT = (15, 108, 189)
HIGHLIGHT = (253, 230, 138)
BETA_BG = (254, 243, 199)
BETA_FG = (146, 64, 14)
BETA_BORDER = (252, 211, 77)
BORDER = (229, 229, 229)

W, H = 1200, 630

FONT_BOLD = "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc"
FONT_REG = "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc"
FONT_HEAVY = "/System/Library/Fonts/ヒラギノ角ゴシック W8.ttc"
FONT_MONO = "/System/Library/Fonts/Menlo.ttc"


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def main() -> None:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    # Subtle top border accent stripe
    d.rectangle([0, 0, W, 6], fill=ACCENT)

    pad_x = 80
    y = 90

    # Beta badge
    badge_text = "BETA · 認証不要"
    bf = font(FONT_BOLD, 22)
    bbox = d.textbbox((0, 0), badge_text, font=bf)
    bw, bh = bbox[2] - bbox[0], bbox[3] - bbox[1]
    bx0, by0 = pad_x, y
    bx1, by1 = bx0 + bw + 32, by0 + bh + 18
    d.rounded_rectangle([bx0, by0, bx1, by1], radius=999, fill=BETA_BG, outline=BETA_BORDER, width=2)
    d.text((bx0 + 16, by0 + 6), badge_text, font=bf, fill=BETA_FG)
    y = by1 + 28

    # Title line 1: "EDINET 全文検索"
    title_f = font(FONT_HEAVY, 92)
    title1 = "EDINET 全文検索"
    # Highlight band behind「全文検索」
    pre = "EDINET "
    pre_w = d.textbbox((0, 0), pre, font=title_f)[2]
    target = "全文検索"
    tg_bbox = d.textbbox((0, 0), target, font=title_f)
    tg_w, tg_h = tg_bbox[2], tg_bbox[3]
    band_top = y + int(tg_h * 0.55)
    band_bot = y + tg_h + 18
    d.rectangle([pad_x + pre_w - 4, band_top, pad_x + pre_w + tg_w + 8, band_bot], fill=HIGHLIGHT)
    d.text((pad_x, y), title1, font=title_f, fill=INK)
    y += 110

    # Title line 2
    sub_f = font(FONT_BOLD, 50)
    d.text((pad_x, y), "MCP サーバー", font=sub_f, fill=INK)
    y += 78

    # Lead text
    lead_f = font(FONT_REG, 30)
    d.text((pad_x, y), "Claude / VS Code / Cursor から、有報・四半期報告書を", font=lead_f, fill=MUTED)
    y += 42
    d.text((pad_x, y), "AI と一緒にすぐ検索。", font=lead_f, fill=MUTED)

    # Stats row (bottom)
    stats_y = 470
    stat_num_f = font(FONT_HEAVY, 56)
    stat_lbl_f = font(FONT_REG, 22)

    stats = [
        ("55,131", "件の書類"),
        ("6,984", "社"),
        ("2 年分", "2024-2026"),
    ]
    col_w = (W - pad_x * 2) // 3
    for i, (num, lbl) in enumerate(stats):
        cx = pad_x + col_w * i
        d.text((cx, stats_y), num, font=stat_num_f, fill=ACCENT)
        d.text((cx, stats_y + 70), lbl, font=stat_lbl_f, fill=MUTED)

    # Footer URL
    url_f = font(FONT_BOLD, 24)
    url = "japan-ir-search.pages.dev"
    url_w = d.textbbox((0, 0), url, font=url_f)[2]
    d.text((W - pad_x - url_w, H - 50), url, font=url_f, fill=INK)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT, "PNG")
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
