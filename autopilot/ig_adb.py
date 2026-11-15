"""「存成 IG 草稿（手機）」: push ig_01..ig_04 to the phone and walk Instagram's new-post flow with adb,
then leave it as a DRAFT. Never taps Share/發佈 (every tap is checked against the live UI first).

Coordinates and the step list live in config/ig_adb.yaml. A screenshot is saved at each key step so the
officer can confirm on the dashboard. Chinese captions can't go through `adb input text`, so the caption is
put on the Windows clipboard and pasted with Ctrl+V into a short-lived scrcpy window (the one step that
shows a window). Manual button only — never part of 全部執行.
"""
from __future__ import annotations

import json
import re
import os
import subprocess
import time
from pathlib import Path
from typing import Callable

import yaml

CONFIG = Path(__file__).resolve().parent.parent / "config" / "ig_adb.yaml"
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW on Windows


class IGStop(RuntimeError):
    pass


LEARNED = CONFIG.parent.parent / "out" / "_ig_learned.yaml"  # positions re-located at run time (not in git)


def load_config(path: Path | None = None, learned: Path | None = None) -> dict:
    cfg = yaml.safe_load(Path(path or CONFIG).read_text(encoding="utf-8")) or {}
    for k in ("adb", "scrcpy"):  # paths may use %LOCALAPPDATA% / %USERPROFILE% (no user name in the repo)
        if isinstance(cfg.get(k), str):
            cfg[k] = os.path.expandvars(cfg[k])
    lp = Path(learned) if learned else (LEARNED if path is None else None)
    if lp and lp.is_file():
        for sect, vals in (yaml.safe_load(lp.read_text(encoding="utf-8")) or {}).items():
            if isinstance(vals, dict):
                cfg.setdefault(sect, {})
                cfg[sect].setdefault("pos", {}).update(vals)
    return cfg


def remember(section: str, key: str, xy: tuple[int, int], learned: Path | None = None) -> None:
    """A quick check failed and the element was found somewhere else: save the new position for next time."""
    lp = Path(learned or LEARNED)
    data = (yaml.safe_load(lp.read_text(encoding="utf-8")) or {}) if lp.is_file() else {}
    data.setdefault(section, {})[key] = [int(xy[0]), int(xy[1])]
    lp.parent.mkdir(parents=True, exist_ok=True)
    lp.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=True), encoding="utf-8")


def _default_runner(args: list[str], *, binary: bool = False, timeout: int = 60):
    kw = {"capture_output": True, "timeout": timeout}
    if hasattr(subprocess, "STARTUPINFO"):
        kw["creationflags"] = NO_WINDOW
    p = subprocess.run(args, **kw)
    out = p.stdout if binary else p.stdout.decode("utf-8", "replace")
    if p.returncode != 0:
        raise IGStop(f"{' '.join(args[1:3])} 失敗：{p.stderr.decode('utf-8', 'replace').strip()[:200]}")
    return out


def devices(cfg: dict, runner: Callable = _default_runner) -> dict:
    """{"ok", "state": none|unauthorized|offline|device|many|no-adb, "message"}"""
    adb = cfg.get("adb") or "adb"
    if runner is _default_runner and not Path(adb).is_file() and adb != "adb":
        return {"ok": False, "state": "no-adb", "message": f"找不到 adb：{adb}"}
    try:
        out = runner([adb, "devices"], timeout=20)
    except Exception as e:
        return {"ok": False, "state": "no-adb", "message": f"adb 無法執行：{e}"}
    rows = [l.split("\t") for l in out.splitlines()[1:] if "\t" in l]
    states = [r[1].strip() for r in rows]
    if not states:
        return {"ok": False, "state": "none", "message": "沒有偵測到手機。"}
    if "unauthorized" in states:
        return {"ok": False, "state": "unauthorized", "message": "手機還沒允許 USB 偵錯：解鎖手機，在跳出的視窗按「允許」。"}
    good = [r for r in rows if r[1].strip() == "device"]
    if len(good) > 1:
        return {"ok": False, "state": "many", "message": "接了不只一支手機，請只留一支。"}
    if not good:
        return {"ok": False, "state": states[0], "message": f"手機狀態：{states[0]}，請重新插拔 USB。"}
    return {"ok": True, "state": "device", "message": "手機已連線。", "serial": good[0][0]}


def parse_nodes(xml: str) -> list[dict]:
    nodes = []
    for m in re.finditer(r"<node\b([^>]*)>?", xml):
        attrs = dict(re.findall(r'([\w-]+)="([^"]*)"', m.group(1)))
        b = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", attrs.get("bounds", ""))
        if not b:
            continue
        x1, y1, x2, y2 = map(int, b.groups())
        nodes.append({"text": attrs.get("text", ""), "desc": attrs.get("content-desc", ""),
                      "rid": attrs.get("resource-id", "").split("/")[-1], "box": (x1, y1, x2, y2)})
    return nodes


