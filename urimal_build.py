#!/usr/bin/env python3
"""원본 XML(data/raw.sqlite) → 조회용 DB(data/urimal.sqlite) 빌드 (표준 라이브러리만 사용)

새 DB를 data/urimal.sqlite.new 에 만들어 검증하고, 통과하면 기존 DB를 data/urimal.prev.sqlite 로
옮긴 뒤 교체한다. 검증에 실패하면 기존 DB는 그대로 둔다.

표: entries(표제어) / senses(뜻풀이) / forms_changed(한눈에 보는 어휘 역사) /
    hist_groups(용례의 뜻 묶음) / hist_centuries(세기·표기별) / examples(용례) / refs(참고 자료)
    + entries_fts(trigram 전문 검색)
"""
import argparse
import os
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from urimal_fetch import parse_xml  # noqa: E402

ROOT = Path(__file__).resolve().parent
RAW_PATH = Path(os.environ.get("URIMAL_HISTORY_DB", ROOT / "data" / "raw.sqlite"))
OUT_PATH = ROOT / "data" / "urimal.sqlite"

SCHEMA = """
CREATE TABLE entries(
    target_code   INTEGER PRIMARY KEY,
    form          TEXT NOT NULL,       -- 표제어 (하이픈 포함 표기 그대로)
    word_number   INTEGER NOT NULL,    -- 어깨 번호
    type          TEXT NOT NULL,       -- 단어 | 구
    language_info TEXT,                -- 어휘 역사 설명
    other_info    TEXT,                -- 부가 정보
    annotations   TEXT,                -- 기타 정보
    other_forms   TEXT,                -- 역사 속 표기(쉼표로 이은 원문)
    related_forms TEXT,                -- 관련어(원문 표기: XML의 realted_forms)
    source        TEXT NOT NULL,       -- 'api'(Open API) | 'download'(누리집 전체 내려받기에만 있는 표제어)
    merged_fields TEXT                 -- API가 256자에서 자른 필드 중 뒷부분을 내려받기 파일로 채운 필드 이름(쉼표)
);
CREATE INDEX entries_form ON entries(form, word_number);

CREATE TABLE senses(
    target_code INTEGER NOT NULL REFERENCES entries,
    idx         INTEGER NOT NULL,
    definition  TEXT NOT NULL,         -- 연결 코드(word_no)를 뺀 뜻풀이, 줄바꿈은 \\n
    link_codes  TEXT,                  -- 뜻풀이 안의 어휘 링크 코드(word_no, 쉼표로 이음)
    PRIMARY KEY(target_code, idx)
);

CREATE TABLE forms_changed(            -- 한눈에 보는 어휘 역사
    target_code INTEGER NOT NULL REFERENCES entries,
    idx         INTEGER NOT NULL,
    form        TEXT NOT NULL,
    centuries   TEXT,                  -- '15세기, 16세기, 미상' 같은 원문
    PRIMARY KEY(target_code, idx)
);

CREATE TABLE hist_groups(              -- 용례의 뜻 묶음 (뜻이 하나면 definition 은 빈 문자열일 수 있음)
    target_code INTEGER NOT NULL REFERENCES entries,
    idx         INTEGER NOT NULL,
    definition  TEXT NOT NULL,
    PRIMARY KEY(target_code, idx)
);

CREATE TABLE hist_centuries(
    id          INTEGER PRIMARY KEY,
    target_code INTEGER NOT NULL REFERENCES entries,
    group_idx   INTEGER NOT NULL,
    idx         INTEGER NOT NULL,
    century     TEXT NOT NULL,         -- '18세기', '20세기', '미상'
    form        TEXT NOT NULL          -- 그 세기의 표기
);
CREATE INDEX hist_centuries_code ON hist_centuries(target_code);

CREATE TABLE examples(
    id          INTEGER PRIMARY KEY,
    century_id  INTEGER NOT NULL REFERENCES hist_centuries,
    target_code INTEGER NOT NULL,
    idx         INTEGER NOT NULL,
    example     TEXT NOT NULL,         -- 용례(태그 제거, 줄바꿈은 \\n)
    example_hl  TEXT NOT NULL,         -- 같은 용례, 표제어 강조를 <strong>…</strong> 로 남김
    original    TEXT,                  -- 한문 원문
    translation TEXT,                  -- 현대어 번역
    source      TEXT NOT NULL          -- 출전
);
CREATE INDEX examples_century ON examples(century_id);
CREATE INDEX examples_code ON examples(target_code);

CREATE TABLE refs(                     -- 참고 자료
    target_code INTEGER NOT NULL REFERENCES entries,
    idx         INTEGER NOT NULL,
    authors     TEXT,
    year        TEXT,
    title       TEXT,
    publisher   TEXT,
    PRIMARY KEY(target_code, idx)
);

CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);

-- 표제어·역사 속 표기·뜻풀이를 부분 문자열로 찾는다 (trigram: 3글자 이상)
CREATE VIRTUAL TABLE entries_fts USING fts5(
    form, historical_forms, definitions, tokenize='trigram');
"""

