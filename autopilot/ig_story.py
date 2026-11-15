"""「轉發貼文到限動（手機）」: after the officer has PUBLISHED the post themselves.

Flow (HTC U23 pro, measured 2026-11-15 on an existing club post):
  open the post (event ig.post_url, else newest non-pinned post on the club profile)
  -> [optional, only when the officer ticked it in the confirm dialog] like + repost, each only if not already on
  -> paper plane -> 新增到限時動態 (reshare editor; IG embeds the post at 80% width with a blurred border)
  -> remove IG's suggested music -> link sticker (課前表單 URL + custom text) dragged to the post's top-right
  -> 限動 music if configured -> try 儲存草稿; this IG build only offers 捨棄／繼續編輯, so we stay in the
     editor and the officer taps 「限時動態」 on the phone themselves.
Never taps 限時動態／摯友／分享到／Share. The post page can't be read with uiautomator (scrolling song title ->
'could not get idle state'), so the action row is found from the screenshot pixels instead."""
from __future__ import annotations

import io
import json
import time
from pathlib import Path
from typing import Callable

from .ig_adb import IGStop, MusicNotFound, Phone, _center, _default_runner, devices, find_text, load_config, \
    node_at, paste_via_scrcpy, remember

STORY_FORBIDDEN = ["限時動態", "你的限時動態", "摯友", "分享到", "分享", "Share", "Your story", "Close Friends",
                   "發佈", "發布", "Post"]
EDITOR_RID = "your_story_share_shortcut_button"


# ---------- screenshot analysis (post page) ----------
def cover_distance(png: bytes, cover: Path) -> float:
    """Mean grey-level difference between the post page's first image and our ig_01 cover (centre of the card,
    where the speaker photo and name are). Same post ≈ 2–5; another post of the same template ≈ 40–50."""
    from PIL import Image, ImageChops, ImageStat
    im = _img(png).convert("L")
    ref = Image.open(cover).convert("L").resize((1080, 1350))
    b = ref.crop((300, 420, 780, 1050)).resize((96, 126))
    best = 999.0
    for top in range(300, 470, 6):
        a = im.crop((0, top, 1080, top + 1350)).crop((300, 420, 780, 1050)).resize((96, 126))
        best = min(best, ImageStat.Stat(ImageChops.difference(a, b)).mean[0])
    return best


COVER_MAX = 15.0


# ---------- screenshot analysis (post page) (row) ----------
def _img(png: bytes):
    from PIL import Image
    return Image.open(io.BytesIO(png)).convert("RGB")


def _dark(p) -> bool:
    return sum(p) < 270


def _red(p) -> bool:
    return p[0] > 200 and p[1] < 90 and p[2] < 110


def _count(im, x1, y1, x2, y2, pred) -> int:
    w, h = im.size
    n = 0
    for y in range(max(0, y1), min(h, y2), 2):
        for x in range(max(0, x1), min(w, x2), 2):
            if pred(im.getpixel((x, y))):
                n += 1
    return n


