"""조회 결과를 사전 항목처럼 구조를 살려 그리는 HTML(문서형). 표준 라이브러리만 사용.

urimal_html.py 는 결과 글자를 `<pre>` 한 덩어리로 넣는데, 이 모듈은 DB 에서 항목별로 읽어 다음처럼 그린다.

  - 어휘 역사: 표기 칸을 화살표로 이어, 표기마다 나타난 세기를 작게 보여 준다(원문 세기 목록은 마우스를 올리면 나온다).
  - 용례: 표제어에 해당하는 부분(DB 의 example_hl 에 남아 있는 <strong>)을 강조한다.
  - 긴 설명(어휘 역사 설명·부가 정보·기타 정보): 문단(##)과 줄로 나누고 [예]·[현대어 역] 줄은 작고 옅게 구분한다.
  - 용례는 세기별로 접었다 펼 수 있고(<details>), '모두 펼치기/접기' 버튼이 있다. 용례가 적으면 처음부터 펼쳐 둔다.
  - 같은 표제어가 둘 이상(동형이의어)이면 맨 위에 바로가기 목차를 둔다.
  - 항목마다 "우리말역사어휘사전 ↗"(국립국어원 누리집의 그 항목 화면). 이어 붙인 설명·끝내 잘린 설명·내려받기 파일에서만 온 표제어에는 그 사실을 작게 알린다.
  - 링크(link_base 가 있을 때만; 로컬 링크 서버가 보내는 화면): 관련어는 그 말이 표제어일 때, 어휘 역사의 옛 표기와 '그 밖의 표기'는 다른
    표제어에도 쓰인 표기일 때만 눌러서 그 조회 화면으로 간다(없는 곳으로 가는 링크는 만들지 않는다). 저장된 파일(file://)에는 링크가 없다.

글꼴은 urimal_html 의 것을 그대로 쓴다(옛한글·한자 자형). 글자는 DB 에서 읽은 그대로이고 HTML 로만 이스케이프한다(모델이 다시 쓰지 않는다).
"""
import html
import re
import urllib.parse

import urimal_html as uh
import urimal_lookup as ul

OPEN_ALL_UNDER = 8          # 한 항목의 용례가 이 개수 이하이면 세기별 접기를 처음부터 펼쳐 둔다
_CENT = re.compile(r"(\d+)세기")


def esc(text):
    return html.escape(text or "", quote=False)


class Ctx:
    """그릴 때 쓰는 문맥: DB 와 링크 기준 주소(없으면 링크를 만들지 않는다)."""

    def __init__(self, db, link_base=None):
        self.db, self.base = db, link_base
        self._cache = {}

    def href(self, by, q, exclude_code=None):
        """조회 화면으로 가는 링크. 갈 곳이 없으면 None. by: form(표제어로 있을 때) | hist(다른 표제어에도 쓰인 옛 표기일 때)."""
        if self.base is None:
            return None
        q = ul.norm(q)
        if not q or len(q) > 40:
            return None
        key = (by, q, exclude_code)
        if key not in self._cache:
            codes = ul.find_form(self.db, q) if by == "form" else [c for c in ul.find_hist(self.db, q) if c != exclude_code]
            self._cache[key] = f"{self.base}/s?by={by}&amp;q={urllib.parse.quote(q)}" if codes else None
        return self._cache[key]

    def link(self, text, by, q, exclude_code=None, cls=""):
        h = self.href(by, q, exclude_code)
        c = f' class="{cls}"' if cls else ""
        return f'<a{c} href="{h}">{esc(text)}</a>' if h else esc(text)


def split_words(text):
    """'입씨름, 팔시름' → ['입씨름', '팔시름'] (쉼표·세미콜론으로 나눈다)."""
    return [w.strip() for w in re.split(r"[,，;]", text or "") if w.strip()]


