"""Certificate follows the official Canva design DAGzIgWU-E4 (page 1 = template)."""
import datetime as dt
import re
from pathlib import Path

from PIL import Image

import autopilot.certificate as cert
from autopilot.certificate import (
    BACKGROUND,
    CANVA_TEMPLATE_DESIGN,
    H,
    W,
    body_lines,
    english_date,
    make_certificate,
    speaker_names,
)
from autopilot.event import load_event

EV = Path(__file__).resolve().parent / "fixtures" / "2026-11-25-candice.yaml"


def test_template_is_official_canva_design():
    assert CANVA_TEMPLATE_DESIGN == "DAGzIgWU-E4"
    assert BACKGROUND.exists()
    assert Image.open(BACKGROUND).size == (W, H)


def test_english_date_matches_official_cards():
    assert english_date(dt.date(2026, 11, 25)) == "November 25, 2026"
    assert english_date(dt.date(2026, 11, 4)) == "November 4, 2026"


def test_speaker_names_split_zh_and_en():
    ev = load_event(EV)
    assert speaker_names(ev) == ("謝慈", "Candice")
    assert speaker_names({"speaker": {"name": "周世恩", "name_zh": "周世恩", "name_en": "Shih-En Chou"}}) == (
        "周世恩", "Shih-En Chou")


def test_other_clubs_are_not_labeled_nycu_and_drop_the_bundled_mark():
    from autopilot.certificate import certificate_view, render_certificate
    ev = load_event(Path(__file__).resolve().parent.parent / "samples" / "demo-club" / "lecture.yaml")
    lines = body_lines(ev)
    assert "NYCU" not in " ".join(lines)
    assert "Demo University Startup Club" in lines[-1]
    im = render_certificate(ev)
    # The bundled rocket sits in this box. A new club's card covers it with paper.
    assert im.getpixel((1630, 2010))[0] > 200
    page = Image.new("RGB", (1200, 800), (240, 240, 240))
    card = Image.new("RGB", (600, 450), (20, 40, 60))
    page.paste(card, (300, 175))
    cropped = certificate_view(page)
    assert abs(cropped.size[0] / cropped.size[1] - 4 / 3) < 0.08
    assert cropped.getpixel((cropped.size[0] // 2, cropped.size[1] // 2)) == (20, 40, 60)


def test_body_uses_official_wording_and_full_title():
    ev = load_event(EV)
    assert body_lines(ev) == [
        "In Appreciation of Your Inspiring Sharing on",
        "“從職涯迷惘、自媒體探索，到一人創業：AI 時代打造不可取代的競爭力”",
        "at NYCU IEC on November 25, 2026",
    ]


def _media_box(pdf: Path):
    m = re.search(br"/MediaBox\s*\[\s*([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)\s*\]", pdf.read_bytes())
    assert m, "PDF is missing a MediaBox"
    return float(m.group(3)), float(m.group(4))


def test_fallback_render_is_canva_page_size(tmp_path):
    ev = load_event(EV)
    pdf, png = tmp_path / "感謝狀.pdf", tmp_path / "感謝狀.png"
    make_certificate(ev, pdf, png_path=png, use_canva=False)
    w, h = _media_box(pdf)
    assert abs(w - 300) < 1 and abs(h - 225) < 1  # 400x300 Canva px
    im = Image.open(png).convert("RGB")
    assert im.size == (W, H)
    # Something was drawn over the blank background in the name and body areas.
    bg = Image.open(BACKGROUND).convert("RGB")
    from PIL import ImageChops
    diff = ImageChops.difference(im, bg).convert("L").point(lambda p: 255 if p > 40 else 0).getbbox()
    assert diff and diff[1] > 1250 and diff[3] < 1950


def test_canva_export_is_copied_when_present(tmp_path, monkeypatch):
    ev = load_event(EV)
    c_pdf, c_png = tmp_path / "certificate.pdf", tmp_path / "certificate.png"
    c_pdf.write_bytes(b"%PDF-1.4 canva-export\n" + b"X" * 50)
    Image.new("RGB", (W, H), "white").save(c_png)
    monkeypatch.setattr(cert, "canva_export_paths", lambda ev: (c_pdf, c_png))
    out, png = tmp_path / "out" / "感謝狀_new.pdf", tmp_path / "out" / "感謝狀_new.png"
    make_certificate(ev, out, png_path=png)
    assert out.read_bytes().startswith(b"%PDF-1.4 canva-export")
    assert png.read_bytes() == c_png.read_bytes()