def find_action_row(png: bytes, rc: dict) -> dict | None:
    """Locate the ♥ 💬 ⟲ ✈ … 🔖 row from the screenshot. The icons move sideways depending on whether like /
    repost / share counts are shown, so the row's y is found from the heart (left edge) and the bookmark (right
    edge), then every icon's x is measured on that row: icons are ≥40 px tall, count digits only ~27 px.
    Returns {y, liked, reposted, heart_x, comment_x, repost_x, share_x} or None."""
    import numpy as np
    a = np.asarray(_img(png)).astype(int)
    h, w, _ = a.shape
    red = (a[:, :, 0] > 200) & (a[:, :, 1] < 90) & (a[:, :, 2] < 110)
    ink = (a.sum(axis=2) < 270) | red
    r = rc.get("half", 28)
    hx, bx = rc.get("heart_x", 62), rc.get("bookmark_x", 1017)
    best = None
    for y in range(rc.get("scan_from", 1200), min(h - r, rc.get("scan_to", 2300)), 4):
        heart = int(ink[y - r:y + r:2, hx - r:hx + r:2].sum())
        mark = int(ink[y - r:y + r:2, bx - r:bx + r:2].sum())
        if heart < rc.get("min_px", 12) or mark < rc.get("bookmark_min_px", 60):
            continue
        if max(heart, mark) > (r * r) * 0.8:  # solid block = photo / text, not an outline icon
            continue
        if best is None or heart + mark > best[0]:
            best = (heart + mark, y)
    if not best:
        return None
    y = best[1]
    band = ink[max(0, y - 45):y + 45, :]
    cols = band.any(axis=0)
    clusters, x = [], 0
    while x < w:
        if not cols[x]:
            x += 1
            continue
        x0 = x
        gap = 0
        while x < w and gap <= 6:
            gap = 0 if cols[x] else gap + 1
            x += 1
        x1 = x - gap - 1
        rows = np.where(band[:, x0:x1 + 1].any(axis=1))[0]
        if x1 - x0 >= 36 and rows.size and rows[-1] - rows[0] >= 40:
            clusters.append((x0, x1, y - 45 + int(rows[0]), y - 45 + int(rows[-1])))
        # digits / short marks are skipped
    if len(clusters) < 5:
        return None
    left = sorted(clusters)[:4]
    (h0, h1, *_), (c0, c1, *_), (r0, r1, *_), (s0, s1, *_) = left
    cy = (left[0][2] + left[0][3]) // 2
    liked = int(red[cy - r:cy + r:2, h0:h1 + 1:2].sum()) > rc.get("liked_px", 40)
    rxc = (r0 + r1) // 2
    ryc = (left[2][2] + left[2][3]) // 2
    # the ✓ sits in the empty middle of the arrows; the plain icon's arrow heads are above/below it
    reposted = int(ink[ryc - 5:ryc + 6, rxc - 12:rxc + 12].sum()) > rc.get("reposted_px", 3) * 4
    return {"y": cy, "liked": liked, "reposted": reposted, "heart_x": (h0 + h1) // 2, "comment_x": (c0 + c1) // 2,
            "repost_x": rxc, "share_x": (s0 + s1) // 2}


class StoryPhone(Phone):
    def __init__(self, cfg: dict, shots: Path, runner=_default_runner, sleep=time.sleep, paste=None):
        cfg = dict(cfg)
        cfg["forbidden_text"] = list(dict.fromkeys((cfg.get("forbidden_text") or []) + STORY_FORBIDDEN))
        super().__init__(cfg, shots, runner, sleep, paste)
        self.sc = cfg.get("story") or {}
        self.home_check = lambda nodes: any(n.get("rid") == EDITOR_RID for n in nodes)
        self.learn_file = None  # tests point this at a temp file

    def pos(self, key: str):
        p = (self.sc.get("pos") or {}).get(key)
        return (int(p[0]), int(p[1])) if p else None

    def tap_pos(self, key: str, name: str) -> bool:
        p = self.pos(key)
        if not (self.fast and p):
            return False
        self.tap(p[0], p[1], name, verified=True)
        return True

    def learn(self, key: str, n: dict) -> None:
        c = _center(n)
        if self.pos(key) != c:
            remember("story", key, c, self.learn_file)
            self.sc.setdefault("pos", {})[key] = list(c)
            self.log.append(f"位置更新：{key} → {c}")

    def png(self) -> bytes:
        return self.run([self.adb, "exec-out", "screencap", "-p"], binary=True)

    def blind_tap(self, x: int, y: int, name: str) -> None:
        """Tap on a screen uiautomator can't read: refuse unless the spot is clear of the story-share bar."""
        nodes = self.ui()
        if nodes:
            return self.tap(x, y, name)
        self.sh("input", "tap", str(x), str(y))
        self.log.append(f"點 {name} ({x},{y})（畫面無法讀取，依截圖座標）")
        self.sleep(float(self.cfg.get("wait_s", 2.5)))

    # 1. open the post
    def open_post(self, post_url: str = "") -> None:
        pkg = self.cfg.get("package") or "com.instagram.android"
        if post_url:
            self.sh("am", "start", "-a", "android.intent.action.VIEW", "-d", post_url, "-p", pkg)
            self.sleep(float(self.cfg.get("wait_s", 2.5)) * 2)
            self.log.append("已用貼文網址開啟貼文")
            return
        self.launch()
        x, y = self.sc.get("profile_tab_xy") or [972, 2200]
        self.tap(int(x), int(y), "個人檔案")
        # The profile grid is cached: a post published a minute ago only shows after pull-to-refresh.
        self.sh("input", "swipe", "540", "900", "540", "1600", "400")
        self.sleep(4.0)
        nodes = self.ui()
        cells = [n for n in nodes if n.get("rid") == "image_button" and "第1橫列" in n["desc"]]
        cells = [n for n in cells if not any(w in n["desc"] for w in ("置頂", "Pinned", "pinned"))] or cells
        if not cells:
            self.shot("STOP_profile")
            raise IGStop("在個人檔案找不到最新貼文，已停止（沒有分享）。可在活動設定填 ig.post_url。")
        cells.sort(key=lambda n: n["box"][0])
        self._tap_node(cells[0], "最新貼文")
        self.log.append("已開啟個人檔案的最新貼文（非置頂）")

    # 2. like / repost (public: only when the officer confirmed; never toggles an already-on button)
    def like_and_repost(self, row: dict) -> dict:
        """Counts appear after a tap and shift the icons, so the row is re-measured before every tap."""
        rc = self.sc.get("row") or {}
        out = {"liked": row["liked"], "reposted": row["reposted"], "tapped": []}
        if not row["liked"]:
            self.blind_tap(row["heart_x"], row["y"], "愛心")
            out["tapped"].append("like")
            self.sleep(1.5)
            row = find_action_row(self.png(), rc) or row
        if not row["reposted"]:
            self.blind_tap(row["repost_x"], row["y"], "轉發")
            out["tapped"].append("repost")
            self.sleep(1.5)
        self.shot("r03_liked_reposted")
        after = find_action_row(self.png(), rc)
        if after:
            out["liked"], out["reposted"] = after["liked"], after["reposted"]
            self.row = after
        if not (out["liked"] and out["reposted"]):
            self.log.append("按讚／轉發後畫面上看不出兩個都已開啟，請看 r03 截圖確認")
        return out

    # 3. reshare editor
    def open_reshare(self, row: dict) -> None:
        rc = self.sc.get("row") or {}
        row = find_action_row(self.png(), rc) or row  # re-measure: like/repost counts may have shifted the icons
        if not row.get("share_x"):
            raise IGStop("找不到分享紙飛機的位置，已停止（沒有分享）。")
        self.blind_tap(row["share_x"], row["y"], "分享紙飛機（開啟傳送選單）")
        self.shot("r04_share_sheet")
        x, y = self.sc.get("add_to_story_xy") or [112, 2045]
        self.blind_tap(int(x), int(y), "新增到限時動態")
        nodes = []
        for _ in range(3):
            self.sleep(1.0)
            nodes = self.ui()
            if self.home_check(nodes):
                break
        if not self.home_check(nodes):
            self.shot("STOP_no_editor")
            self.sh("input", "keyevent", "4")
            raise IGStop("按「新增到限時動態」後沒有進到限動編輯畫面，已停止（沒有分享）。請看截圖。")
        self.log.append("已進入限動編輯（轉發貼文）")
        return nodes

    @staticmethod
    def _is_sticker(n: dict) -> bool:
        # "貼圖. 按住即可調整位置…" = a placed sticker; the toolbar button's desc is just "貼圖".
        return n["desc"].startswith("貼圖.") and n["box"][2] - n["box"][0] > 10

    def post_sticker(self, nodes: list[dict]) -> dict | None:
        st = [n for n in nodes if self._is_sticker(n)]
        return max(st, key=lambda n: (n["box"][2] - n["box"][0]) * (n["box"][3] - n["box"][1]), default=None)

    def check_scale(self, nodes: list[dict] | None = None) -> float:
        p = self.post_sticker(nodes if nodes is not None else self.ui())
        if not p:
            raise IGStop("限動編輯畫面上找不到貼文，已停止（沒有分享）。")
        w = (p["box"][2] - p["box"][0]) / float((self.cfg.get("screen") or [1080])[0])
        lo, hi = self.sc.get("scale_range") or [0.7, 0.85]
        self.log.append(f"貼文寬度 {w:.0%}（IG 預設，四周有底色邊框）" + ("" if lo <= w <= hi else "，超出 75–80%，請手動調整"))
        return w

    def drop_suggested_music(self, nodes: list[dict] | None = None) -> None:
        nodes = nodes if nodes is not None else self.ui()
        h = next((n for n in nodes if n.get("rid") == "suggested_music_header"), None)
        if not h:
            self.log.append("沒有 IG 建議音樂")
            return
        self.sh("input", "tap", str(h["box"][2] + 19), str((h["box"][1] + h["box"][3]) // 2))
        self.sleep(1.5)
        nodes = self.ui()
        done = next((n for n in nodes if n.get("rid") == "done_button"), None)
        if done:  # missed the X and opened the text tool: close it, nothing typed
            self._tap_node(done, "關閉文字工具")
        self.log.append("已移除 IG 建議音樂")

    # 4. link sticker
    def add_link_sticker(self, url: str, cta: str) -> dict:
        """Fast: tap the remembered positions, paste, then ONE read to verify URL/text before 完成."""
        if self.fast and all(self.pos(k) for k in ("sticker_tool", "link_sticker", "url_field", "link_done")):
            before = {n["box"] for n in self._last_editor or [] if self._is_sticker(n)}
            self.tap_pos("sticker_tool", "貼圖")
            self.sleep(0.8)  # sticker tray animation
            self.tap_pos("link_sticker", "連結貼圖")
            self.tap_pos("url_field", "網址欄")
            self.paste_fn(self.cfg, url)
            if cta and self.pos("cta_row") and self.pos("cta_field"):
                self.tap_pos("cta_row", "自訂貼圖文字")
                self.tap_pos("cta_field", "貼圖文字欄")
                self.paste_fn(self.cfg, cta)
            nodes = self.ui()  # checkpoint
            f = next((n for n in nodes if n.get("rid") == "link_sticker_list_web_url_edit_text"), None)
            if f and f["text"].strip() == url:
                e = next((n for n in nodes if n.get("rid") == "link_sticker_custom_cta_edit_text"), None)
                if cta and not (e and e["text"].strip() == cta):
                    if e:
                        self._tap_node(e, "貼圖文字欄")
                        self.paste_fn(self.cfg, cta)
                    else:
                        self.log.append("沒看到自訂貼圖文字欄，貼圖會顯示網址")
                self.shot("r06_link_filled")
                self.tap_pos("link_done", "連結完成")
                nodes = self.ui()
                new = [n for n in nodes if self._is_sticker(n) and n["box"] not in before
                       and (not self.post_sticker(nodes) or n["box"] != self.post_sticker(nodes)["box"])]
                if new:
                    self._last_editor = nodes
                    self.log.append(f"已加連結貼圖（網址已核對{'，文字「' + cta + '」' if cta else ''}）")
                    return min(new, key=lambda n: (n["box"][2] - n["box"][0]) * (n["box"][3] - n["box"][1]))
                raise IGStop("加完連結後找不到貼圖，已停止（沒有分享）。")
            # quick check failed: find where we are and fall back to locating (positions get re-learned)
            self.log.append("快速點擊沒有到連結網址欄，改用找字定位")
            if f:  # sheet is open but the paste missed: redo the field by locating
                return self._link_from_sheet(url, cta, set())
            if find_text(nodes, ["連結貼圖", "Link sticker"]):
                return self._link_from_tray(url, cta, before)
            if not self.home_check(nodes):
                self.sh("input", "keyevent", "4")
                self.sleep(1.0)
        return self._add_link_slow(url, cta)

    _last_editor: list | None = None

    def _add_link_slow(self, url: str, cta: str) -> dict:
        nodes = self.ui()
        tool = next((n for n in nodes if (n["desc"] or n["text"]) == "貼圖" and n["box"][0] > 850), None)
        if not tool:
            raise IGStop("找不到「貼圖」工具，已停止（沒有分享）。")
        self.learn("sticker_tool", tool)
        self._tap_node(tool, "貼圖")
        before = {n["box"] for n in nodes if self._is_sticker(n)}
        return self._link_from_tray(url, cta, before)

    def _link_from_tray(self, url: str, cta: str, before: set) -> dict:
        link = None
        for _ in range(3):
            link = find_text(self.ui(), ["連結貼圖", "Link sticker", "連結"])
            if link:
                break
            self.sleep(1.0)
        if not link:
            self.shot("STOP_no_link_sticker")
            raise IGStop("貼圖列表沒有「連結」貼圖，已停止（沒有分享）。")
        self.learn("link_sticker", link)
        self._tap_node(link, "連結貼圖")
        return self._link_from_sheet(url, cta, before)

    def _link_from_sheet(self, url: str, cta: str, before: set) -> dict:
        nodes = self.ui()
        f = next((n for n in nodes if n.get("rid") == "link_sticker_list_web_url_edit_text"), None)
        if not f:
            raise IGStop("找不到連結網址欄，已停止（沒有分享）。")
        self.learn("url_field", f)
        self._tap_node(f, "網址欄")
        self.paste_fn(self.cfg, url)
        self.sleep(1.0)
        f = next((n for n in self.ui() if n.get("rid") == "link_sticker_list_web_url_edit_text"), None)
        if not f or f["text"].strip() != url:
            self.shot("STOP_link_url")
            raise IGStop("網址欄的內容和課前表單網址不同，已停止（沒有分享）。")
        if cta:
            row = next((n for n in self.ui() if n.get("rid") in ("link_sticker_custom_cta_row", "link_sticker_custom_cta_row_title")), None)
            if row:
                self.learn("cta_row", row)
                self._tap_node(row, "自訂貼圖文字")
            e = next((n for n in self.ui() if n.get("rid") == "link_sticker_custom_cta_edit_text"), None)
            if e:
                self.learn("cta_field", e)
                self._tap_node(e, "貼圖文字欄")
                self.paste_fn(self.cfg, cta)
                self.sleep(1.0)
        self.shot("r06_link_filled")
        done = next((n for n in self.ui() if n.get("rid") == "link_sticker_list_done_icon_button"), None)
        if not done:
            raise IGStop("找不到連結貼圖的完成鈕，已停止（沒有分享）。")
        self.learn("link_done", done)
        self._tap_node(done, "連結完成")
        nodes = self.ui()
        post = self.post_sticker(nodes)
        new = [n for n in nodes if self._is_sticker(n) and n["box"] not in before and (not post or n["box"] != post["box"])]
        self._last_editor = nodes
        if not new:
            raise IGStop("加完連結後找不到貼圖，已停止（沒有分享）。")
        self.log.append(f"已加連結貼圖（網址已核對{'，文字「' + cta + '」' if cta else ''}）")
        return min(new, key=lambda n: (n["box"][2] - n["box"][0]) * (n["box"][3] - n["box"][1]))

    def place_top_right(self, sticker: dict) -> tuple:
        """Drag the link sticker so its top-right sits on the post's top-right corner (a few tries)."""
        m = int(self.sc.get("sticker_margin", 6))
        box = sticker["box"]
        nodes = self._last_editor
        for _ in range(int(self.sc.get("place_tries", 3))):
            post = self.post_sticker(nodes if nodes else self.ui())
            nodes = None
            if not post:
                break
            w, h = box[2] - box[0], box[3] - box[1]
            tx, ty = post["box"][2] - m - w // 2, post["box"][1] + m + h // 2
            cx, cy = _center({"box": box})
            if abs(tx - cx) <= 15 and abs(ty - cy) <= 15:
                break
            # IG eats the first ~20 px of a drag (touch slop): overshoot a little
            ox = 20 if tx > cx else -20 if tx < cx else 0
            oy = 20 if ty > cy else -20 if ty < cy else 0
            self.sh("input", "swipe", str(cx), str(cy), str(tx + ox), str(ty + oy), "1500")
            self.sleep(0.6 if self.fast else 1.5)
            cand, nodes = self._find_moved(w, post["box"])
            if not cand:
                break
            box = cand[0]["box"]
        post = self.post_sticker(nodes or self.ui())
        ok = bool(post) and abs(box[2] - post["box"][2]) <= 25 and abs(box[1] - post["box"][1]) <= 25
        self.log.append(f"連結貼圖位置 {box}，貼文 {post['box'] if post else '?'}：" + ("在右上角" if ok else "沒對準右上角，請手動拖"))
        return box, ok

    def _find_moved(self, w: int, post_box) -> tuple:
        """Re-read the dragged sticker. Right after a drag the dump can fail or still show the old spot,
        so retry a few times (a stale position made the run report 「沒對準」 while it was placed fine)."""
        nodes = []
        for i in range(3):
            nodes = self.ui()
            cand = [n for n in nodes if self._is_sticker(n) and abs((n["box"][2] - n["box"][0]) - w) <= 4
                    and n["box"] != post_box]
            if cand:
                return cand, nodes
            self.sleep(0.8)
        return [], nodes

    def open_story_music(self) -> None:
        if not self.tap_pos("music_tool", "音樂"):
            tool = next((n for n in self.ui() if (n["desc"] or n["text"]) == "音樂" and n["box"][0] > 850), None)
            if not tool:
                raise IGStop("找不到「音樂」工具，已停止（沒有分享）。")
            self.learn("music_tool", tool)
            self._tap_node(tool, "音樂")
        self.sleep(1.0)
        # If a music sticker already exists IG opens its editor instead of the search: go back to search.
        back = next((n for n in self.ui() if n.get("rid") == "album_art_button"), None)
        if back:
            self._tap_node(back, "返回音樂搜尋")

    def publish(self) -> str:
        """Publish the story (tap 「限時動態」 in the share bar). Only when the officer ticked
        「直接發佈限動」 and confirmed. Never taps 摯友 / 分享到."""
        nodes = self.ui()
        btn = next((n for n in nodes if n.get("rid") == "your_story_share_shortcut_button"), None)
        if not btn:
            raise IGStop("找不到「限時動態」發佈鈕，沒有發佈。")
        x, y = _center(btn)
        self.sh("input", "tap", str(x), str(y))
        self.log.append(f"點 限時動態（發佈限動） ({x},{y})")
        for _ in range(10):
            self.sleep(2.0)
            if not any(n.get("rid") in ("cancel_button", "your_story_share_shortcut_button") for n in self.ui()):
                self.log.append("限動已發佈（編輯畫面已關閉）")
                return "published"
        raise IGStop("按了「限時動態」但編輯畫面沒有關閉，請看手機確認是否已發佈（不會重按）。")

    def finish(self, discard: bool = False) -> str:
        """Try IG's story draft; if this IG has none, stay in the editor. Never shares."""
        cancel = next((n for n in self.ui() if n.get("rid") == "cancel_button"), None)
        if not cancel:
            raise IGStop("找不到返回鈕，已停止（沒有分享）。")
        self._tap_node(cancel, "返回（看有沒有儲存草稿）")
        nodes = self.ui()
        self.shot("r09_leave_dialog")
        draft = find_text(nodes, self.cfg.get("draft_text") or ["儲存草稿", "Save draft"])
        if draft and not discard:
            self._tap_node(draft, "儲存草稿")
            return "draft"
        if discard:
            # the dialog headline is 「捨棄相片？」: take the button (rid, else exact label), never the headline
            d = next((n for n in nodes if n.get("rid") == "igds_alert_dialog_primary_button"), None) or next(
                (n for n in nodes if (n["text"] or n["desc"]).strip() in ("捨棄", "Discard")), None)
            if d:
                self._tap_node(d, "捨棄（測試）")
                return "discarded"
        keep = find_text(nodes, ["繼續編輯", "Keep editing"])
        if keep:
            self._tap_node(keep, "繼續編輯")
        return "ready"


def reshare_story(form_url: str, shots: Path, post_url: str = "", cta: str = "", music: dict | None = None,
                  like_repost: bool = False, discard: bool = False, publish: bool = False, probe: dict | None = None, cfg: dict | None = None,
                  expect_cover: Path | None = None, runner: Callable = _default_runner, sleep: Callable = time.sleep, paste: Callable | None = None) -> dict:
    cfg = cfg or load_config()
    sc = cfg.get("story") or {}
    cta = cta if cta else sc.get("link_text", "")
    if not form_url or "{{" in form_url:
        raise IGStop("還沒有課前表單網址。")
    dev = devices(cfg, runner)
    if not dev["ok"]:
        raise IGStop(dev["message"])
    ph = StoryPhone(cfg, shots, runner, sleep, paste or paste_via_scrcpy)
    t0 = time.time()
    res = {"ok": False, "error": "", "music_error": "", "outcome": "", "sticker_ok": False, "scale": None,
           "like_repost": None}
    try:
        ph.check_screen()
        ph.open_post(post_url)
        png = ph.png()
        (ph.shots / "r01_post.png").write_bytes(png)
        if expect_cover:  # never like / repost / share a post that isn't this event's (e.g. a stale profile grid)
            for attempt in range(3):
                dist = cover_distance(png, Path(expect_cover))
                ph.log.append(f"貼文第一張圖與 ig_01 差異 {dist:.1f}（≤{COVER_MAX:.0f} 才算同一則）")
                if dist <= COVER_MAX:
                    break
                if post_url or attempt == 2:
                    raise IGStop("開到的貼文不是這期的新貼文（第一張圖和 ig_01 不同），已停止：沒有按讚、轉發或分享。")
                ph.sh("input", "keyevent", "4")
                ph.sleep(2.0)
                ph.open_post("")
                png = ph.png()
                (ph.shots / "r01_post.png").write_bytes(png)
        ph.shot_files.append("r01_post.png")
        row = find_action_row(png, sc.get("row") or {})
        if not row:
            raise IGStop("在貼文畫面找不到 ♥／轉發／分享 那一列，已停止（沒有分享）。請看 r01_post 截圖。")
        ph.log.append(f"貼文按鈕列 y={row['y']}；已按讚={row['liked']}，已轉發={row['reposted']}")
        if like_repost:
            res["like_repost"] = ph.like_and_repost(row)
        nodes = ph.open_reshare(row)
        ph.shot("r05_editor")
        res["scale"] = ph.check_scale(nodes)
        ph.drop_suggested_music(nodes)
        ph._last_editor = nodes
        st = ph.add_link_sticker(form_url, cta)
        _, res["sticker_ok"] = ph.place_top_right(st)
        ph.shot("r07_sticker")
        if probe:  # search-only check of another song (e.g. the 貼文 song); nothing is attached
            res["probe_found"] = ph.search_only(probe, ph.open_story_music)
        if music:
            try:
                ph.add_music(music, opener=ph.open_story_music, verify_pill=False)
            except MusicNotFound as e:
                res["music_error"] = str(e)
                ph.log.append(str(e))
        else:
            ph.log.append("限動沒有設定音樂，不加")
        res["music_max"], res["music_warn"], res["music_found"] = ph.music_max, ph.music_warn, ph.music_found
        ph.shot("r08_final", key=True)
        res["outcome"] = ph.publish() if publish else ph.finish(discard=discard)
        ph.shot("r10_end", key=True)
        res["ok"] = True
    except IGStop as e:
        res["error"] = str(e)
    res.update({"log": ph.log, "shots": ph.shot_files, "at": time.strftime("%Y-%m-%d %H:%M"),
                "seconds": round(time.time() - t0, 1)})
    (Path(shots) / "story_result.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    return res


def capture_post_pages(shots: Path, pages: int = 4, expect_cover: Path | None = None, cfg: dict | None = None,
                       runner: Callable = _default_runner, sleep: Callable = time.sleep) -> list[str]:
    """After publishing: open the newest (non-pinned) profile post and save one screenshot per carousel
    page as post_p1.png..post_pN.png. Read-only: it only swipes the carousel (never double-taps, likes or
    shares). Returns [] if the opened post's cover doesn't match *expect_cover* (stale grid)."""
    cfg = cfg or load_config()
    sc = cfg.get("story") or {}
    ph = StoryPhone(cfg, shots, runner, sleep, paste=lambda *a, **k: None)
    ph.open_post("")
    ph.sleep(float(sc.get("page_wait_s", 2.5)))
    png = ph.png()
    if expect_cover and cover_distance(png, Path(expect_cover)) > COVER_MAX:
        ph.sh("input", "keyevent", "4")
        return []
    y = str(int(sc.get("carousel_swipe_y", 1050)))
    names = []
    for i in range(1, pages + 1):
        if i > 1:
            ph.sh("input", "swipe", "950", y, "150", y, "300")
            ph.sleep(1.5)
            png = ph.png()
        name = f"post_p{i}.png"
        (Path(shots) / name).write_bytes(png)
        names.append(name)
    ph.sh("input", "keyevent", "4")
    return names