def compress_centuries(raw):
    """'15세기, 16세기, 17세기, 미상' → '15~17세기 · 미상'. 이어지지 않으면 '15세기 · 17세기'. 알 수 없는 말은 그대로 뒤에 붙인다."""
    if not raw:
        return ""
    nums, rest = [], []
    for part in (p.strip() for p in raw.split(",")):
        m = _CENT.fullmatch(part)
        if m:
            nums.append(int(m.group(1)))
        elif part:
            rest.append(part)
    nums = sorted(set(nums))
    runs, start = [], None
    for i, n in enumerate(nums):
        if start is None:
            start = n
        if i + 1 == len(nums) or nums[i + 1] != n + 1:
            runs.append((start, n))
            start = None
    out = []
    for a, b in runs:
        out.append(f"{a}세기" if a == b else f"{a}~{b}세기")
    return " · ".join(out + rest)


def paragraphs(text):
    """긴 설명을 문단(##)과 줄로 나눠 <p> 로. [예]·[현대어 역]·[예1] 로 시작하는 줄은 class="ex"."""
    if not text:
        return ""
    out = []
    for block in text.split("##"):
        for line in block.split("\n"):
            line = line.strip()
            if not line:
                continue
            cls = ' class="ex"' if re.match(r"^\[(예\d*|현대어 ?역)\]", line) else ""
            out.append(f"<p{cls}>{esc(line)}</p>")
    return "\n".join(out)


def example_html(hl):
    """example_hl(표제어 부분만 <strong>…</strong>)을 이스케이프하면서 강조 태그만 되살린다."""
    s = html.escape(hl or "", quote=False)
    return s.replace("&lt;strong&gt;", "<strong>").replace("&lt;/strong&gt;", "</strong>").replace("\n", "<br>")


def load_entry(db, code):
    """한 항목을 그리는 데 필요한 것을 DB 에서 읽는다. 없으면 None."""
    e = db.execute("SELECT * FROM entries WHERE target_code=?", (code,)).fetchone()
    if e is None:
        return None
    d = dict(e)
    d["homonyms"] = db.execute("SELECT COUNT(*) FROM entries WHERE form=?", (e["form"],)).fetchone()[0]
    d["senses"] = [dict(r) for r in db.execute(
        "SELECT idx, definition FROM senses WHERE target_code=? ORDER BY idx", (code,))]
    d["forms"] = [dict(r) for r in db.execute(
        "SELECT form, centuries FROM forms_changed WHERE target_code=? ORDER BY idx", (code,))]
    groups = []
    n_examples = 0
    for g in db.execute("SELECT idx, definition FROM hist_groups WHERE target_code=? ORDER BY idx", (code,)):
        cents = []
        for c in db.execute("SELECT id, century, form FROM hist_centuries WHERE target_code=? AND group_idx=? ORDER BY idx",
                            (code, g["idx"])):
            exs = [dict(r) for r in db.execute(
                "SELECT example_hl, original, translation, source FROM examples WHERE century_id=? ORDER BY idx", (c["id"],))]
            n_examples += len(exs)
            cents.append({"century": c["century"], "form": c["form"], "examples": exs})
        groups.append({"definition": g["definition"], "centuries": cents})
    d["groups"], d["n_examples"] = groups, n_examples
    d["refs"] = [dict(r) for r in db.execute(
        "SELECT authors, year, title, publisher FROM refs WHERE target_code=? ORDER BY idx", (code,))]
    return d


# ---------------------------------------------------------------- 조각
def head_html(d, ctx=None):
    num = f"<sup>{d['word_number']}</sup>" if d["word_number"] != 1 or d["homonyms"] > 1 else ""
    links = (f'<a class="orig" href="{html.escape(ul.original_url(d["form"], d["target_code"]))}" '
             f'target="_blank" rel="noopener noreferrer">우리말역사어휘사전 ↗</a>')
    return (f'<header><h2>{esc(d["form"])}{num}</h2>'
            f'<span class="meta">{esc(d["type"])} · #{d["target_code"]}</span>{links}</header>')


