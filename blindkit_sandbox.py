#!/usr/bin/env python3
"""
QR with repeated human-readable label around all four borders.
- Exact physical output size (e.g., 3 cm × 3 cm) at specified DPI
- 4× supersampling + LANCZOS downscale for sharp text
- Corner-safe tiling, dynamic margins, repeat spacing
"""

import qrcode
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path
import math

# ---------------------- PHYSICAL TARGET ----------------------
TARGET_CM = 3.0        # final sticker width/height in centimeters
DPI       = 900        # printer DPI (203 / 300 / 600, etc.)
SCALE     = 4          # supersampling factor (2–4 typical; 4 is very sharp)
# -------------------------------------------------------------

# ---------------------- CONTENT ----------------------
DATA  = "ABCD"
LABEL = "RAT001PHYS :: ABCD"
# ------------------------------------------------------

# ---------------------- AESTHETICS ----------------------
# Base (pre-scale) sizes; code multiplies by SCALE internally
# Good starting points for a 3 cm sticker at 300 dpi:
FONT_SIZE_BASE = 9      # final text height ~18 px → legible at 3 cm; adjust if needed
INNER_GAP      = 5       # QR ↔ text (tight but safe)
OUTER_GAP      = 4       # text ↔ outer canvas edge
REPEAT_GAP     = 3      # spacing between repeated labels
CORNER_GAP     = 1       # extra breathing room at corners
# ------------------------------------------------------

# ---------------------- FONT PICK ----------------------
FONT_PATHS = [
    "/usr/share/fonts/truetype/jetbrains-mono/JetBrainsMono-Regular.ttf",
    "/Library/Fonts/JetBrainsMono-Regular.ttf",
    "C:/Windows/Fonts/JetBrainsMono-Regular.ttf",
    "C:/Windows/Fonts/consola.ttf",                        # Consolas (Windows)
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", # DejaVu
]
# ------------------------------------------------------

# ---------------------- QR PARAMS ----------------------
# Keep quiet border >= 4 modules for robust scanning
QR_VERSION   = 2       # version will auto-increase if DATA needs it (fit=True)
BOX_SIZE     = 7       # base module pixels (before SCALE); adjust if needed
QUIET_BORDER = 4
# ------------------------------------------------------

def cm_to_px(cm, dpi):
    return int(round((cm / 2.54) * dpi))

def load_font(paths, size_px):
    for p in paths:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size_px)
            except Exception:
                pass
    return ImageFont.load_default()

def make_qr(data, version, box_size, border):
    qr = qrcode.QRCode(
        version=version,                      # will be increased by fit=True if needed
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=box_size,
        border=border,
    )
    qr.add_data(data)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").convert("RGBA")

def text_sprite(label, font, bleed=2):
    """Tight text sprite with baseline offset (no clipping)."""
    tmp = Image.new("RGBA", (1, 1))
    d = ImageDraw.Draw(tmp)
    left, top, right, bottom = d.textbbox((0, 0), label, font=font)
    w = (right - left) + 2 * bleed
    h = (bottom - top) + 2 * bleed
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.text((bleed - left, bleed - top), label, font=font, fill=(0, 0, 0, 255))
    return img

def tile_horizontal(canvas, sprite, y, gap_px=12, x_start=0, x_end=None):
    """Repeat sprite left→right across [x_start, x_end). Corner-safe via bounds."""
    W, _ = canvas.size
    if x_end is None:
        x_end = W
    step = sprite.width + gap_px
    origin = 0
    first = x_start + ((-(x_start - origin)) % step)
    x = first
    while x + sprite.width <= x_end:
        canvas.alpha_composite(sprite, (x, y))
        x += step

def tile_vertical(canvas, sprite_rot, x, gap_px=12, y_start=0, y_end=None):
    """Repeat sprite top→bottom across [y_start, y_end). Corner-safe via bounds."""
    _, H = canvas.size
    if y_end is None:
        y_end = H
    step = sprite_rot.height + gap_px
    origin = 0
    first = y_start + ((-(y_start - origin)) % step)
    y = first
    while y + sprite_rot.height <= y_end:
        canvas.alpha_composite(sprite_rot, (x, y))
        y += step