def node_at(nodes: list[dict], x: int, y: int) -> list[dict]:
    return [n for n in nodes if n["box"][0] <= x <= n["box"][2] and n["box"][1] <= y <= n["box"][3]]


def find_text(nodes: list[dict], words: list[str]) -> dict | None:
    for n in nodes:
        label = (n["text"] or n["desc"]).strip()
        if label and any(label == w or label.startswith(w) for w in words):
            return n
    return None


class MusicNotFound(IGStop):
    pass


def _norm(s: str) -> str:
    return re.sub(r"[\s\W_]+", "", (s or "").casefold())


def _top_pill(n: dict) -> bool:
    """The audio pill sits near the top of the edit screen; a tall or low box is a layout container."""
    x1, y1, x2, y2 = n["box"]
    return (y1 + y2) // 2 < 600 and (y2 - y1) <= 400


def _center(n: dict) -> tuple[int, int]:
    return (n["box"][0] + n["box"][2]) // 2, (n["box"][1] + n["box"][3]) // 2


class Phone:
    def __init__(self, cfg: dict, shots: Path, runner: Callable = _default_runner, sleep: Callable = time.sleep,
                 paste: Callable | None = None):
        self.cfg, self.shots, self.run, self.sleep = cfg, Path(shots), runner, sleep
        self.adb = cfg.get("adb") or "adb"
        self.paste_fn = paste or paste_via_scrcpy
        self.log: list[str] = []
        self.shot_files: list[str] = []
        self.music_max: int | None = None
        self.music_warn = ""
        self.music_found = ""
        self.fast = bool(cfg.get("fast", True))
        self.shots.mkdir(parents=True, exist_ok=True)

    def sh(self, *args: str, binary: bool = False):
        return self.run([self.adb, "shell", *args], binary=binary)

    def ui(self) -> list[dict]:
        """Current screen nodes. The old dump file is deleted first: on animated screens (a post page with a
        scrolling song title) uiautomator fails with 'could not get idle state', and reading a stale file
        would describe the previous screen. A failed dump returns [] (self.dump_ok = False)."""
        self.sh("rm", "-f", "/sdcard/club_ui.xml")
        self.sh("uiautomator", "dump", "/sdcard/club_ui.xml")
        xml = self.run([self.adb, "exec-out", "cat", "/sdcard/club_ui.xml"])
        xml = xml if isinstance(xml, str) else xml.decode("utf-8", "replace")
        self.dump_ok = "<hierarchy" in xml
        return parse_nodes(xml) if self.dump_ok else []

    def shot(self, name: str, key: bool = False) -> None:
        """Fast mode keeps only key screenshots (final / checkpoints) and STOP_* ones."""
        if self.fast and not key and not name.startswith("STOP") and name not in (self.cfg.get("fast_shots") or []):
            return
        png = self.run([self.adb, "exec-out", "screencap", "-p"], binary=True)
        p = self.shots / f"{name}.png"
        p.write_bytes(png)
        self.shot_files.append(p.name)
        self.log.append(f"截圖 {p.name}")

    def guard(self, x: int, y: int, name: str) -> None:
        bad = self.cfg.get("forbidden_text") or ["分享", "Share"]
        for n in node_at(self.ui(), x, y):
            label = (n["text"] or n["desc"]).strip()
            if label and any(label == w or label.startswith(w) for w in bad):
                self.shot(f"STOP_{name}")
                raise IGStop(f"「{name}」({x},{y}) 的位置是「{label}」按鈕，已停止，沒有點。請調整 config/ig_adb.yaml。")

    def tap(self, x: int, y: int, name: str, verified: bool = False) -> None:
        """verified=True: a position measured in config on a screen with no share button there; fast mode taps it
        directly (no uiautomator round-trip). Everything else still goes through the Share guard."""
        quick = self.fast and verified
        if not quick:
            self.guard(x, y, name)
        self.sh("input", "tap", str(x), str(y))
        self.log.append(f"點 {name} ({x},{y})")
        self.sleep(float(self.cfg.get("fast_wait_s", 1.0) if quick else self.cfg.get("wait_s", 2.5)))

    def check_unlocked(self) -> None:
        out = self.sh("dumpsys", "window")
        out = out if isinstance(out, str) else out.decode("utf-8", "replace")
        if "isKeyguardShowing=true" in out or "mDreamingLockscreen=true" in out:
            raise IGStop("手機螢幕鎖著，請先解鎖手機（保持亮著）再按一次。")
        pw = self.sh("dumpsys", "power")
        pw = pw if isinstance(pw, str) else pw.decode("utf-8", "replace")
        if "mWakefulness=Asleep" in pw or "mWakefulness=Dozing" in pw:
            raise IGStop("手機螢幕是關的，請先打開並解鎖再按一次。")

    def check_screen(self) -> None:
        self.check_unlocked()
        want = self.cfg.get("screen") or [1080, 2400]
        out = self.sh("wm", "size")
        m = re.findall(r"(\d+)x(\d+)", out if isinstance(out, str) else out.decode())
        if not m:
            raise IGStop("讀不到手機解析度。")
        w, h = map(int, m[-1])
        if [w, h] != list(want):
            raise IGStop(f"手機解析度 {w}x{h} 與設定 {want[0]}x{want[1]} 不同，座標會點錯，先停止。")

    def push_images(self, cards: list[Path]) -> None:
        remote = self.cfg.get("remote_dir") or "/sdcard/Pictures/VentureCrew"
        self.sh("mkdir", "-p", remote)
        # Push the last card first so ig_01 (the cover) is the newest and sits first in the gallery.
        for p in reversed(cards):
            dest = f"{remote}/{Path(p).name}"
            self.run([self.adb, "push", str(p), dest], timeout=120)
            self.sh("touch", dest)
            self.sh("am", "broadcast", "-a", "android.intent.action.MEDIA_SCANNER_SCAN_FILE", "-d", f"file://{dest}")
            self.sleep(1.1)
        self.log.append(f"已推 {len(cards)} 張圖到 {remote}")

    def launch(self) -> None:
        self.sh("monkey", "-p", self.cfg.get("package") or "com.instagram.android",
                "-c", "android.intent.category.LAUNCHER", "1")
        self.sleep(float(self.cfg.get("wait_s", 2.5)) * 2)
        self.log.append("已開啟 Instagram")

    def _music_pill(self, nodes: list[dict]) -> tuple[dict | None, bool]:
        """(pill node, attached?). IG shows a suggested-audio pill (rid audio_pill_empty_background,
        text 建議音訊) when nothing is attached; any other audio pill means a song is attached."""
        pills = [n for n in nodes if "audio_pill" in n.get("rid", "") or "music_pill" in n.get("rid", "")]
        if not pills:
            return None, False
        p = pills[0]
        label = (p["text"] + p["desc"])
        suggested = "empty" in p["rid"] or any("建議音訊" in (n["text"] or "") for n in nodes) or label.startswith("建議")
        return p, not suggested

    def no_music(self) -> None:
        """Never add music; if IG auto-attached a song on the edit screen, remove it and verify."""
        pill, attached = self._music_pill(self.ui())
        if not attached:
            self.log.append("沒有加入音樂（IG 只顯示建議音訊，未點）" if pill else "沒有音樂")
            return
        x = (pill["box"][0] + pill["box"][2]) // 2
        y = (pill["box"][1] + pill["box"][3]) // 2
        self.tap(x, y, "音樂標籤")
        words = self.cfg.get("music_remove_text") or ["移除音樂", "移除音訊", "移除", "Remove"]
        n = find_text(self.ui(), words)
        if not n:
            self.shot("STOP_music")
            raise IGStop("IG 自動加了音樂，但找不到「移除」，已停止（沒有分享）。請看截圖。")
        rx = (n["box"][0] + n["box"][2]) // 2
        ry = (n["box"][1] + n["box"][3]) // 2
        # Direct tap: the remove button is expected here; the Share guard still applies.
        for m in node_at(self.ui(), rx, ry):
            lab = (m["text"] or m["desc"]).strip()
            if lab.startswith(("分享", "Share", "發佈", "發布")):
                raise IGStop(f"移除音樂的位置是「{lab}」，已停止。")
        self.sh("input", "tap", str(rx), str(ry))
        self.sleep(float(self.cfg.get("wait_s", 2.5)))
        _, still = self._music_pill(self.ui())
        if still:
            self.sh("input", "keyevent", "4")  # close the music sheet, then check again
            self.sleep(1.5)
            _, still = self._music_pill(self.ui())
        self.shot("05b_no_music")
        if still:
            raise IGStop("移除音樂後畫面上還有音樂，已停止（沒有分享）。請看 05b_no_music 截圖。")
        self.log.append("已移除 IG 自動加入的音樂")

    # ---- music: only the song the officer configured; never IG's suggestion, never another song ----
    def _tap_node(self, n: dict, name: str) -> None:
        """Tap an element we just read from the screen. Its own label is checked against the Share list, so
        no second uiautomator round-trip is needed."""
        lab = (n.get("text") or n.get("desc") or "").strip()
        bad = self.cfg.get("forbidden_text") or ["分享", "Share"]
        if lab and any(lab == w or lab.startswith(w) for w in bad):
            self.shot(f"STOP_{name}")
            raise IGStop(f"「{name}」是「{lab}」按鈕，已停止，沒有點。")
        x, y = _center(n)
        self.tap(x, y, name, verified=True)

    def _type(self, text: str) -> None:
        # The phone's Gboard is in 注音/拼音 mode: `adb input text` gets converted into Chinese even for ASCII,
        # so paste through scrcpy by default (type_mode: adb only if the keyboard is English).
        if text.isascii() and self.cfg.get("type_mode") == "adb":
            esc = re.sub(r"([\\'\"`$&|;<>()])", r"\\\1", text).replace(" ", "%s")
            self.sh("input", "text", esc)
        else:  # adb input can't type Chinese -> clipboard + scrcpy paste
            self.paste_fn(self.cfg, text)
        self.sleep(1.0)

    def _close_sheet(self) -> None:
        mc = self.cfg.get("music") or {}
        home = getattr(self, "home_check", None) or (lambda nodes: self._music_pill(nodes)[0] is not None)
        for _ in range(int(mc.get("close_backs", 2))):
            if home(self.ui()):
                return
            self.sh("input", "keyevent", "4")
            self.sleep(1.2)
            nodes = self.ui()
            keep = find_text(nodes, ["繼續編輯", "Keep editing"])  # back went one step too far: stay
            if keep:
                self.sh("input", "tap", *map(str, _center(keep)))
                self.sleep(1.0)
                return
            if home(nodes):
                return

    def find_song(self, nodes: list[dict], title: str, artist: str) -> dict | None:
        """A result row whose title matches; if an artist is given, it must be on the same row."""
        t, a = _norm(title), _norm(artist)
        if not t:
            return None
        for n in nodes:
            raw = (n["text"] or n["desc"]).strip()
            if raw.startswith(("儲存", "Save", "收藏")):  # bookmark button on the row, not the song
                continue
            if "search" in n.get("rid", "") or "edit_text" in n.get("rid", ""):  # what we typed
                continue
            lab = _norm(raw)
            if not lab or t not in lab:
                continue
            if not a or a in lab:
                return n
            y1, y2 = n["box"][1] - 40, n["box"][3] + 120
            if any(a in _norm(m["text"] or m["desc"]) and y1 <= m["box"][1] <= y2 for m in nodes):
                return n
        return None

    # ---- music editor helpers (measured on the story music editor, 2026-11-15) ----
    def _search_box(self) -> dict | None:
        words = (self.cfg.get("music") or {}).get("search_text") or ["搜尋音樂", "搜尋", "Search music", "Search"]
        for i in range(4):  # the music page loads slowly
            nodes = self.ui()
            box = next((n for n in nodes if n.get("rid") == "row_search_edit_text"), None) or find_text(nodes, words) \
                or next((n for n in nodes if "search_edit" in n.get("rid", "")), None)
            if box:
                return box
            self.sleep(1.5)
        return None

    @staticmethod
    def _row_label(nodes: list[dict], hit: dict) -> str:
        y1, y2 = hit["box"][1] - 40, hit["box"][3] + 120
        return " / ".join((n["text"] or "").strip() for n in nodes
                          if y1 <= n["box"][1] <= y2 and (n["text"] or "").strip())[:120]

    @staticmethod
    def _row_length(nodes: list[dict], hit: dict) -> int | None:
        y1, y2 = hit["box"][1] - 40, hit["box"][3] + 120
        for n in nodes:
            m = re.search(r"•\s*(\d+):(\d\d)\s*$", n["text"] or "")
            if m and y1 <= n["box"][1] <= y2:
                return int(m.group(1)) * 60 + int(m.group(2))
        return None

    @staticmethod
    def _chip(nodes: list[dict]) -> dict | None:
        for n in nodes:
            lab = (n["text"] or n["desc"]).strip()
            if re.fullmatch(r"\d{1,2}\s*(秒|s)?(的片段)?", lab):
                return n
        return None

    def _picker_value(self) -> int | None:
        for n in self.ui():
            if n.get("rid") == "numberpicker_input":
                m = re.match(r"(\d+)", (n["text"] or n["desc"]).strip())
                return int(m.group(1)) if m else None
        return None

    def _set_length(self, chip: dict, dur: int) -> int:
        """Open the circled-number picker, read the longest length this IG offers, then roll to `dur`
        (or the max, with a warning). Falls back to a plain option list."""
        mc = self.cfg.get("music") or {}
        self._tap_node(chip, "片段長度")
        cur = self._picker_value()
        if cur is None:  # list-style options
            opts = {}
            for n in self.ui():
                m = re.fullmatch(r"(\d{1,2})\s*(秒|s)?", (n["text"] or n["desc"]).strip())
                if m:
                    opts.setdefault(int(m.group(1)), n)
            if not opts:
                self.shot("STOP_music_length")
                self._close_sheet()
                raise IGStop("打開片段長度後讀不到任何秒數選項，已停止加音樂（沒有分享）。")
            self.music_max = max(opts)
            use = dur if dur in opts else max([s for s in opts if s <= dur] or [min(opts)])
            self._tap_node(opts[use], f"{use} 秒")
        else:
            x, cy = mc.get("picker_xy") or [540, 1829]
            row = int(mc.get("picker_row_px", 194))
            last, same = cur, 0
            burst = int(mc.get("picker_burst", 4)) if self.fast else 1  # flings between reads
            for _ in range(int(mc.get("picker_max_flings", 15))):  # fling up to the end -> max
                for _ in range(burst):
                    self.sh("input", "swipe", str(x), str(cy + 220), str(x), str(cy - 230), "150")
                    self.sleep(0.4 if self.fast else 0.8)
                v = self._picker_value()
                same = same + 1 if v == last else 0
                last = v if v is not None else last
                if same >= 1:
                    break
            self.music_max = last
            self.shot("05d0_music_lengths")
            use = min(dur, self.music_max)
            v = last
            for _ in range(8):
                if v is None or v == use:
                    break
                # roll the whole distance in 2-row slow drags, then read once
                left = v - use
                while left:
                    steps = max(-2, min(2, left))  # swipe down = smaller numbers
                    self.sh("input", "swipe", str(x), str(cy - 130), str(x), str(cy - 130 + steps * row), "1200")
                    self.sleep(0.3)
                    left -= steps
                v = self._picker_value()
            got = self._picker_value()
            done = next((n for n in self.ui() if n.get("rid") == "music_duration_picker_done_button"), None)
            if done:
                self._tap_node(done, "長度完成")
            if got != use:
                self.music_warn = f"片段長度沒能調到 {use} 秒（目前 {got} 秒），請在手機上確認"
        self.log.append(f"IG 片段長度最長 {self.music_max} 秒")
        if use != dur and not self.music_warn:
            self.music_warn = (f"這支手機的 IG 最長只能 {self.music_max} 秒，已改用 {use} 秒（設定是 {dur} 秒）"
                               if dur > (self.music_max or 0) else f"IG 沒有 {dur} 秒，已改用 {use} 秒")
        if self.music_warn:
            self.log.append(self.music_warn)
        return use

    def _bar_pos(self) -> float | None:
        """Where the selected clip starts, as a fraction of the song, from the white handle on the progress line."""
        mc = self.cfg.get("music") or {}
        bar = mc.get("bar") or {}
        try:
            from PIL import Image
            import io as _io
            im = Image.open(_io.BytesIO(self.run([self.adb, "exec-out", "screencap", "-p"], binary=True))).convert("RGB")
        except Exception:
            return None
        x0, x1, y = int(bar.get("x0", 213)), int(bar.get("x1", 866)), int(bar.get("handle_y", 1748))
        xs = [x for x in range(x0 - 10, x1 + 10) if sum(im.getpixel((x, y))) > 600]
        if not xs or len(xs) > 60:
            return None
        return ((xs[0] + xs[-1]) / 2 - x0) / float(x1 - x0)

    def _set_start(self, start: int, song_len: int | None) -> None:
        mc = self.cfg.get("music") or {}
        y = int(mc.get("scrub_y", 1925))
        pps = float(mc.get("px_per_s", 9.0))
        want = f"{start // 60}:{start % 60:02d}"
        if not song_len:
            if start:
                x0 = int(mc.get("scrub_x", 540))
                self.sh("input", "swipe", str(x0), str(y), str(max(5, x0 - int(start * pps))), str(y), "1500")
                self.sleep(1.5)
            self.log.append(f"開始時間 {want}：不知道歌曲長度，只能估計拖曳，請看截圖確認")
            return
        cur_s = None
        for _ in range(int(mc.get("start_tries", 6))):
            f = self._bar_pos()
            if f is None:
                break
            cur_s = f * song_len
            delta = start - cur_s
            if abs(delta) <= float(mc.get("start_tolerance_s", 2)):
                break
            dx = int(delta * pps)
            dx = max(-800, min(800, dx))
            x_from = 900 if dx > 0 else 180
            slop = 20 if dx > 0 else -20
            self.sh("input", "swipe", str(x_from), str(y), str(x_from - dx - slop), str(y), "1500")
            self.sleep(1.5)
            f2 = self._bar_pos()
            if f2 is not None and abs(f2 - f) > 0.002:  # learn the real px/s from this drag
                moved = (f2 - f) * song_len
                if moved * dx > 0:
                    pps = max(2.0, min(40.0, abs(dx) / abs(moved)))
        if cur_s is None:
            self.log.append(f"開始時間 {want}：讀不到進度條，請看截圖確認")
            self.music_warn = self.music_warn or f"音樂開始時間沒能確認（設定 {want}），請在手機上確認"
            return
        f = self._bar_pos()
        got = f * song_len if f is not None else cur_s
        self.log.append(f"開始時間：設定 {want}，畫面約 {int(got) // 60}:{int(got) % 60:02d}")
        if abs(got - start) > float(mc.get("start_tolerance_s", 2)) + 1:
            self.music_warn = self.music_warn or f"音樂開始時間只調到約 {int(got) // 60}:{int(got) % 60:02d}（設定 {want}）"

    def search_only(self, job: dict, opener: Callable) -> str:
        """Search IG's music library and back out without picking anything. Returns the matched row text or ""."""
        opener()
        box = self._search_box()
        if not box:
            self._close_sheet()
            raise IGStop("打開音樂後找不到搜尋欄（測試搜尋），已停止（沒有分享）。")
        self._tap_node(box, "搜尋音樂（只搜尋）")
        self._type(job["query"])
        self.sh("input", "keyevent", "66")
        self.sleep(float((self.cfg.get("music") or {}).get("search_wait_s", 3.5)))
        nodes = self.ui()
        self.shot("05p_song_probe")
        hit = self.find_song(nodes, job["title"], job["artist"])
        label = ""
        if hit:
            y1, y2 = hit["box"][1] - 40, hit["box"][3] + 120
            label = " / ".join((n["text"] or n["desc"]).strip() for n in nodes
                               if y1 <= n["box"][1] <= y2 and (n["text"] or n["desc"]).strip())[:120]
        self.log.append(("測試搜尋找到：" + label) if hit else f"測試搜尋：IG 找不到這首歌：{job['label']}")
        self._close_sheet()
        return label

    def add_music(self, job: dict | None, opener: Callable | None = None, verify_pill: bool = True) -> str:
        """Search IG's music library for the configured song, set start & clip length, press 完成.
        Returns "" on success / no music; raises MusicNotFound when IG doesn't have the song."""
        if not job:
            self.log.append("沒有設定音樂，不加")
            return ""
        mc = self.cfg.get("music") or {}
        if opener:
            opener()
        else:
            pill, attached = self._music_pill(self.ui())
            for _ in range(4):  # the editor may still be laying out: the pill belongs at the top of the screen
                if not pill or _top_pill(pill):
                    break
                self.sleep(1.0)
                pill, attached = self._music_pill(self.ui())
            if pill and not _top_pill(pill):
                pill = None  # never tap a misplaced pill (it would land on the image); use open_xy instead
            if attached:
                raise IGStop("加音樂前畫面上已經有音樂，已停止（沒有分享）。")
            if pill:
                self._tap_node(pill, "音樂")
            else:
                x, y = mc.get("open_xy") or [540, 175]
                self.tap(int(x), int(y), "音樂")
        box = self._search_box()
        if not box:
            self.shot("STOP_music_search")
            self._close_sheet()
            raise IGStop("打開音樂後找不到搜尋欄，已停止（沒有分享）。請看截圖。")
        self._tap_node(box, "搜尋音樂")
        self._type(job["query"])
        self.sh("input", "keyevent", "66")
        self.sleep(float(mc.get("search_wait_s", 3.5)))
        self.shot("05c_music_search")
        nodes = self.ui()
        hit = self.find_song(nodes, job["title"], job["artist"])
        if not hit:
            self._close_sheet()
            raise MusicNotFound(f"IG 找不到這首歌：{job['label']}（沒有改選別首）")
        song_len = self._row_length(nodes, hit)
        self.music_found = self._row_label(nodes, hit)
        self.log.append(f"IG 找到：{self.music_found}")
        for _ in range(3):  # the first tap may only close the keyboard
            self._tap_node(hit, f"歌曲 {job['title']}")
            nodes = self.ui()
            if self._chip(nodes) or any(n.get("rid") == "music_scrubber" for n in nodes):
                break
            hit = self.find_song(nodes, job["title"], job["artist"]) or hit
        # Clip length
        dur = int(job["dur_s"])
        chip = self._chip(self.ui())
        if chip:
            use = self._set_length(chip, dur)
            job = dict(job, dur_s=use)
        else:
            self.log.append("沒看到片段長度選項（IG 可能自動決定長度），請看截圖確認")
        # Start time
        start = int(job["start_s"])
        if start or song_len:
            self._set_start(start, song_len)
        self.shot("05d_music_trim")
        done = find_text(self.ui(), mc.get("done_text") or ["完成", "Done"])
        if not done:
            self.shot("STOP_music_done")
            self._close_sheet()
            raise IGStop("設定音樂後找不到「完成」，已停止加音樂（沒有分享）。")
        self._tap_node(done, "音樂完成")
        _, attached = self._music_pill(self.ui()) if verify_pill else (None, True)
        self.shot("05e_music")
        if not attached:
            raise IGStop("按完成後畫面上沒有音樂，已停止（沒有分享）。請看 05e_music 截圖。")
        self.log.append(f"已加音樂：{job['label']}，{job['dur_s']} 秒")
        return ""

    def caption_music(self, job: dict | None) -> None:
        """Add the configured post song from the caption screen (「新增音樂」 row → music browser → search)."""
        if not job:
            self.log.append("沒有設定音樂，不加")
            return
        words = (self.cfg.get("music") or {}).get("caption_row_text") or ["新增音樂", "Add music"]
        on_caption = lambda nodes: any(n.get("rid") in ("music_row_title", "caption_input_text_view") for n in nodes)

        def opener():
            row = None
            for _ in range(3):
                nodes = self.ui()
                row = next((n for n in nodes if n.get("rid") == "music_row_title"), None) or find_text(nodes, words)
                if row:
                    break
                self.sleep(1.0)
            if not row:
                self.shot("STOP_caption_music")
                raise IGStop("文案頁找不到「新增音樂」，已停止（沒有分享）。")
            self._tap_node(row, "新增音樂")

        self.home_check = on_caption
        self.add_music(job, opener=opener, verify_pill=False)
        nodes = self.ui()
        for _ in range(3):
            if on_caption(nodes):
                break
            self.sleep(1.0)
            nodes = self.ui()
        title = _norm(job["title"])
        if not any(title and title in _norm((n.get("text") or "") + (n.get("desc") or "")) for n in nodes):
            self.shot("STOP_caption_music_check")
            raise IGStop("加音樂後文案頁沒有顯示這首歌，已停止（沒有分享）。")
        self.shot("06b_caption_music")
        self.log.append("文案頁已顯示音樂：" + job["label"])

    def publish_post(self) -> None:
        """Tap IG's 「分享」 on the caption screen. Only when the officer ticked 「直接發佈貼文」 and
        confirmed in the dashboard; tapped once, then we wait for the upload instead of retrying."""
        words = ["分享", "Share"]
        nodes = self.ui()
        btn = next((n for n in nodes if (n["text"] or n["desc"]).strip() in words and n["box"][1] > 1500), None) \
            or next((n for n in nodes if (n["text"] or n["desc"]).strip() in words), None)
        if not btn:
            self.shot("STOP_no_share_button")
            raise IGStop("找不到「分享」鈕，沒有發佈（草稿也沒存）。請看截圖。")
        x = (btn["box"][0] + btn["box"][2]) // 2
        y = (btn["box"][1] + btn["box"][3]) // 2
        self.sh("input", "tap", str(x), str(y))
        self.log.append(f"點 分享（發佈貼文） ({x},{y})")
        for _ in range(15):
            self.sleep(2.0)
            left = [n for n in self.ui() if (n["text"] or n["desc"]).strip() in words and n["box"] == btn["box"]]
            if self.dump_ok and not left:
                self.log.append("貼文已送出（分享頁已關閉）")
                return
        raise IGStop("按了「分享」但畫面沒有離開，請看手機確認是否已發佈（不會重按）。")

    def save_draft(self) -> None:
        words = self.cfg.get("draft_text") or ["儲存草稿", "Save draft"]
        for i in range(int(self.cfg.get("max_back", 4))):
            self.sh("input", "keyevent", "4")
            self.sleep(1.5)
            n = find_text(self.ui(), words)
            if n:
                x = (n["box"][0] + n["box"][2]) // 2
                y = (n["box"][1] + n["box"][3]) // 2
                self.tap(x, y, "儲存草稿")
                self.log.append("已存成草稿")
                return
        self.shot("STOP_no_draft_button")
        raise IGStop("按返回後沒看到「儲存草稿」，已停止（沒有分享）。請看截圖。")


