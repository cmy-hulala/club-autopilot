"""Canva step: new copies of the templates, filled per event, exported — never the originals."""
import sys
import threading
import time
from pathlib import Path

from autopilot import canva_job
from autopilot.event import load_event

ROOT = Path(__file__).resolve().parent.parent
EV = ROOT / "samples" / "notion_export" / "2026-11-25-candice.yaml"
sys.path.insert(0, str(ROOT / "scripts"))
import canva_ops  # noqa: E402


def test_plan_fills_event_and_names_templates():
    p = canva_job.plan(load_event(EV))
    assert p["ig"]["template"] == "DAHSJ-8pyXw" and p["cert"]["template"] == "DAGzIgWU-E4"
    pairs = {x["find"]: x["replace"] for x in p["ig"]["replace"]}
    assert pairs["周世恩"] == "謝慈" and pairs["Shih-En Chou"] == "Candice"
    assert pairs["社課介紹#5"] == "社課介紹#6"
    assert pairs["11/04 19:00～21：00"] == "11/25 19:00～21：30"
    assert p["ig"]["title_text"] == "從職涯迷惘到一人創業：\nAI 時代打造不可取代的競爭力"
    cert = {x["find"]: x["replace"] for x in p["cert"]["replace"]}
    assert cert["November 4, 2026"] == "November 25, 2026"
    assert cert["周世恩"] == "謝慈"
    assert p["exported_dir"] == "assets/canva_export/2026-11-25"


def _tx():
    def text(eid, t, page=1, w=600):
        return {"page_index": page, "element_id": eid, "regions": [{"type": "character", "text": t}],
                "containerElement": {"type": "TEXT", "position": {"top": 100, "left": 100},
                                     "dimension": {"width": w, "height": 50}}}
    return {
        "richtexts": [text("P1-a", "周世恩"), text("P1-t", canva_job.IG_TEMPLATE_TEXT["title"]),
                      text("P4-t", canva_job.IG_TEMPLATE_TEXT["title"], 4, 400)],
        "fills": [
            {"type": "image", "asset_id": "MAElxWljx8I", "page_index": 1, "editable": True, "element_id": "P1"},
            {"type": "image", "asset_id": "MAFaGHuA5rc", "page_index": 2, "editable": True, "element_id": "P2-clip",
             "containerElement": {"type": "MEDIA", "position": {"top": 388.4, "left": 231.1},
                                  "dimension": {"width": 38.6, "height": 137.7}}},
            {"type": "image", "asset_id": "MAHZTLdxgr4", "page_index": 1, "editable": True, "element_id": "P1-photo",
             "containerElement": {"type": "SHAPE", "position": {"top": 483, "left": 348},
                                  "dimension": {"width": 466, "height": 388}}},
        ],
    }


def test_ops_replace_text_swap_clean_assets_and_photo():
    p = canva_job.plan(load_event(EV))
    swaps = canva_ops.load_swaps(str(ROOT / "config" / "canva_clean_assets.yaml"))
    ops, missing = canva_ops.build(_tx(), p["ig"], "MAPHOTO", 1, swaps)
    kinds = [(o["type"], o.get("element_id")) for o in ops]
    assert ("find_and_replace_text", "P1-a") in kinds
    assert ("update_fill", "P1") in kinds                       # watermarked background -> clean upload
    assert ("delete_element", "P2-clip") in kinds               # pre-rotated clip re-inserted
    ins = next(o for o in ops if o["type"] == "insert_fill")
    assert ins["page_id"] == "P2" and ins["rotation"] == 90
    assert any(o["type"] == "update_fill" and o["element_id"] == "P1-photo" and o["asset_id"] == "MAPHOTO" for o in ops)
    assert ("resize_element", "P4-t") in kinds                  # narrow title box widened
    assert "周世恩" not in missing


def test_request_wait_complete_with_local_files(tmp_path):
    ev = load_event(EV)
    out = tmp_path / "out"
    slug = "2026-11-25-candice"
    job = canva_job.request(ev, out, slug)
    assert job["status"] == "pending"
    src = tmp_path / "exports"
    src.mkdir()
    for i in range(1, 5):
        (src / f"p{i}.png").write_bytes(b"png")
    (src / "c.pdf").write_bytes(b"%PDF")
    (src / "c.png").write_bytes(b"png")

    def worker():
        time.sleep(0.3)
        canva_job.progress(out, slug, "上傳講者照片")
        canva_job.complete(out, slug, tmp_path, "DNEWIG00001", "DNEWCE00001",
                           [str(src / f"p{i}.png") for i in range(1, 5)], str(src / "c.pdf"), str(src / "c.png"))

    threading.Thread(target=worker).start()
    res = canva_job.wait(out, slug, timeout_s=10, poll_s=0.05)
    assert res["status"] == "done" and res["ig_design_id"] == "DNEWIG00001"
    assert canva_job.outputs_ready(tmp_path, out, slug, res["plan"]["exported_dir"])
    assert (out / slug / "感謝狀.pdf").read_bytes() == b"%PDF"


def test_wait_times_out(tmp_path):
    ev = load_event(EV)
    canva_job.request(ev, tmp_path, "s")
    assert canva_job.wait(tmp_path, "s", timeout_s=0.2, poll_s=0.05)["status"] == "timeout"


def test_cert_medal_swapped_for_clean_redraw():
    p = canva_job.plan(load_event(EV))
    tx = {"richtexts": [], "fills": [
        {"type": "image", "asset_id": "MAGz9E4s3Q8", "page_index": 1, "editable": True, "element_id": "PC-medal",
         "containerElement": {"type": "MEDIA", "position": {"top": 43.52, "left": 186.48},
                              "dimension": {"width": 34.37, "height": 42.96}}}]}
    swaps = canva_ops.load_swaps(str(ROOT / "config" / "canva_clean_assets.yaml"))
    ops, _ = canva_ops.build(tx, p["cert"], None, 1, swaps)
    assert {"type": "delete_element", "element_id": "PC-medal"} in ops
    ins = next(o for o in ops if o["type"] == "insert_fill")
    assert ins["asset_id"] == swaps["MAGz9E4s3Q8"].split()[0] and ins["rotation"] == 0
    assert (ins["top"], ins["left"]) == (43.5, 186.5)


def test_clean_medal_draws_transparent_png():
    import make_clean_medal
    im = make_clean_medal.draw(200)
    assert im.size == (200, 250) and im.mode == "RGBA"
    assert im.getpixel((0, 0))[3] == 0 and im.getpixel((100, 100))[3] == 255
