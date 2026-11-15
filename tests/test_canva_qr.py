from pathlib import Path

from autopilot import canva_step
from autopilot.event import load_event

EV = Path(__file__).resolve().parent / "fixtures" / "2026-11-25-candice.yaml"
SIGNUP = "https://docs.google.com/forms/d/e/1FAIpQLSFAKEFORMID-signup-0000000000000000000000000000/viewform"


def _decode(path: Path) -> str | None:
    try:
        import cv2
        data, _, _ = cv2.QRCodeDetector().detectAndDecode(cv2.imread(str(path)))
        if data:
            return data
    except Exception:
        pass
    return None


def test_qr_skipped_without_form_url(tmp_path):
    ev = load_event(EV)
    assert canva_step.real_signup_url(ev) is None
    result = canva_step.run(ev, tmp_path / "ig", mode="connector")
    assert result["qr"]["ok"] is False
    assert result["qr"]["skipped"]
    assert result["qr"]["page"] == 4
    assert "update_fill" in result["qr"]["connector_steps"]
    assert not (tmp_path / "ig" / canva_step.QR_PNG_NAME).exists()


def test_qr_png_decodes_to_signup_url(tmp_path):
    ev = load_event(EV, {"form_url": SIGNUP, "form_short_url": SIGNUP})
    assert canva_step.real_signup_url(ev) == SIGNUP
    out = tmp_path / "ig"
    result = canva_step.run(ev, out, mode="connector")
    assert result["qr"]["ok"] is True
    assert result["qr"]["url"] == SIGNUP
    assert result["qr"]["page"] == 4
    png = out / canva_step.QR_PNG_NAME
    assert png.exists() and png.stat().st_size > 0
    data = _decode(png)
    if data is None:
        import pytest
        pytest.skip("no QR decoder (OpenCV) installed")
    assert data == SIGNUP


def test_ig_title_lines_breaks_at_colon():
    ev = load_event(EV)
    assert canva_step.ig_title_lines(ev) == "從職涯迷惘到一人創業：\nAI 時代打造不可取代的競爭力"
    assert ev["title"] != canva_step.ig_title_lines(ev)


def test_ig_title_lines_fallback_without_short_title(tmp_path):
    ev = load_event(EV, {"short_title": "", "title": "上半段標題：下半段副標"})
    assert canva_step.ig_title_lines(ev) == "上半段標題：\n下半段副標"
    ev2 = load_event(EV, {"short_title": "", "title": "沒有冒號的超長標題需要自動斷行處理一下"})
    lines = canva_step.ig_title_lines(ev2, max_line_chars=16)
    assert lines == "沒有冒號的超長標題需要自動斷行處\n理一下"
    ev3 = load_event(EV, {"short_title": "", "title": "短標"})
    assert canva_step.ig_title_lines(ev3) == "短標"
    result = canva_step.run(load_event(EV), tmp_path / "ig", mode="connector")
    assert result["title_lines"] == "從職涯迷惘到一人創業：\nAI 時代打造不可取代的競爭力"


def test_update_qr_is_dashboard_entry_point(tmp_path):
    # The Flask dashboard looks up canva_step.update_qr (then aliases) before run().
    assert callable(getattr(canva_step, "update_qr"))
    ev = load_event(EV)
    res = canva_step.update_qr(ev, tmp_path / "qr")
    assert res["ok"] is False
    assert res["skipped"]


def test_update_qr_writes_png_and_stamps_card(tmp_path):
    from PIL import Image

    export = tmp_path / "export"
    export.mkdir()
    Image.new("RGB", (1080, 1350), "white").save(export / "ig_04.png")
    Image.new("RGB", (1080, 1350), "white").save(export / "ig_01.png")
    ig01_before = (export / "ig_01.png").read_bytes()
    ev = load_event(EV, {"form_url": SIGNUP, "form_short_url": SIGNUP})
    ev.raw.setdefault("canva", {})["exported_dir"] = str(export)
    dest = tmp_path / "qr"
    res = canva_step.update_qr(ev, dest)
    assert res["ok"] is True
    assert res["url"] == SIGNUP
    assert (dest / canva_step.QR_PNG_NAME).is_file()
    assert res.get("card") == "ig_04.png"
    assert (dest / "ig_04.png").is_file()
    data = _decode(dest / "ig_04.png")
    if data is None:
        import pytest
        pytest.skip("no QR decoder (OpenCV) installed")
    assert data == SIGNUP
    # gallery card is replaced because the blank card had no QR
    assert _decode(export / "ig_04.png") == SIGNUP
    # other pages are left alone
    assert (export / "ig_01.png").read_bytes() == ig01_before


def test_apply_qr_covers_tilted_frame(tmp_path):
    # The template's QR frame is tilted ~3°; no placeholder green may show after stamping.
    import numpy as np
    from PIL import Image, ImageDraw
    import math

    fr = canva_step.DEFAULT_QR_FRAME
    (cx, cy), (w, h), ang = fr["center"], fr["size"], math.radians(fr["angle"])
    corners = []
    for dx, dy in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)):
        corners.append((cx + dx * math.cos(ang) - dy * math.sin(ang), cy + dx * math.sin(ang) + dy * math.cos(ang)))
    card = Image.new("RGB", (1080, 1350), "white")
    ImageDraw.Draw(card).polygon(corners, fill=(0, 200, 0))
    card.save(tmp_path / "card.png")
    canva_step.write_qr_png(SIGNUP, tmp_path / "qr.png")
    out = canva_step.apply_qr_to_card(tmp_path / "card.png", tmp_path / "qr.png", tmp_path / "out.png")
    px = np.asarray(Image.open(out).convert("RGB")).astype(int)
    green = (px[..., 1] > 150) & (px[..., 0] < 100) & (px[..., 2] < 100)
    assert green.sum() == 0
    data = _decode(out)
    if data is not None:
        assert data == SIGNUP


def test_update_qr_restamps_from_pristine_template(tmp_path):
    from PIL import Image

    export = tmp_path / "export"
    export.mkdir()
    Image.new("RGB", (1080, 1350), (0, 200, 0)).save(export / "ig_04.png")
    pristine = (export / "ig_04.png").read_bytes()
    ev = load_event(EV, {"form_url": SIGNUP, "form_short_url": SIGNUP})
    ev.raw.setdefault("canva", {})["exported_dir"] = str(export)
    canva_step.update_qr(ev, tmp_path / "qr")
    tpl = export / canva_step.QR_TEMPLATE_NAME
    assert tpl.read_bytes() == pristine
    first = (export / "ig_04.png").read_bytes()
    canva_step.update_qr(ev, tmp_path / "qr2")
    # a second run stamps from the template again, so the card is unchanged
    assert (export / "ig_04.png").read_bytes() == first
    assert tpl.read_bytes() == pristine