def paste_via_scrcpy(cfg: dict, caption: str) -> None:
    """Put the caption on the Windows clipboard, open scrcpy briefly and press Ctrl+V so scrcpy syncs
    the clipboard to the phone and pastes into the focused caption field. This shows a window (~5 s)."""
    import tempfile
    extra = "".join(f"'{a}'," for a in (cfg.get("scrcpy_args") or ["--no-audio"]))
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(caption.strip())
        cap_path = f.name
    ps = (
        f"$c = Get-Content -Raw -Encoding UTF8 '{cap_path}'; Set-Clipboard -Value $c.TrimEnd(); "
        f"$p = Start-Process -FilePath '{cfg.get('scrcpy')}' -ArgumentList {extra}'--window-title=VentureCrewIG' -PassThru; "
        "Start-Sleep -Seconds 5; $w = New-Object -ComObject WScript.Shell; "
        "if ($w.AppActivate('VentureCrewIG')) { Start-Sleep -Milliseconds 700; $w.SendKeys('^v'); Start-Sleep -Seconds 3 } "
        "else { Stop-Process -Id $p.Id; throw 'scrcpy window not found' }; Stop-Process -Id $p.Id"
    )
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=60)
    finally:
        Path(cap_path).unlink(missing_ok=True)
    if r.returncode != 0:
        raise IGStop("貼上文案失敗：" + r.stderr.decode("utf-8", "replace").strip()[:200])


