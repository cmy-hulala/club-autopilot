"""Feature 11: speaker thank-you certificate (感謝狀).

Format = the club's official Canva certificate design DAGzIgWU-E4 (one page per past
speaker, 400 x 300). The newest speaker page (page 1, 周世恩 Shih-En Chou, 2026-11-04) is the
template: wavy guilloche frame, gold medal, "CERTIFICATE / OF APPRECIATION / Proudly Presented
to", the speaker's Chinese name (heavy serif) next to the English name (script), a dotted rule,
three gold lines

    In Appreciation of Your Inspiring Sharing on
    “<full lecture title>”
    at NYCU IEC on <Month D, YYYY>

and the club rocket logo. No signature, no ROC date.

Two ways to produce it (same idea as canva_step):

* mode "connector" (used for 11/25): Canva's official connector copies template page 1 into a new
  design (the original design is never edited), replaces the Chinese name, English name, title
  and date, re-centres the name pair, and exports a vector PDF. A 3200 x 2400 PNG is rendered from
  that PDF. The files live in <event.canva.exported_dir>/certificate.pdf / certificate.png and
  make_certificate() simply copies them.
* fallback: Pillow re-render on assets/certificate_bg.png, which is the same Canva page exported
  with the three variable text boxes deleted, so frame / medal / headings / rule / logo are pixel
  identical. Fonts approximate Canva's (Source Han Serif Heavy -> Noto Serif TC/CJK, Aniyah
  script -> Great Vibes, Garet -> Montserrat; bundled OFL fonts are in assets/fonts).
"""
from __future__ import annotations

import os
import shutil
from datetime import date
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .event import Event

ROOT = Path(__file__).resolve().parent.parent
BACKGROUND = ROOT / "assets" / "certificate_bg.png"
FONTS = ROOT / "assets" / "fonts"
# Kept for backwards compatibility (old DAGkWpv1Xc8-style frame, no longer used).
FRAME = BACKGROUND

CANVA_TEMPLATE_DESIGN = "DAGzIgWU-E4"


def certificate_template_id() -> str:
    """Local private config can replace the built-in certificate template. The constant stays the default."""
    from .canva_templates import cert_template_id
    return cert_template_id()
CANVA_TEMPLATE_PAGE = 1
CANVA_BLANK_DESIGN = "DAHa96agpic"  # copy of page 1 with the variable texts deleted -> certificate_bg.png
CONNECTOR_STEPS = (
    f"Copy page {CANVA_TEMPLATE_PAGE} of Canva design {CANVA_TEMPLATE_DESIGN} into a NEW design "
    "(copy-design with page_numbers=[1]; never edit the original). In the copy, find-and-replace: "
    "Chinese name text box -> speaker name_zh; script English name box -> English name; inside the "
    "body box replace only the quoted title with the full lecture title and the date after "
    "'at NYCU IEC on' with english_date(ev). Re-centre the name pair on x=200 keeping the original "
    "~2.5 px gap. Title the design '感謝狀｜<speaker>｜<date>'. Export PDF; render PNG 3200x2400 "
    "from the PDF; save as <canva.exported_dir>/certificate.pdf/.png."
)

# Canva page is 400 x 300; we render at 8x.
W, H = 3200, 2400
SCALE = W / 400

PAPER = (255, 254, 234)
NAME_INK = (0, 0, 0)
BODY_INK = (162, 116, 48)

# Name pair (measured on the Canva export of the 11/25 page at 3200 px).
NAME_ZH_INK_TOP = 1334
NAME_ZH_SIZE = 195
NAME_EN_INK_BOTTOM = 1500
NAME_EN_SIZE = 172
NAME_GAP = 28  # ink gap between 謝慈 and Candice
# Body: three centred lines; ink tops of each line, max width = Canva text box width.
BODY_TOPS = (1687, 1748, 1818)
BODY_SIZE = 49
BODY_MAX_W = 199.28 * SCALE

