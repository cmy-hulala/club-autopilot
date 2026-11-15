"""Connector-side helper: turn a Canva start-editing-transaction result + a canva_job plan section
into the perform-editing-operations list (find/replace per text element, photo frame swap).

usage: python scripts/canva_ops.py TX.json PLAN.json ig|cert [PHOTO_ASSET_ID]
(ig and cert also swap watermarked stock images for the clean uploads in config/canva_clean_assets.yaml)
"""
import json
import sys


def text_of(el):
    return "".join(r.get("text", "") for r in el.get("regions") or [])


def load_swaps(path="config/canva_clean_assets.yaml"):
    swaps = {}
    try:
        for line in open(path, encoding="utf-8"):
            line = line.split("#", 1)[0].strip()
            if ":" in line:
                a, b = (x.strip() for x in line.split(":", 1))
                if a and b:
                    swaps[a] = b
    except OSError:
        pass
    return swaps


def build(tx, section, photo_asset=None, photo_page=1, swaps=None):
    ops, hits = [], {}
    for f in tx.get("fills") or []:
        new = (swaps or {}).get(f.get("asset_id"))
        if not new or not f.get("editable"):
            continue
        asset, _, mode = new.partition(" ")
        box = f.get("containerElement") or {}
        mode = mode.split("#")[0].strip()
        if mode in ("reinsert", "reinsert0") and box:
            ops.append({"type": "delete_element", "element_id": f["element_id"]})
            ops.append({"type": "insert_fill", "page_id": f["element_id"].split("-")[0], "asset_type": "image",
                        "asset_id": asset, "alt_text": "clean asset",
                        "top": round(box["position"]["top"], 1), "left": round(box["position"]["left"], 1),
                        "width": round(box["dimension"]["width"], 1), "height": round(box["dimension"]["height"], 1),
                        "rotation": 0 if mode == "reinsert0" else 90})
        else:
            ops.append({"type": "update_fill", "element_id": f["element_id"], "asset_type": "image",
                        "asset_id": asset, "alt_text": "clean asset"})
    for el in tx.get("richtexts") or []:
        txt = text_of(el)
        for pair in section["replace"]:
            if pair["find"] and pair["find"] in txt:
                ops.append({"type": "find_and_replace_text", "element_id": el["element_id"],
                            "find_text": pair["find"], "replace_text": pair["replace"]})
                hits[pair["find"]] = hits.get(pair["find"], 0) + 1
    tf = section.get("title_find")
    if tf:
        boxes = [el for el in tx.get("richtexts") or [] if tf in text_of(el)]
        if not boxes:
            pass
        widest = max((el["containerElement"]["dimension"]["width"] for el in boxes), default=0)
        for el in boxes:
            ops.append({"type": "replace_text", "element_id": el["element_id"], "text": section["title_text"]})
            box = el["containerElement"]
            w = box["dimension"]["width"]
            if w < widest - 1:
                cx = box["position"]["left"] + w / 2
                ops.append({"type": "resize_element", "element_id": el["element_id"], "width": round(widest, 1)})
                ops.append({"type": "position_element", "element_id": el["element_id"],
                            "top": round(box["position"]["top"], 1), "left": round(cx - widest / 2, 1)})
    missing = [p["find"] for p in section["replace"] if p["find"] not in hits]
    if tf and not any(tf in text_of(el) for el in tx.get("richtexts") or []):
        missing.append(tf)
    if photo_asset:
        frames = [f for f in tx.get("fills") or [] if f.get("page_index") == photo_page and f.get("editable")
                  and (f.get("containerElement") or {}).get("type") == "SHAPE"]
        if not frames:
            raise SystemExit("no photo frame on page %d" % photo_page)
        frame = max(frames, key=lambda f: f["containerElement"]["dimension"]["width"] * f["containerElement"]["dimension"]["height"])
        ops.append({"type": "update_fill", "element_id": frame["element_id"], "asset_type": "image",
                    "asset_id": photo_asset, "alt_text": "講者照片"})
    if section.get("title"):
        ops.append({"type": "update_title", "title": section["title"]})
    return ops, missing


if __name__ == "__main__":
    tx = json.load(open(sys.argv[1], encoding="utf-8"))
    plan = json.load(open(sys.argv[2], encoding="utf-8"))
    plan = plan.get("plan", plan)
    which = sys.argv[3]
    ops, missing = build(tx, plan[which], sys.argv[4] if len(sys.argv) > 4 else None,
                         plan[which].get("photo_page", 1), load_swaps())
    print(json.dumps({"operations": ops, "missing": missing}, ensure_ascii=False))
