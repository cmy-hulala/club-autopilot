from html.parser import HTMLParser
from pathlib import Path

from autopilot import i18n
from autopilot.pipeline import run_pipeline
from autopilot.web import ACTION_META, FIELDS, create_app

EV = "tests/fixtures/2026-11-25-candice.yaml"
SLUG = "2026-11-25-candice"
CJK = i18n.CJK
VOID = ("input", "img", "br", "meta", "link", "hr", "source")


class _Texts(HTMLParser):
    SKIP = {"script", "style", "pre", "textarea", "iframe"}

    def __init__(self):
        super().__init__()
        self.stack, self.out = [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        skip = tag in self.SKIP or "data-no-i18n" in a or bool(self.stack and self.stack[-1])
        if not skip:
            for k in ("placeholder", "title", "alt", "data-confirm"):
                if a.get(k):
                    self.out.append(a[k])
        if tag not in VOID:
            self.stack.append(skip)

    def handle_endtag(self, tag):
        if tag not in VOID and self.stack:
            self.stack.pop()

    def handle_data(self, data):
        if data.strip() and not (self.stack and self.stack[-1]):
            self.out.append(data)


def _page(tmp_path, lang="en"):
    run_pipeline(EV, "preview", tmp_path)
    c = create_app(out_root=tmp_path, events_dir=Path(EV).parent).test_client()
    c.set_cookie("lang", lang)
    return c, c.get(f"/event/{SLUG}").get_data(as_text=True)


def test_toggle_present_and_cookie_sets_lang(tmp_path):
    c, html = _page(tmp_path, "en")
    assert 'data-setlang="en"' in html and 'data-setlang="zh"' in html
    assert 'data-lang="en"' in html and 'lang="en"' in html and "i18n-data" in html
    c.set_cookie("lang", "zh")
    assert 'data-lang="zh"' in c.get(f"/event/{SLUG}").get_data(as_text=True)


def test_every_ui_string_on_the_page_translates(tmp_path):
    _, html = _page(tmp_path)
    p = _Texts()
    p.feed(html)
    left = sorted({t.strip() for t in p.out if CJK.search(i18n.translate(t))})
    assert not left, left


def test_step_names_statuses_and_confirms_translate():
    strings = [x for _, lab, conf in ACTION_META for x in (lab, conf)] + [lab for _, lab in FIELDS]
    strings += ["未執行", "執行中", "已完成", "失敗", "已略過", "即將推出",
                "依序執行全部步驟：已完成的會略過，不會寄信、不會發 IG、不會推 LINE。開始？",
                "確定要用社團帳號對這則貼文按讚＋轉發？（公開動作）",
                "確定要直接在社團 IG 發佈這則貼文？（公開動作）",
                "因為「建立課前／課後表單（個人帳號）」沒有完成，先略過；修好後按重跑。",
                "跑完了，但有 1 項失敗、2 項略過，請按該項的「重跑」。",
                "課前報名 3 人；課後回饋 — 份（只顯示人數）。",
                "預告信 密件副本 20 人，已排除 1 人（新建）",
                "手機螢幕鎖著，請先解鎖手機（保持亮著）再按一次。"]
    left = [s for s in strings if CJK.search(i18n.translate(s))]
    assert not left, left


def test_generated_content_stays_chinese(tmp_path):
    _, html = _page(tmp_path)
    assert '<pre data-no-i18n class="copy"' in html
    assert i18n.translate("hello") == "hello"
    assert i18n.translate("預告信", "zh") == "預告信"
