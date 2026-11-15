"""Feature 5: Canva IG cards in the main pipeline.

The pre-hackathon version drove a logged-in Chrome with Playwright (canva_download_ig.py) and broke
whenever Canva's UI moved. This version never touches the Canva UI:

* mode "connector" (used for the 11/25 demo): Canva's official AI connector duplicated the club IG
  template (event.canva.template_design_id), uploaded the speaker photo pulled from Notion as an asset,
  swapped it into the photo frame, replaced the text and exported PNGs. When a real 課前 form URL is
  available, the step also generates a QR PNG for that URL. The connector then uploads it and replaces
  the QR image on page 4 of the working copy only (never the template), keeping position and size, and
  exports page 4 as ig_04.png with no watermark. The pipeline picks the exported PNGs up from
  event.canva.exported_dir and records the design link.
* mode "api": Canva Connect REST API with CANVA_ACCESS_TOKEN: upload the photo (and the signup QR) as
  assets, run an autofill job if a brand template is configured (Canva Teams/Enterprise), then export
  PNGs. Element-level QR replace is not in the public Connect REST API; the asset is uploaded and the
  same connector / Design Editing steps apply unless a brand-template autofill field is set.
"""
from __future__ import annotations

import json
import os
import shutil
import time
import urllib.request
from pathlib import Path

from .event import Event

API = "https://api.canva.com/rest/v1"
ROOT = Path(__file__).resolve().parent.parent
QR_PAGE = 4
QR_PNG_NAME = "qr_signup.png"
# Page-4 QR frame on the 1080x1350 club IG card (left, top, width, height).
DEFAULT_QR_BOX = (356.515, 517.374, 432.617, 417.021)
# The template's QR image frame is tilted ~3° clockwise (cv2.minAreaRect on the Canva export):
# centre (x, y), size (w, h), angle in degrees. Pasting an axis-aligned QR leaves the green
# placeholder showing at the corners, so the whole tilted frame is covered with a white tile
# and the QR is rotated to match.
DEFAULT_QR_FRAME = {"center": (572.45, 725.44), "size": (433.2, 417.4), "angle": 3.02}
QR_TEMPLATE_NAME = "ig_04_template.png"
CONNECTOR_QR_STEPS = (
    "When a real 課前 form responder URL is available (after forms are created, or in preview if "
    "the event or a previous run's state already has form_url): generate a QR PNG for that URL; "
    "upload it as a Canva asset; in the working-copy design only (event.canva.design_id — never "
    "the template event.canva.template_design_id), replace the existing QR image on page 4 with "
    "update_fill, keeping the same position and size; set the page-4 title to ig_title_lines(ev) "
    "(explicit two-line break), widen the text box or shrink the font so each line stays on one "
    "row with a clear gap above the QR; export page 4 only as ig_04.png with no watermark. "
    "Leave pages 1–3 untouched."
)


def _req(method, path, token, body=None, headers=None, data=None):
    h = {"Authorization": f"Bearer {token}"}
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    r = urllib.request.Request(API + path, data=data, method=method, headers=h)
    with urllib.request.urlopen(r, timeout=60) as resp:
        return json.loads(resp.read() or b"{}")


def _poll(path, token, key):
    for _ in range(60):
        j = _req("GET", path, token)[key]
        if j["status"] != "in_progress":
            return j
        time.sleep(2)
    raise TimeoutError(path)