def source_note(d):
    """자료 출처가 달라 원본과 다를 수 있는 항목에 붙이는 안내."""
    if d.get("source") == "download":
        return ('<p class="warn">※ 이 표제어는 Open API가 내주지 않아 누리집 전체 내려받기 파일의 내용입니다. '
                '큰따옴표(“ ”)·줄바꿈·용례 강조가 없습니다.</p>')
    return ""


def field_note(d, col):
    """긴 설명 필드(col)가 이어 붙인 글이거나 끝내 잘린 글이면 그 사실을 알린다."""
    if col in (d.get("truncated_fields") or "").split(","):
        return ('<p class="warn">※ 이 글은 중간에서 잘려 있습니다(Open API가 256자까지만 주고 뒷부분을 이어 붙이지 못했습니다). '
                f'전문은 <a href="{html.escape(ul.original_url(d["form"], d["target_code"]))}" target="_blank" rel="noopener noreferrer">우리말역사어휘사전 ↗</a>에서 볼 수 있습니다.</p>')
    if col in (d.get("merged_fields") or "").split(","):
        return ('<p class="warn">※ 뒷부분은 누리집 전체 내려받기 파일에서 이어 붙인 것이라 큰따옴표(“ ”)와 줄바꿈이 없습니다.</p>')
    return ""


def senses_html(d):
    items = []
    for s in d["senses"]:
        lines = [ln.strip() for ln in s["definition"].split("\n") if ln.strip()]
        body = esc(lines[0]) if lines else ""
        for ln in lines[1:]:
            body += f'<br><span class="sub">{esc(ln)}</span>'
        items.append(f"<li>{body}</li>")
    return f'<ol class="senses">{"".join(items)}</ol>' if items else ""


def timeline_html(d, ctx=None):
    if not d["forms"]:
        return ""
    ctx = ctx or Ctx(None)
    items = []
    for f in d["forms"]:
        raw = f["centuries"] or ""
        word = ctx.link(f["form"], "hist", f["form"], exclude_code=d["target_code"])     # 다른 표제어에도 쓰인 표기만 링크
        items.append(f'<li title="{html.escape(raw)}"><span class="f">{word}</span>'
                     f'<span class="c">{esc(compress_centuries(raw))}</span></li>')
    shown = {ul.norm(f["form"]) for f in d["forms"]}
    extra = [w for w in split_words(d["other_forms"]) if ul.norm(w) not in shown]
    more = ""
    if extra:
        words = " · ".join(ctx.link(w, "hist", w, exclude_code=d["target_code"]) for w in extra)
        more = f'<p class="also">그 밖의 표기: {words}</p>'
    return f'<section><h3>어휘 역사</h3><ol class="timeline">{"".join(items)}</ol>{more}</section>'


def notes_html(d, ctx=None):
    ctx = ctx or Ctx(None)
    out = []
    for label, col in (("어휘 역사 설명", "language_info"), ("부가 정보", "other_info"), ("기타 정보", "annotations")):
        body = paragraphs(d[col])
        if body:
            out.append(f'<section class="note"><h3>{label}</h3>{body}{field_note(d, col)}</section>')
    if d["related_forms"]:
        words = split_words(d["related_forms"].replace("\n", " "))
        body = ", ".join(ctx.link(w, "form", w) for w in words)
        out.append(f'<section class="note"><h3>관련어</h3><p>{body}</p></section>')
    return "".join(out)