_BR = re.compile(r"[ \t]*\n[ \t]*")


def flat(e, keep_strong=False):
    """요소 안의 글을 평문으로. <br>은 줄바꿈, <strong>은 keep_strong 이면 남긴다. word_no는 뺀다."""
    out = []

    def walk(x):
        if x.tag.lower() == "br":
            out.append("\n")
        elif x.tag == "word_no":
            pass
        elif x.tag == "strong" and keep_strong:
            out.append("<strong>")
            out.append(x.text or "")
            for c in x:
                walk(c)
            out.append("</strong>")
        else:
            out.append(x.text or "")
            for c in x:
                walk(c)
        out.append(x.tail or "")

    out.append(e.text or "")
    for c in e:
        walk(c)
    return tidy("".join(out))


_LIT_WORD_NO = re.compile(r"<word_no>(.*?)</word_no>", re.S)
_LIT_BR = re.compile(r"<br\s*/?>", re.I)
_IGNORE = re.compile(r"[\s\u201c\u201d]")      # 공백과 큰따옴표: 내려받기 파일에는 큰따옴표가 없다


def tidy(text):
    """평문 안에 글자 그대로 들어 있는 <br>·<word_no>(내려받기 파일)를 API 결과와 같은 모양으로."""
    text = _LIT_BR.sub("\n", _LIT_WORD_NO.sub("", text)).replace("\u00a0", " ")
    return re.sub(r"\n{3,}", "\n\n", _BR.sub("\n", text)).strip()


def merge_tail(api_text, dl_text):
    """API 글이 256자에서 잘렸으면, 내려받기 글의 나머지 부분을 이어 붙인다.
    API 글(큰따옴표 포함)은 그대로 두고, 이어 붙인 뒷부분에는 큰따옴표·줄바꿈이 없다.
    앞부분이 서로 맞지 않으면 합치지 않는다. 반환: (글, 합쳤는지)"""
    if not api_text or not dl_text or len(api_text) < 255:
        return api_text, False
    want = _IGNORE.sub("", api_text)
    seen = 0
    for pos, ch in enumerate(dl_text):
        if not _IGNORE.match(ch):
            if seen >= len(want) or ch != want[seen]:
                if seen >= len(want):
                    rest = dl_text[pos:].replace("\u00a0", " ")
                    return (api_text + (" " if dl_text[pos - 1].isspace() else "") + rest.strip(), True) if rest.strip() else (api_text, False)
                return api_text, False
            seen += 1
    return api_text, False


def opt(e, tag):
    x = e.find(tag)
    if x is None:
        return None
    t = flat(x)
    return t or None