def ig_title_lines(ev: Event, max_line_chars: int = 16) -> str:
    """IG card title as two lines, so a long title does not overflow the card or the QR.

    Prefers ``short_title`` + the subtitle after the colon in ``title``. Falls back to
    splitting the full title at a fullwidth or ASCII colon. If there is no colon and the
    title is longer than *max_line_chars*, it is broken at that length. Connector and API
    paths should set this exact string (with a newline) and, if needed, widen the text box
    or shrink the font so each line stays on one row.
    """
    short = (ev["short_title"] or "").strip()
    full = (ev["title"] or "").strip()
    sub = (ev.ctx.get("title_sub") or "").strip()
    if short and sub:
        line1, line2 = f"{short}：", sub
    else:
        sep = "：" if "：" in full else (":" if ":" in full else "")
        if sep:
            a, b = full.split(sep, 1)
            line1, line2 = a.strip() + sep, b.strip()
        elif max_line_chars and len(full) > max_line_chars:
            line1, line2 = full[:max_line_chars], full[max_line_chars:]
        else:
            return full
    # If line 1 is still too long (e.g. short_title missing and title_main is long), shrink it
    # by using short_title when available; otherwise the connector should also reduce font size.
    if short and line2 and len(line1) > max_line_chars + 1:
        line1 = f"{short}："
    return f"{line1}\n{line2}" if line2 else line1


def real_signup_url(ev: Event) -> str | None:
    """Return the 課前 form URL if it is a real http(s) address, not a placeholder."""
    url = (ev["form_url"] or ev.raw.get("form_url") or "").strip()
    if not url or "{{" in url:
        return None
    if not url.startswith(("http://", "https://")):
        return None
    return url


def write_qr_png(url: str, dest: Path) -> Path:
    """Write a high-resolution black-on-white QR PNG that encodes *url*."""
    import qrcode
    from qrcode.constants import ERROR_CORRECT_M

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_M, box_size=16, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    if hasattr(img, "convert"):
        img = img.convert("RGB")
    img.save(dest, format="PNG")
    return dest


def _upload_qr_api(png: Path) -> dict:
    token = os.environ["CANVA_ACCESS_TOKEN"]
    meta = json.dumps({"name_base64": __import__("base64").b64encode(png.name.encode()).decode()})
    job = _req("POST", "/asset-uploads", token, data=png.read_bytes(),
               headers={"Content-Type": "application/octet-stream", "Asset-Upload-Metadata": meta})["job"]
    asset = _poll(f"/asset-uploads/{job['id']}", token, "job")["asset"]["id"]
    return {
        "asset_id": asset,
        "api": (
            "Uploaded the QR as a Canva asset. The public Connect REST API has no element-level "
            "replace (only brand-template autofill). Use the official Canva AI connector (or the "
            "Design Editing API if available) to update_fill the page-4 QR image with this "
            "asset_id, keeping position and size, then export page 4 only. If canva.qr_field is "
            "set on a brand template, that autofill field is filled with this asset."
        ),
    }


def prepare_qr(ev: Event, out_dir: Path, mode: str) -> dict:
    """Generate a signup-form QR PNG when a real form URL is present.

    Connector mode records the steps a Canva connector session should take (upload, replace
    page 4, export ig_04.png). API mode also uploads the PNG as an asset when a token is set.
    """
    cv = ev.raw.get("canva", {})
    page = int(cv.get("qr_page", QR_PAGE))
    base = {
        "page": page,
        "connector_steps": CONNECTOR_QR_STEPS,
        "template_design_id": cv.get("template_design_id"),
        "design_id": cv.get("design_id"),
    }
    url = real_signup_url(ev)
    if not url:
        return {**base, "ok": False, "skipped": "no signup form URL yet"}
    png = write_qr_png(url, out_dir / QR_PNG_NAME)
    result = {**base, "ok": True, "url": url, "png": png.name}
    if mode == "api" and os.environ.get("CANVA_ACCESS_TOKEN"):
        result.update(_upload_qr_api(png))
    return result



def qr_box(ev: Event) -> tuple[float, float, float, float]:
    box = ev.raw.get("canva", {}).get("qr_box")
    if box and len(box) == 4:
        return tuple(float(x) for x in box)
    return DEFAULT_QR_BOX