ZH_HEAVY = [
    (r"C:\Windows\Fonts\NotoSerifTC-VF.ttf", 0, b"Black"),
    ("/usr/share/fonts/opentype/noto/NotoSerifCJK-Black.ttc", 3, None),
    ("/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc", 3, None),
    (r"C:\Windows\Fonts\mingliub.ttc", 0, None),
    (r"C:\Windows\Fonts\msjhbd.ttc", 0, None),
]
SCRIPT = [(str(FONTS / "GreatVibes-Regular.ttf"), 0, None)]
LATIN = [(str(FONTS / "Montserrat-VariableFont_wght.ttf"), 0, b"Regular")]
CJK_SANS = [
    (r"C:\Windows\Fonts\NotoSansTC-VF.ttf", 0, b"Regular"),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 3, None),
    (r"C:\Windows\Fonts\msjh.ttc", 0, None),
]
# Old names kept so existing callers/tests importing them don't break.
BOLD, REG = ZH_HEAVY, CJK_SANS

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]


def _font(cands, size):
    for path, idx, var in cands:
        if os.path.exists(path):
            try:
                f = ImageFont.truetype(path, size, index=idx)
                if var:
                    try:
                        f.set_variation_by_name(var)
                    except Exception:
                        pass
                return f
            except OSError:
                continue
    return ImageFont.load_default(size)


def english_date(d: date) -> str:
    """Date as on the official card: 'November 25, 2026' (no zero padding)."""
    return f"{MONTHS[d.month - 1]} {d.day}, {d.year}"


def roc_date(d: date) -> str:
    """ROC date (old DAGkWpv1Xc8 format); kept for other callers, not printed on the new card."""
    return f"中華民國 {d.year - 1911} 年 {d.month} 月 {d.day} 日"


def speaker_names(ev: Event | dict) -> tuple[str, str]:
    """(Chinese name, English name). English is '' when the event has none."""
    sp = ev["speaker"] if not isinstance(ev, dict) else ev.get("speaker", ev)
    zh = (sp.get("name_zh") or "").strip()
    name = (sp.get("name") or zh).strip()
    en = (sp.get("name_en") or "").strip()
    if not en and zh and name.startswith(zh):
        en = name[len(zh):].strip()
    if not zh:
        zh, en = name, en
    return zh, en


def speaker_line(ev: Event | dict) -> str:
    zh, en = speaker_names(ev)
    return f"{zh} {en}".strip()


def lecture_title(ev: Event) -> str:
    return (ev.raw.get("certificate_title") or ev.raw.get("title") or ev["title"]).strip()


def body_lines(ev: Event) -> list[str]:
    return [
        "In Appreciation of Your Inspiring Sharing on",
        f"“{lecture_title(ev)}”",
        f"at NYCU IEC on {english_date(ev['date_obj'])}",
    ]


def canva_export_paths(ev: Event) -> tuple[Path, Path]:
    canva = ev.raw.get("canva") or {}
    d = canva.get("exported_dir") or f"assets/canva_export/{ev.raw['date']}"
    base = Path(d) if Path(d).is_absolute() else ROOT / d
    return base / "certificate.pdf", base / "certificate.png"


# ---------------- Pillow fallback ----------------

def _mixed_runs(text: str):
    """Split into (is_ascii, chunk) runs so Latin uses Montserrat and CJK uses Noto Sans."""
    runs: list[tuple[bool, str]] = []
    for ch in text:
        a = ch.isascii()
        if runs and runs[-1][0] == a:
            runs[-1] = (a, runs[-1][1] + ch)
        else:
            runs.append((a, ch))
    return runs


def _line_width(text: str, latin, cjk) -> float:
    return sum((latin if a else cjk).getlength(s) for a, s in _mixed_runs(text))


