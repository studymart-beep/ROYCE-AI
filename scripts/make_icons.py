"""Generate Royce AI icons (forest square, gold up-right arrow). Needs Pillow: pip install pillow.

Writes app/static/icon-192.png, icon-512.png (rounded), icon-maskable-192.png, icon-maskable-512.png
(full-bleed, art kept inside the 80% safe zone).
Re-run after changing the design; outputs are committed so nothing needs Pillow at runtime.
"""
import os
from PIL import Image, ImageDraw

FOREST = (13, 59, 46, 255)
GOLD = (201, 162, 75, 255)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "app", "static")


def arrow(size, pad_frac, rounded):
    """Render at 4x then downsample for clean edges."""
    S = size * 4
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if rounded:
        d.rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.22), fill=FOREST)
    else:
        d.rectangle([0, 0, S - 1, S - 1], fill=FOREST)
    # arrow drawn inside the safe zone (pad_frac of the canvas on each side)
    lo, hi = S * pad_frac, S * (1 - pad_frac)
    w = max(4, int(S * 0.085))
    a, b = (lo + (hi - lo) * 0.18, hi - (hi - lo) * 0.18), (hi - (hi - lo) * 0.18, lo + (hi - lo) * 0.18)
    d.line([a, b], fill=GOLD, width=w)
    head = (hi - (hi - lo) * 0.18, lo + (hi - lo) * 0.18)
    arm = (hi - (hi - lo) * 0.52, lo + (hi - lo) * 0.18)
    arm2 = (hi - (hi - lo) * 0.18, lo + (hi - lo) * 0.52)
    d.line([arm, head, arm2], fill=GOLD, width=w, joint="curve")
    r = w / 2
    for x, y in [a, head]:
        d.ellipse([x - r, y - r, x + r, y + r], fill=GOLD)
    return img.resize((size, size), Image.LANCZOS)


def save(img, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path, "PNG", optimize=True)


if __name__ == "__main__":
    save(arrow(192, 0.12, True), os.path.join(STATIC, "icon-192.png"))
    save(arrow(512, 0.12, True), os.path.join(STATIC, "icon-512.png"))
    save(arrow(192, 0.20, False), os.path.join(STATIC, "icon-maskable-192.png"))
    save(arrow(512, 0.20, False), os.path.join(STATIC, "icon-maskable-512.png"))
    print("icons written")