def qr_frame(ev: Event) -> dict:
    """Tilted page-4 QR frame: event.canva.qr_frame {center, size, angle} overrides the default.
    A legacy axis-aligned event.canva.qr_box is honoured as a frame with angle 0."""
    cv = ev.raw.get("canva", {})
    fr = cv.get("qr_frame")
    if isinstance(fr, dict) and fr.get("center") and fr.get("size"):
        return {"center": tuple(float(x) for x in fr["center"]),
                "size": tuple(float(x) for x in fr["size"]),
                "angle": float(fr.get("angle", 0.0))}
    box = cv.get("qr_box")
    if box and len(box) == 4:
        left, top, w, h = (float(x) for x in box)
        return {"center": (left + w / 2, top + h / 2), "size": (w, h), "angle": 0.0}
    return dict(DEFAULT_QR_FRAME)


def apply_qr_to_card(card_path: Path, qr_path: Path, dest: Path, frame=None, margin: int = 12,
                     quiet: int = 16) -> Path:
    """Paste a signup QR into the page-4 card, matching the template's tilted QR frame.

    A white tile (frame size + *margin*) covers the whole frame so none of the placeholder
    shows; the QR (square, min(w, h) - *quiet*, nearest-neighbour so modules stay crisp) sits
    centred on it, and the tile is rotated by the frame angle (clockwise) before pasting at the
    frame centre. *frame* may also be a legacy (left, top, w, h) box (angle 0).
    """
    from PIL import Image

    if frame is None:
        frame = DEFAULT_QR_FRAME
    if not isinstance(frame, dict):
        left, top, w, h = (float(x) for x in frame)
        frame = {"center": (left + w / 2, top + h / 2), "size": (w, h), "angle": 0.0}
    cx, cy = frame["center"]
    fw, fh = frame["size"]
    angle = float(frame.get("angle", 0.0))
    tw, th = int(round(fw)) + margin, int(round(fh)) + margin
    side = int(round(min(fw, fh))) - quiet
    card = Image.open(card_path).convert("RGB")
    qr = Image.open(qr_path).convert("RGB").resize((side, side), Image.Resampling.NEAREST)
    tile = Image.new("RGB", (tw, th), "white")
    tile.paste(qr, ((tw - side) // 2, (th - side) // 2))
    mask = Image.new("L", (tw, th), 255)
    if angle:
        # PIL rotates counter-clockwise for positive angles; the frame is tilted clockwise.
        tile = tile.rotate(-angle, resample=Image.Resampling.BICUBIC, expand=True, fillcolor="white")
        mask = mask.rotate(-angle, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=0)
    card.paste(tile, (int(round(cx - tile.width / 2)), int(round(cy - tile.height / 2))), mask)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    card.save(dest, format="PNG")
    return dest


def _decode_qr_png(path: Path) -> str | None:
    try:
        import cv2
        data, _, _ = cv2.QRCodeDetector().detectAndDecode(cv2.imread(str(path)))
        return data or None
    except Exception:
        return None


def update_qr(ev: Event, dest: Path) -> dict:
    """Dashboard entry point for the 「更新 Canva QR」 button.

    Writes a QR PNG for the current 課前 form URL into *dest*. If a page-4 card
    already exists in event.canva.exported_dir, the new QR is stamped into that
    frame (ig_04.png) so the gallery updates. When CANVA_ACCESS_TOKEN is set the
    asset is also uploaded (and autofilled if canva.qr_field is configured).
    Returns the same keys ``web._do_qr`` already reads: ok, url, png, skipped,
    plus title_lines and an optional card filename.
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    mode = "api" if os.environ.get("CANVA_ACCESS_TOKEN") else "connector"
    qr = prepare_qr(ev, dest, mode)
    qr["title_lines"] = ig_title_lines(ev)
    if not qr.get("ok"):
        return {**qr, "qr": qr}
    qr_png = dest / qr["png"]
    cv = ev.raw.get("canva", {})
    export_dir = ROOT / cv.get("exported_dir", "") if cv.get("exported_dir") else None
    if export_dir and export_dir.is_dir():
        shutil.copy2(qr_png, export_dir / QR_PNG_NAME)
        card_src = export_dir / "ig_04.png"
        template = export_dir / QR_TEMPLATE_NAME
        if card_src.is_file() and not template.is_file() and _decode_qr_png(card_src) is None:
            # Keep the untouched Canva export so a re-stamp never paints over an older QR.
            shutil.copy2(card_src, template)
        base_card = template if template.is_file() else card_src
        if base_card.is_file():
            stamped = apply_qr_to_card(base_card, qr_png, dest / "ig_04.png", qr_frame(ev))
            qr["card"] = stamped.name
            # From the pristine template the stamp is always current; otherwise only replace a
            # card whose QR does not already point at this form.
            if base_card != card_src or _decode_qr_png(card_src) != qr["url"]:
                shutil.copy2(stamped, card_src)
    return {**qr, "qr": qr}


def run_api(ev: Event, out_dir: Path) -> dict:
    token = os.environ["CANVA_ACCESS_TOKEN"]
    cv = ev.raw.get("canva", {})
    photo = ROOT / ev.raw["photo"]
    qr = prepare_qr(ev, out_dir, "api")
    meta = json.dumps({"name_base64": __import__("base64").b64encode(photo.name.encode()).decode()})
    job = _req("POST", "/asset-uploads", token, data=photo.read_bytes(),
               headers={"Content-Type": "application/octet-stream", "Asset-Upload-Metadata": meta})["job"]
    asset = _poll(f"/asset-uploads/{job['id']}", token, "job")["asset"]["id"]
    design_id = cv.get("design_id")
    if cv.get("brand_template_id"):
        data = {"speaker_photo": {"type": "image", "asset_id": asset},
                "title": {"type": "text", "text": ig_title_lines(ev)},
                "speaker_name": {"type": "text", "text": ev["speaker"]["name"]},
                "speaker_title": {"type": "text", "text": ev["speaker"]["title"]},
                "time": {"type": "text", "text": f"{ev['md']} {ev['time_range']}"},
                "room": {"type": "text", "text": ev["room"]}}
        if qr.get("asset_id") and cv.get("qr_field"):
            data[cv["qr_field"]] = {"type": "image", "asset_id": qr["asset_id"]}
        j = _req("POST", "/autofills", token, {"brand_template_id": cv["brand_template_id"], "data": data,
                                               "title": f"{ev['semester']} {ev['session_label']} IG"})["job"]
        design_id = _poll(f"/autofills/{j['id']}", token, "job")["result"]["design"]["id"]
    j = _req("POST", "/exports", token, {"design_id": design_id, "format": {"type": "png", "pages": cv.get("pages", [1])}})["job"]
    urls = _poll(f"/exports/{j['id']}", token, "job")["urls"]
    files = []
    for i, u in enumerate(urls, 1):
        p = out_dir / f"ig_{i:02d}.png"
        urllib.request.urlretrieve(u, p); files.append(p.name)
    return {"mode": "api", "design_id": design_id, "asset_id": asset, "files": files, "qr": qr,
            "title_lines": ig_title_lines(ev)}


def run(ev: Event, out_dir: Path, mode: str | None = None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    mode = mode or ("api" if os.environ.get("CANVA_ACCESS_TOKEN") else "connector")
    cv = ev.raw.get("canva", {})
    if mode == "api":
        return run_api(ev, out_dir)
    qr = prepare_qr(ev, out_dir, mode)
    src = ROOT / cv.get("exported_dir", "")
    files = sorted(src.glob("ig_[0-9][0-9].png")) if src.is_dir() else []
    for f in files:
        shutil.copy2(f, out_dir / f.name)
    return {"mode": "connector", "design_id": cv.get("design_id"), "template_design_id": cv.get("template_design_id"),
            "design_url": f"https://www.canva.com/design/{cv.get('design_id')}/edit" if cv.get("design_id") else "",
            "files": [f.name for f in files], "ok": bool(files), "qr": qr, "title_lines": ig_title_lines(ev)}
