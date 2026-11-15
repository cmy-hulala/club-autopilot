"""Draw a clean (watermark-free) gold rosette medal matching the certificate template's look.

The template's stock medal exports with a "Canva" watermark on this account, so the Canva step swaps
in this drawing (uploaded once to the club's Canva uploads; see config/canva_clean_assets.yaml).
usage: python scripts/make_clean_medal.py OUT.png [WIDTH]
"""
import math
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

GOLD_DARK = np.array([168, 112, 22], float)
GOLD = np.array([222, 170, 52], float)
GOLD_LIGHT = np.array([255, 233, 150], float)


def _mix(a, b, t):
    return a + (b - a) * t[..., None]


def conic(w, h, cx, cy, rays=6):
    y, x = np.mgrid[0:h, 0:w]
    ang = np.arctan2(y - cy, x - cx)
    t = (np.cos(ang * rays) + 1) / 2          # alternating light/dark rays like brushed gold
    t = t ** 1.6
    return _mix(GOLD, GOLD_LIGHT, t)


def linear(w, h, top, bottom):
    t = np.linspace(0, 1, h)[:, None].repeat(w, 1)
    return _mix(top, bottom, t)


def star_points(cx, cy, r_out, r_in, n=10):
    pts = []
    for i in range(n * 2 * 6):
        a = -math.pi / 2 + i * math.pi / (n * 6)
        # smooth scallop between tips
        k = (math.cos(a * n) + 1) / 2
        r = r_in + (r_out - r_in) * k ** 0.7
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def draw(width=800):
    S = 3
    W, H = width * S, int(width * 1.25) * S
    cx, cy = W / 2, H * 0.40
    R = W * 0.46
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))

    # ribbons (behind the rosette)
    rib = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(rib)
    bw = W * 0.20
    for side in (-1, 1):
        x0 = cx + side * W * 0.10
        top = cy + R * 0.55
        bot = H * 0.97
        x1 = x0 + side * W * 0.16
        poly = [(x0 - bw / 2, top), (x0 + bw / 2, top), (x1 + bw / 2, bot),
                (x1, bot - H * 0.07), (x1 - bw / 2, bot)]
        d.polygon(poly, fill=255)
    ribbon_col = Image.fromarray(linear(W, H, GOLD_LIGHT * 0.95, GOLD_DARK).astype("uint8"))
    canvas.paste(ribbon_col, (0, 0), rib)

    # scalloped rosette
    ros = Image.new("L", (W, H), 0)
    ImageDraw.Draw(ros).polygon(star_points(cx, cy, R, R * 0.86), fill=255)
    ros_col = Image.fromarray(_mix(GOLD_LIGHT, GOLD_DARK, np.clip(
        (np.mgrid[0:H, 0:W][0] - (cy - R)) / (2 * R), 0, 1)).astype("uint8"))
    shadow = Image.new("RGBA", (W, H), (120, 80, 10, 90))
    canvas.paste(shadow, (int(W * 0.006), int(W * 0.01)), ros.filter(ImageFilter.GaussianBlur(W * 0.01)))
    canvas.paste(ros_col, (0, 0), ros)

    # outer ring + inner brushed disc
    def disc(r, img):
        m = Image.new("L", (W, H), 0)
        ImageDraw.Draw(m).ellipse((cx - r, cy - r, cx + r, cy + r), fill=255)
        canvas.paste(img, (0, 0), m)
    disc(R * 0.80, Image.fromarray(linear(W, H, GOLD_DARK, GOLD_LIGHT).astype("uint8")))
    disc(R * 0.74, Image.fromarray(linear(W, H, GOLD_LIGHT, GOLD_DARK).astype("uint8")))
    disc(R * 0.70, Image.fromarray(conic(W, H, cx, cy).astype("uint8")))

    return canvas.resize((width, int(width * 1.25)), Image.LANCZOS)


if __name__ == "__main__":
    out = sys.argv[1]
    w = int(sys.argv[2]) if len(sys.argv) > 2 else 800
    draw(w).save(out)
    print(out)