def build_qr_with_border_labels(
    data, label, font,
    inner_gap, outer_gap, repeat_gap, corner_gap,
    qr_version, box_size, quiet_border
):
    # 1) QR
    qr_img = make_qr(data, qr_version, box_size, quiet_border)

    # 2) Label sprites
    sprite = text_sprite(label, font, bleed=2)
    left_sprite  = sprite.rotate(90,  expand=True)
    right_sprite = sprite.rotate(-90, expand=True)

    # 3) Margin from measured sprite (text height) + gaps
    label_margin = sprite.height + inner_gap + outer_gap

    # 4) Canvas & place QR
    W, H = qr_img.size
    canvas = Image.new("RGBA", (W + 2 * label_margin, H + 2 * label_margin), (255, 255, 255, 255))
    canvas.alpha_composite(qr_img, (label_margin, label_margin))

    # 5) Edge positions
    top_y    = outer_gap
    bottom_y = canvas.height - sprite.height - outer_gap
    left_x   = outer_gap
    right_x  = canvas.width - right_sprite.width - outer_gap

    # 6) Corner guards (avoid collisions)
    left_guard   = outer_gap + left_sprite.width  + corner_gap
    right_guard  = canvas.width - (outer_gap + right_sprite.width + corner_gap)
    top_guard    = outer_gap + sprite.height + corner_gap
    bottom_guard = canvas.height - (outer_gap + sprite.height + corner_gap)

    # 7) Tile labels
    tile_horizontal(canvas, sprite, y=top_y,    gap_px=repeat_gap, x_start=left_guard,  x_end=right_guard)
    tile_horizontal(canvas, sprite, y=bottom_y, gap_px=repeat_gap, x_start=left_guard,  x_end=right_guard)
    tile_vertical(  canvas, left_sprite,  x=left_x,  gap_px=repeat_gap, y_start=top_guard,    y_end=bottom_guard)
    tile_vertical(  canvas, right_sprite, x=right_x, gap_px=repeat_gap, y_start=top_guard,    y_end=bottom_guard)

    return canvas

def main():
    # Compute exact target pixels and superscaled working size
    target_px = cm_to_px(TARGET_CM, DPI)       # final width=height in pixels
    work_px   = target_px * SCALE

    # Load font at superscaled size
    font = load_font(FONT_PATHS, FONT_SIZE_BASE * SCALE)

    # Build at high resolution (all distances scaled)
    img_hi = build_qr_with_border_labels(
        DATA, LABEL, font,
        inner_gap = INNER_GAP   * SCALE,
        outer_gap = OUTER_GAP   * SCALE,
        repeat_gap= REPEAT_GAP  * SCALE,
        corner_gap= CORNER_GAP  * SCALE,
        qr_version= QR_VERSION,
        box_size  = BOX_SIZE    * SCALE,   # QR modules scale too
        quiet_border = QUIET_BORDER,
    )

    # If the hi-res image isn't exactly work_px, center-crop or pad to square work_px
    # (Usually close already, but we force exact so downscale hits 3 cm precisely)
    W, H = img_hi.size
    # First, resize proportionally so the smallest side == work_px,
    # then center-crop or pad to exact square work_px×work_px.
    scale_factor = work_px / min(W, H)
    newW = int(round(W * scale_factor))
    newH = int(round(H * scale_factor))
    img_hi = img_hi.resize((newW, newH), resample=Image.LANCZOS)

    # Center-crop or pad to exact square
    left   = (newW - work_px) // 2
    top    = (newH - work_px) // 2
    right  = left + work_px
    bottom = top + work_px

    if newW >= work_px and newH >= work_px:
        img_hi = img_hi.crop((left, top, right, bottom))
    else:
        # pad if smaller (unlikely with current settings)
        canvas = Image.new("RGBA", (work_px, work_px), (255, 255, 255, 255))
        canvas.alpha_composite(img_hi, ((work_px - newW)//2, (work_px - newH)//2))
        img_hi = canvas

    # Downscale to exact target (3 cm @ DPI)
    img = img_hi.resize((target_px, target_px), resample=Image.LANCZOS)

    # Save with DPI metadata
    out = f"qr_border_3cm_{DPI}dpi.png"
    img.save(out, dpi=(DPI, DPI))
    print(f"Saved {out} — exact size: {TARGET_CM} cm × {TARGET_CM} cm at {DPI} dpi ({target_px}×{target_px}px)")

if __name__ == "__main__":
    main()