def examples_html(d):
    if not d["n_examples"]:
        return ""
    open_attr = " open" if d["n_examples"] <= OPEN_ALL_UNDER else ""
    out = []
    for g in d["groups"]:
        if not g["centuries"]:
            continue
        if g["definition"] and len(d["groups"]) > 1:
            out.append(f'<h4>{esc(g["definition"])}</h4>')
        for c in g["centuries"]:
            items = []
            for x in c["examples"]:
                extra = ""
                if x["original"]:
                    extra += f'<div class="aux"><b>원문</b> {esc(x["original"].replace(chr(10), " "))}</div>'
                if x["translation"]:
                    extra += f'<div class="aux"><b>번역</b> {esc(x["translation"].replace(chr(10), " "))}</div>'
                items.append(f'<li><span class="txt">{example_html(x["example_hl"])}</span> '
                             f'<cite>{esc(x["source"])}</cite>{extra}</li>')
            out.append(f'<details{open_attr}><summary><span class="cent">{esc(c["century"])}</span> '
                       f'<span class="cform">{esc(c["form"])}</span> '
                       f'<span class="count">{len(c["examples"])}</span></summary><ul class="ex-list">{"".join(items)}</ul></details>')
    toggle = ('<button type="button" class="toggle" data-open="1">모두 펼치기</button>'
              if not open_attr else '<button type="button" class="toggle" data-open="0">모두 접기</button>')
    return (f'<section class="examples"><h3>용례 <span class="count">{d["n_examples"]}</span> {toggle}</h3>'
            f'{"".join(out)}</section>')


def refs_html(d):
    if not d["refs"]:
        return ""
    items = []
    for r in d["refs"]:
        who = (r["authors"] or "").strip()
        year = f" ({esc(r['year'])})" if r["year"] else ""
        title = esc(r["title"] or "")
        pub = f", {esc(r['publisher'])}" if r["publisher"] else ""
        items.append(f"<li>{esc(who)}{year} {title}{pub}</li>")
    return f'<section class="refs"><h3>참고 자료</h3><ul>{"".join(items)}</ul></section>'


def entry_html(d, ctx=None):
    return (f'<article id="e{d["target_code"]}">{head_html(d, ctx)}{source_note(d)}{senses_html(d)}{timeline_html(d, ctx)}'
            f'{notes_html(d, ctx)}{examples_html(d)}{refs_html(d)}</article>')


def toc_html(entries):
    """동형이의어가 둘 이상일 때 맨 위 바로가기 목차."""
    if len(entries) < 2:
        return ""
    items = []
    for d in entries:
        first = (d["senses"][0]["definition"] if d["senses"] else "").replace("\n", " ")
        gist = first if len(first) <= 22 else first[:22] + "…"
        items.append(f'<li><a href="#e{d["target_code"]}"><b>{esc(d["form"])}</b><sup>{d["word_number"]}</sup> '
                     f'<span class="sub">{esc(gist)}</span></a></li>')
    return f'<nav class="toc" aria-label="동형이의어"><ol>{"".join(items)}</ol></nav>'


def list_html(db, codes, ctx=None):
    ctx = ctx or Ctx(db)
    items = []
    for code in codes:
        e = db.execute("SELECT form, word_number, type FROM entries WHERE target_code=?", (code,)).fetchone()
        s = db.execute("SELECT definition FROM senses WHERE target_code=? ORDER BY idx LIMIT 1", (code,)).fetchone()
        if e is None:
            continue
        dfn = esc((s[0] if s else "").replace("\n", " "))
        items.append(f'<li><b>{ctx.link(e["form"], "form", e["form"])}</b><sup>{e["word_number"]}</sup> '
                     f'<span class="meta">{esc(e["type"])} · #{code}</span><div class="sub">{dfn}</div></li>')
    return f'<ul class="hits">{"".join(items)}</ul>'