def save_ig_draft(cards: list[Path], caption: str, shots: Path, cfg: dict | None = None,
                  runner: Callable = _default_runner, sleep: Callable = time.sleep,
                  paste: Callable | None = None, music: dict | None = None, publish: bool = False) -> dict:
    """music: ig_music.to_job(...) for 貼文, or None for no music.
    publish=True (dashboard 「直接發佈貼文」 + confirm only): tap 分享 instead of saving a draft."""
    cfg = cfg or load_config()
    music_error = ""
    cards = [Path(c) for c in cards]
    if len(cards) != 4:
        raise IGStop(f"需要 ig_01～ig_04 四張圖，目前 {len(cards)} 張。")
    dev = devices(cfg, runner)
    if not dev["ok"]:
        raise IGStop(dev["message"])
    ph = Phone(cfg, shots, runner, sleep, paste)
    t0 = time.time()
    try:
        ph.check_screen()
        ph.push_images(cards)
        for st in cfg.get("steps") or []:
            do = st.get("do")
            if do == "launch":
                ph.launch()
            elif do == "shot":
                ph.shot(st["name"])
            elif do == "tap":
                x, y = st["xy"]
                ph.tap(int(x), int(y), st.get("name") or f"{x},{y}", verified=not st.get("guard"))
                if ph.fast and st.get("wait"):
                    ph.sleep(float(st["wait"]))
            elif do == "paste_caption":
                ph.paste_fn(cfg, caption)
                ph.log.append(f"已貼上文案（{len(caption)} 字）")
                ph.sleep(1.5)
            elif do == "no_music":
                ph.no_music()
            elif do == "music":
                try:
                    ph.add_music(music)
                except MusicNotFound as e:  # stop only the music step; draft continues with no music
                    music_error = str(e)
                    ph.log.append(music_error)
            elif do == "caption_music":
                # Post music from the caption screen's 「新增音樂」 row: on the edit screen IG's audio pill only
                # applies its own suggestion (no search). Publishing never goes ahead with music missing.
                try:
                    ph.caption_music(music)
                except MusicNotFound as e:
                    if publish:
                        raise IGStop(str(e) + "；沒有發佈（設定了音樂就不會無音樂發佈）。")
                    music_error = str(e)
                    ph.log.append(music_error)
            elif do == "save_draft":
                if publish:
                    ph.publish_post()
                    break  # the remaining steps belong to the draft flow
                ph.save_draft()
            elif do == "wait":
                ph.sleep(float(st.get("s", 1)))
            else:
                raise IGStop(f"設定檔有不認得的步驟：{do}")
        ok, err = True, ""
    except IGStop as e:
        ok, err = False, str(e)
    if ph.fast:
        try:
            ph.shot("final", key=True)
        except Exception:
            pass
    res = {"ok": ok, "error": err, "music_error": music_error, "published": bool(publish and ok), "seconds": round(time.time() - t0, 1), "log": ph.log, "shots": ph.shot_files,
           "at": time.strftime("%Y-%m-%d %H:%M")}
    (Path(shots) / "ig_result.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    return res