def build(raw_path, out_path):
    raw = sqlite3.connect(raw_path)
    if out_path.exists():
        out_path.unlink()
    db = sqlite3.connect(out_path)
    db.executescript(SCHEMA)
    n_entries = 0
    cid = eid = 0
    has_dl = bool(raw.execute("SELECT 1 FROM sqlite_master WHERE name='raw_download'").fetchone())
    dl = {}
    if has_dl:
        for code, blob in raw.execute("SELECT target_code, xml FROM raw_download"):
            dl[code] = ET.fromstring(zlib.decompress(blob).decode("utf-8"))
    api = {code: blob for code, blob in raw.execute("SELECT target_code, xml FROM raw_xml")}
    for code in sorted(set(api) | set(dl)):
        if code in api:
            item, source = parse_xml(zlib.decompress(api[code]).decode("utf-8")).find("item"), "api"
        else:
            item, source = dl[code], "download"
        w = item.find("word_info")
        oi = item.find("origin/origin_info")
        texts, merged = {}, []
        for tag in ("language_info", "other_info", "annotations"):
            text = opt(oi, tag)
            if source == "api" and code in dl:
                text, did = merge_tail(text, opt(dl[code].find("origin/origin_info"), tag))
                if did:
                    merged.append(tag)
            texts[tag] = text
        db.execute(
            "INSERT INTO entries VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (code, w.findtext("form"), int(w.findtext("word_number") or 1), w.findtext("type"),
             texts["language_info"], texts["other_info"], texts["annotations"],
             opt(oi, "other_forms"), opt(oi, "realted_forms"), source, ",".join(merged) or None))
        defs = []
        for i, d in enumerate(item.findall("sense_info/definitions/definition"), 1):
            links = [x.text.strip() for x in d.iter("word_no") if x.text]
            links += [m.strip() for m in _LIT_WORD_NO.findall(d.text or "")]
            text = flat(d)
            defs.append(text)
            db.execute("INSERT INTO senses VALUES(?,?,?,?)", (code, i, text, ",".join(links) or None))
        hist_forms = []
        for i, fc in enumerate(oi.findall("forms_changed"), 1):
            form = (fc.findtext("form") or "").strip()
            hist_forms.append(form)
            db.execute("INSERT INTO forms_changed VALUES(?,?,?,?)",
                       (code, i, form, (fc.findtext("centuries") or "").strip() or None))
        for gi, g in enumerate(item.findall("historical_info/historical_examples/definitions"), 1):
            db.execute("INSERT INTO hist_groups VALUES(?,?,?)", (code, gi, flat(g.find("definition")) if g.find("definition") is not None else ""))
            for ci, c in enumerate(g.findall("centuries"), 1):
                cid += 1
                db.execute("INSERT INTO hist_centuries VALUES(?,?,?,?,?,?)",
                           (cid, code, gi, ci, (c.findtext("century") or "").strip(), (c.findtext("form") or "").strip()))
                for xi, ex in enumerate(c.findall("examples"), 1):
                    eid += 1
                    e_el = ex.find("example")
                    db.execute("INSERT INTO examples VALUES(?,?,?,?,?,?,?,?,?)",
                               (eid, cid, code, xi, flat(e_el), flat(e_el, keep_strong=True),
                                opt(ex, "original"), opt(ex, "translation"), (ex.findtext("source") or "").strip()))
        for ri, r in enumerate(item.findall("references_info/references"), 1):
            db.execute("INSERT INTO refs VALUES(?,?,?,?,?,?)",
                       (code, ri, opt(r, "authors"), opt(r, "year"), opt(r, "title"), opt(r, "publisher")))
        db.execute("INSERT INTO entries_fts(rowid, form, historical_forms, definitions) VALUES(?,?,?,?)",
                   (code, w.findtext("form"), " ".join(hist_forms), "\n".join(defs)))
        n_entries += 1
    meta = {"entries": n_entries, "examples": eid}
    for k, v in meta.items():
        db.execute("INSERT INTO meta VALUES(?,?)", (k, str(v)))
    db.commit()
    return db, raw


def verify(db, raw):
    errs = []
    n_raw = raw.execute("SELECT COUNT(*) FROM (SELECT target_code FROM raw_xml UNION SELECT target_code FROM raw_download)"
                        if raw.execute("SELECT 1 FROM sqlite_master WHERE name='raw_download'").fetchone()
                        else "SELECT COUNT(*) FROM raw_xml").fetchone()[0]
    n = db.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    if n != n_raw:
        errs.append(f"표제어 수 불일치: 원본 {n_raw} / DB {n}")
    if db.execute("SELECT COUNT(*) FROM entries WHERE form='' OR form IS NULL").fetchone()[0]:
        errs.append("빈 표제어가 있습니다")
    if db.execute("SELECT COUNT(*) FROM entries e WHERE NOT EXISTS (SELECT 1 FROM senses s WHERE s.target_code=e.target_code)").fetchone()[0]:
        errs.append("뜻풀이가 없는 표제어가 있습니다")
    if db.execute("SELECT COUNT(*) FROM (SELECT form, word_number FROM entries GROUP BY 1,2 HAVING COUNT(*)>1)").fetchone()[0]:
        errs.append("(표제어, 어깨번호) 중복이 있습니다")
    if db.execute("SELECT COUNT(*) FROM examples WHERE example LIKE '%<strong>%' OR example LIKE '%<br%'").fetchone()[0]:
        errs.append("용례에 태그가 남아 있습니다")
    if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        errs.append("integrity_check 실패")
    return errs


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.parse_args()
    if not RAW_PATH.exists():
        sys.exit(f"원본 DB가 없습니다: {RAW_PATH}")
    tmp = OUT_PATH.with_name(OUT_PATH.name + ".new")
    db, raw = build(RAW_PATH, tmp)
    errs = verify(db, raw)
    if errs:
        db.close()
        tmp.unlink()
        sys.exit("검증 실패, 기존 DB는 그대로 둡니다:\n  " + "\n  ".join(errs))
    src = dict(db.execute("SELECT source, COUNT(*) FROM entries GROUP BY source").fetchall())
    merged = db.execute("SELECT COUNT(*) FROM entries WHERE merged_fields IS NOT NULL").fetchone()[0]
    print(f"  출처: {src}, 잘린 설명을 내려받기 파일로 이어 붙인 표제어 {merged}개")
    for t in ("entries", "senses", "forms_changed", "hist_groups", "hist_centuries", "examples", "refs"):
        print(f"  {t:<15}{db.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]:>8}")
    db.execute("VACUUM")
    db.close()
    if OUT_PATH.exists():
        OUT_PATH.replace(OUT_PATH.with_name("urimal.prev.sqlite"))
    tmp.replace(OUT_PATH)
    print(f"완료: {OUT_PATH}")


if __name__ == "__main__":
    main()