STYLE = """
  :root { color-scheme: light dark; --fg:#1d1d1f; --mute:#6b6b70; --line:#e2e2e6; --accent:#9a3412; --soft:#f6f5f2; --hl:#fde9c8; }
  @media (prefers-color-scheme: dark) { :root { --fg:#e8e6e3; --mute:#a09d98; --line:#3a3936; --accent:#f0a46c; --soft:#262523; --hl:#4a3a1e; } }
  html { -webkit-text-size-adjust: 100%; }
  body { font-family: __STACK__; color: var(--fg); background: Canvas; font-size: 17px; line-height: 1.8;
         max-width: 50rem; margin: 2.2rem auto 3rem; padding: 0 1.1rem; word-break: keep-all; overflow-wrap: break-word; }
  .site { color: var(--mute); font-size: 13px; letter-spacing: .02em; margin-bottom: .4rem; }
  .lead { color: var(--mute); margin: .2rem 0 1.2rem; }
  article { padding: 1.4rem 0 1.6rem; border-top: 1px solid var(--line); }
  article:first-of-type { border-top: 2px solid var(--fg); }
  header { display: flex; align-items: baseline; gap: .8rem; flex-wrap: wrap; }
  h2 { font-size: 2rem; line-height: 1.25; margin: 0; font-weight: 700; }
  h2 sup, .hits sup { font-size: .5em; color: var(--accent); margin-left: .1em; }
  .meta { color: var(--mute); font-size: 13px; }
  h3 { font-size: 13px; letter-spacing: .06em; color: var(--mute); font-weight: 600; margin: 1.5rem 0 .35rem; }
  h4 { font-size: 15px; margin: 1rem 0 .2rem; color: var(--accent); }
  ol.senses { margin: .7rem 0 0; padding-left: 1.5rem; }
  ol.senses li { margin: .2rem 0; }
  .sub { color: var(--mute); font-size: 15px; }
  ol.timeline { list-style: none; display: flex; flex-wrap: wrap; align-items: stretch; gap: .5rem .2rem; margin: .3rem 0 0; padding: 0; }
  ol.timeline li { display: flex; flex-direction: column; padding: .3rem .8rem; background: var(--soft); border: 1px solid var(--line);
                   border-radius: .5rem; min-width: 3.2rem; }
  ol.timeline li + li { margin-left: 1.3rem; position: relative; }
  ol.timeline li + li::before { content: "→"; position: absolute; left: -1.25rem; top: 50%; transform: translateY(-50%); color: var(--mute); }
  ol.timeline .f { font-size: 1.15rem; line-height: 1.5; }
  ol.timeline .c { font-size: 12px; color: var(--mute); line-height: 1.4; }
  .note p { margin: .45rem 0; }
  .note p.ex { color: var(--mute); font-size: 15px; border-left: 3px solid var(--line); padding-left: .8rem; }
  .count { display: inline-block; font-size: 12px; font-weight: 500; color: var(--mute); background: var(--soft); border: 1px solid var(--line);
           border-radius: 999px; padding: 0 .55rem; line-height: 1.5; }
  .toggle { margin-left: .6rem; font: inherit; font-size: 12px; color: var(--mute); background: none; border: 1px solid var(--line);
            border-radius: .4rem; padding: .05rem .6rem; cursor: pointer; }
  .toggle:hover { color: var(--fg); border-color: var(--mute); }
  details { border-top: 1px solid var(--line); }
  details:last-of-type { border-bottom: 1px solid var(--line); }
  summary { cursor: pointer; padding: .45rem 0; list-style: none; display: flex; align-items: baseline; gap: .6rem; }
  summary::-webkit-details-marker { display: none; }
  summary::before { content: "▸"; color: var(--mute); font-size: 12px; width: .8rem; }
  details[open] > summary::before { content: "▾"; }
  .cent { font-weight: 600; min-width: 3.6rem; }
  .cform { color: var(--accent); }
  ul.ex-list { margin: .1rem 0 .7rem; padding-left: 1.4rem; }
  ul.ex-list li { margin: .5rem 0; }
  ul.ex-list strong { background: var(--hl); font-weight: 600; border-radius: .2rem; padding: 0 .08em; }
  cite { color: var(--mute); font-style: normal; font-size: 13px; white-space: nowrap; }
  .aux { color: var(--mute); font-size: 15px; margin-left: .2rem; }
  .aux b { font-size: 11px; letter-spacing: .06em; border: 1px solid var(--line); border-radius: .3rem; padding: 0 .35rem; margin-right: .3rem; }
  .refs ul { margin: .2rem 0; padding-left: 1.3rem; color: var(--mute); font-size: 15px; }
  ul.hits { list-style: none; padding: 0; } ul.hits li { padding: .6rem 0; border-top: 1px solid var(--line); }
  a { color: var(--accent); text-decoration: none; border-bottom: 1px solid transparent; }
  a:hover { border-bottom-color: var(--accent); }
  .meta { border: 1px solid var(--line); border-radius: 999px; padding: 0 .6rem; font-size: 12px; line-height: 1.6; }
  nav.toc { margin: 1rem 0 .2rem; padding: .5rem .9rem; background: var(--soft); border: 1px solid var(--line); border-radius: .6rem; }
  nav.toc ol { list-style: none; margin: 0; padding: 0; }
  nav.toc li { padding: .15rem 0; }
  nav.toc a { color: var(--fg); display: block; } nav.toc a:hover { border-bottom-color: transparent; background: var(--hl); }
  nav.toc sup { color: var(--accent); }
  .warn { color: var(--mute); font-size: 13px; margin: .3rem 0 0; padding-left: .7rem; border-left: 3px solid var(--hl); }
  a.orig { margin-left: auto;  font-size: 12px; color: var(--mute); border: 1px solid var(--line); border-radius: 999px; padding: 0 .65rem; }
  a.orig:hover { color: var(--accent); border-color: var(--accent); }
  .also { color: var(--mute); font-size: 14px; margin: .5rem 0 0; }
  ol.timeline .f a { color: var(--accent); }
  footer { margin-top: 2.2rem; padding-top: .8rem; border-top: 1px solid var(--line); font-size: 13px; color: var(--mute); }
  @media print { details { display: block; } details > summary::before { content: ""; } .toggle { display: none; } }
"""