def _line_ink(text: str, latin, cjk) -> tuple[float, float]:
    """(top, bottom) of the line's ink relative to the baseline."""
    top, bot = 0.0, 0.0
    for a, s in _mixed_runs(text):
        if not s.strip():
            continue
        b = (latin if a else cjk).getbbox(s, anchor="ls")
        top, bot = min(top, b[1]), max(bot, b[3])
    return top, bot


def _draw_body_line(d: ImageDraw.ImageDraw, text: str, ink_top: float, size: int) -> None:
    latin, cjk = _font(LATIN, size), _font(CJK_SANS, size)
    w = _line_width(text, latin, cjk)
    if w > BODY_MAX_W:  # long title: shrink to stay on one line inside the Canva box
        size = max(24, int(size * BODY_MAX_W / w))
        latin, cjk = _font(LATIN, size), _font(CJK_SANS, size)
        w = _line_width(text, latin, cjk)
    top, _ = _line_ink(text, latin, cjk)
    y = ink_top - top
    x = W / 2 - w / 2
    for a, s in _mixed_runs(text):
        f = latin if a else cjk
        d.text((x, y), s, font=f, fill=BODY_INK, anchor="ls")
        x += f.getlength(s)


def _compose(ev: Event) -> Image.Image:
    if BACKGROUND.exists():
        im = Image.open(BACKGROUND).convert("RGB")
        if im.size != (W, H):
            im = im.resize((W, H), Image.Resampling.LANCZOS)
    else:
        im = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(im)

    zh, en = speaker_names(ev)
    zf = _font(ZH_HEAVY, NAME_ZH_SIZE)
    ef = _font(SCRIPT, NAME_EN_SIZE)
    zb = zf.getbbox(zh, anchor="ls")
    eb = ef.getbbox(en, anchor="ls") if en else (0, 0, 0, 0)
    zw = zb[2] - zb[0]
    ew = (eb[2] - eb[0]) if en else 0
    total = zw + (NAME_GAP + ew if en else 0)
    x0 = W / 2 - total / 2
    d.text((x0 - zb[0], NAME_ZH_INK_TOP - zb[1]), zh, font=zf, fill=NAME_INK, anchor="ls")
    if en:
        d.text((x0 + zw + NAME_GAP - eb[0], NAME_EN_INK_BOTTOM - eb[3]), en, font=ef, fill=NAME_INK, anchor="ls")

    for top, line in zip(BODY_TOPS, body_lines(ev)):
        _draw_body_line(d, line, top, BODY_SIZE)
    return im


def render_certificate(ev: Event) -> Image.Image:
    return _compose(ev)


def make_certificate(ev: Event, out_path: Path, signer: str | None = None,
                     png_path: Path | None = None, use_canva: bool = True) -> Path:
    """Write the certificate PDF (and optional PNG).

    If the Canva connector export for this event exists it is copied as-is (official file);
    otherwise the Pillow fallback renders the same layout. `signer` is ignored (no signature
    on the official card) and kept only for compatibility.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    c_pdf, c_png = canva_export_paths(ev)
    if use_canva and c_pdf.is_file() and out_path.suffix.lower() == ".pdf":
        shutil.copyfile(c_pdf, out_path)
        if png_path is not None:
            Path(png_path).parent.mkdir(parents=True, exist_ok=True)
            if c_png.is_file():
                shutil.copyfile(c_png, png_path)
            else:
                _compose(ev).save(png_path, "PNG")
        return out_path
    im = _compose(ev)
    if out_path.suffix.lower() == ".png":
        im.save(out_path, "PNG")
    else:
        # 400 x 300 Canva px == 300 x 225 pt, same page size as Canva's PDF export.
        im.save(out_path, "PDF", resolution=W / (300 / 72), title=f"感謝狀｜{speaker_line(ev)}")
    if png_path is not None:
        Path(png_path).parent.mkdir(parents=True, exist_ok=True)
        im.save(png_path, "PNG")
    return out_path
