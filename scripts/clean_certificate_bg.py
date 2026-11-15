"""Replace the watermarked Canva medal in assets/certificate_bg.png with the clean redraw.

The background was exported from a copy of DAGzIgWU-E4 (400x300 design units); the medal box there is
left 186.48, top 43.52, 34.37 x 42.96. Back the file up before running (done by the caller).
usage: python scripts/clean_certificate_bg.py assets/certificate_bg.png
"""
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_clean_medal import draw  # noqa: E402

BOX = (186.48, 43.52, 34.37, 42.96)


def clean(path: Path) -> None:
    im = Image.open(path).convert("RGB")
    s = im.width / 400
    l, t, w, h = (round(v * s) for v in BOX)
    pad = round(4 * s)
    bg = im.getpixel((l - pad - 5, t + h // 2))
    patch = Image.new("RGB", (w + 2 * pad, h + 2 * pad), bg)
    im.paste(patch, (l - pad, t - pad))
    medal = draw(800).resize((w, h), Image.LANCZOS)
    im.paste(medal, (l, t), medal)
    im.save(path)


if __name__ == "__main__":
    clean(Path(sys.argv[1]))
    print("cleaned", sys.argv[1])