SCRIPT = """<script>
document.querySelectorAll('.toggle').forEach(function (b) {
  b.addEventListener('click', function () {
    var sec = b.closest('.examples'), open = b.dataset.open === '1';
    sec.querySelectorAll('details').forEach(function (d) { d.open = open; });
    b.dataset.open = open ? '0' : '1';
    b.textContent = open ? '모두 접기' : '모두 펼치기';
  });
});
</script>"""


def render_page(db, resolved, title, font_url=None, link_base=None):
    """resolve() 결과를 문서형 HTML 한 장으로. link_base 가 있으면(예: "" = 같은 서버의 /s?… 주소) 관련어·옛 표기·목록의 표제어가 링크가 된다."""
    ctx = Ctx(db, link_base)
    parts = []
    if resolved.get("note"):
        parts.append(f'<p class="lead">{esc(resolved["note"])}</p>')
    if resolved["kind"] == "list":
        parts.append(list_html(db, resolved["codes"], ctx))
    else:
        entries = [d for d in (load_entry(db, c) for c in resolved["codes"]) if d]
        parts.append(toc_html(entries))
        parts.extend(entry_html(d, ctx) for d in entries)
    if resolved.get("more"):
        parts.append(f'<p class="lead">동형 표제어 {resolved["more"][0]}개 중 {resolved["more"][1]}개만 표시했습니다.</p>')
    css = uh._font_face_css(font_url) + STYLE.replace("__STACK__", uh.FONT_STACK)
    return (f'<!doctype html>\n<html lang="ko">\n<meta charset="utf-8">\n'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f'<title>{esc(title)} — 우리말역사어휘사전</title>\n<style>\n{css}</style>\n'
            f'<div class="site">우리말역사어휘사전</div>\n{"".join(parts)}\n'
            f'<footer>{uh.ATTRIBUTION}</footer>\n{SCRIPT}\n')


def render_query(db, by, q, limit=50, max_entries=None, title=None, font_url=None, link_base=None):
    """검색 조건으로 바로 HTML 을 만든다. 찾지 못하면 urimal_lookup.Miss."""
    resolved = ul.resolve(db, by, q, limit, max_entries)
    return render_page(db, resolved, title or q, font_url, link_base)
