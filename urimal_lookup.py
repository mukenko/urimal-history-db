#!/usr/bin/env python3
"""우리말역사어휘사전 조회 (표준 라이브러리만 사용, DB는 읽기 전용)

  python3 urimal_lookup.py 고드름                 # 표제어로 조회 (없으면 역사 속 표기 → 앞부분 일치 순으로 찾음)
  python3 urimal_lookup.py 곳어름 --by hist       # 옛 표기(역사 속 표기)로 찾기
  python3 urimal_lookup.py 얼어붙은 --by def      # 뜻풀이 안에서 찾기 (2글자 이하도 가능)
  python3 urimal_lookup.py 고드 --by prefix       # 표제어 앞부분 일치 목록
  python3 urimal_lookup.py --code 4548            # 코드로 조회
  python3 urimal_lookup.py 고드름 --examples 0    # 용례를 빼고 보기 (기본: 세기마다 2개, --examples all 은 전부)
  python3 urimal_lookup.py 고래 --examples all --html   # 글꼴을 지정한 HTML 로 만들어 브라우저로 열기(옛한글·한자 자형이 누리집처럼 나옴)

표제어의 하이픈(가-건물)은 검색어에 넣지 않아도 됩니다.
주의: API가 어휘 역사 설명·부가 정보·기타 정보를 256자에서 잘라 보내므로, 긴 설명은 끝이 잘려 있을 수 있습니다.
"""
import argparse
import contextlib
import io
import os
import sqlite3
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("URIMAL_DB", ROOT / "data" / "urimal.sqlite"))


def connect():
    if not DB_PATH.exists():
        sys.exit(f"조회용 DB가 없습니다: {DB_PATH}\n  python3 urimal_build.py 로 먼저 만드세요.")
    db = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


ORIGIN_SEARCH = "https://kdict.korean.go.kr/history/main/searchResult?searchType=simple&tabType=title&searchWord="
ORIGIN_DETAIL = "https://kdict.korean.go.kr/history/main/searchResultDetail?searchWord={q}&etymNo={code}"


def original_url(form, code=None):
    """국립국어원 누리집에서 이 표제어를 보는 화면의 주소. code(target_code)가 있으면 그 항목 하나로 바로 가고(etymNo = target_code,
    열어서 확인), 없으면 표제어 검색 화면이다. 하이픈은 뺀다."""
    q = urllib.parse.quote(norm(form))
    return ORIGIN_DETAIL.format(q=q, code=int(code)) if code is not None else ORIGIN_SEARCH + q


def norm(q):
    return q.replace("-", "").strip()


# ---------------------------------------------------------------- 찾기
def find_form(db, q):
    return [r[0] for r in db.execute(
        "SELECT target_code FROM entries WHERE REPLACE(form,'-','')=? ORDER BY word_number", (q,))]


def find_hist(db, q):
    codes = [r[0] for r in db.execute(
        "SELECT DISTINCT target_code FROM forms_changed WHERE REPLACE(form,'-','')=? "
        "UNION SELECT DISTINCT target_code FROM hist_centuries WHERE REPLACE(form,'-','')=? "
        "ORDER BY 1", (q, q))]
    return codes


def find_prefix(db, q, limit):
    return [r[0] for r in db.execute(
        "SELECT target_code FROM entries WHERE REPLACE(form,'-','') LIKE ? ESCAPE '\\' "
        "ORDER BY REPLACE(form,'-',''), word_number LIMIT ?", (like_escape(q) + "%", limit))]


def find_def(db, q, limit):
    if len(q) >= 3:      # trigram 전문 검색
        sql = ("SELECT rowid FROM entries_fts WHERE definitions MATCH ? ORDER BY rank LIMIT ?")
        try:
            return [r[0] for r in db.execute(sql, ('"' + q.replace('"', '""') + '"', limit))]
        except sqlite3.OperationalError:
            pass
    return [r[0] for r in db.execute(
        "SELECT DISTINCT target_code FROM senses WHERE definition LIKE ? ESCAPE '\\' LIMIT ?",
        ("%" + like_escape(q) + "%", limit))]


def like_escape(s):
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ---------------------------------------------------------------- 출력
def head(e):
    num = f"({e['word_number']})" if e["word_number"] != 1 or homonym_count(e) > 1 else ""
    return f"{e['form']}{num} [{e['type']}]  #{e['target_code']}"


_homonyms = {}


def homonym_count(e):
    return _homonyms.get(e["form"], 1)


def show_entry(db, code, n_examples):
    e = db.execute("SELECT * FROM entries WHERE target_code=?", (code,)).fetchone()
    if not e:
        print(f"코드 {code}: 없음")
        return
    _homonyms[e["form"]] = db.execute("SELECT COUNT(*) FROM entries WHERE form=?", (e["form"],)).fetchone()[0]
    print(f"■ {head(e)}")
    for s in db.execute("SELECT idx, definition FROM senses WHERE target_code=? ORDER BY idx", (code,)):
        lines = s["definition"].split("\n")
        print(f"  {s['idx']}. {lines[0]}")
        for ln in lines[1:]:
            if ln:
                print(f"     {ln}")
    fc = db.execute("SELECT form, centuries FROM forms_changed WHERE target_code=? ORDER BY idx", (code,)).fetchall()
    if fc:
        print("  [어휘 역사] " + "  →  ".join(f"{r['form']}({r['centuries']})" if r["centuries"] else r["form"] for r in fc))
    for label, col in (("어휘 역사 설명", "language_info"), ("부가 정보", "other_info"), ("기타 정보", "annotations")):
        if e[col]:
            print(f"  [{label}] " + e[col].replace("\n", "\n    "))
    if e["related_forms"]:
        print(f"  [관련어] {e['related_forms']}")
    if n_examples != 0:
        show_examples(db, code, n_examples)
    for r in db.execute("SELECT authors, year, title, publisher FROM refs WHERE target_code=? ORDER BY idx", (code,)):
        print(f"  [참고] {r['authors'] or ''} ({r['year'] or ''}) {r['title'] or ''}, {r['publisher'] or ''}")
    print()


def show_examples(db, code, n):
    groups = db.execute("SELECT idx, definition FROM hist_groups WHERE target_code=? ORDER BY idx", (code,)).fetchall()
    total_all = shown_all = 0
    for g in groups:
        if g["definition"]:
            print(f"  [용례: {g['definition']}]")
        else:
            print("  [용례]")
        for c in db.execute("SELECT id, century, form FROM hist_centuries WHERE target_code=? AND group_idx=? ORDER BY idx",
                            (code, g["idx"])):
            sql = "SELECT example, original, translation, source FROM examples WHERE century_id=? ORDER BY idx"
            args = [c["id"]]
            total = db.execute("SELECT COUNT(*) FROM examples WHERE century_id=?", args).fetchone()[0]
            if n != "all":
                sql += " LIMIT ?"
                args.append(n)
            rows = db.execute(sql, args).fetchall()
            total_all += total
            shown_all += len(rows)
            shown = f"{total}개 중 {len(rows)}개 표시, {total - len(rows)}개 더 있음" if total > len(rows) else f"{total}개"
            print(f"    {c['century']} {c['form']} ({shown})")
            for r in rows:
                print(f"      · {r['example'].replace(chr(10), ' ')}  <{r['source']}>")
                if r["original"]:
                    print(f"        원문: {r['original'].replace(chr(10), ' ')}")
                if r["translation"]:
                    print(f"        번역: {r['translation'].replace(chr(10), ' ')}")
    if total_all > shown_all:                                  # 사실 한 줄: 모델이 사용자에게 '더 있음'을 알릴 수 있게
        print(f"    용례 합계: {total_all}개 중 {shown_all}개 표시, {total_all - shown_all}개 더 있음")


def show_list(db, codes, q=None):
    for code in codes:
        e = db.execute("SELECT form, word_number, type, target_code FROM entries WHERE target_code=?", (code,)).fetchone()
        s = db.execute("SELECT definition FROM senses WHERE target_code=? ORDER BY idx LIMIT 1", (code,)).fetchone()
        d = (s[0] if s else "").replace("\n", " ")
        print(f"  #{code:<6} {e['form']}({e['word_number']}) [{e['type']}]  {d[:60]}")


class Miss(Exception):
    """찾는 것이 없을 때(메시지는 사용자에게 그대로 보여 준다)."""


def resolve(db, by, q, limit, max_entries=None):
    """무엇을 보여 줄지 정한다(출력하지 않는다). 찾지 못하면 Miss.
    돌려주는 값: {"kind": "entries"|"list", "codes": [...], "note": 머리말 또는 None, "more": (전체 수, 표시 수) 또는 None}
    텍스트 출력(query)과 HTML 쪽(urimal_page)이 같은 결과를 보여 주도록 이 함수를 함께 쓴다."""
    q = norm(q)
    if not q:
        raise Miss("검색어가 비어 있습니다.")
    if by == "form":
        codes = find_form(db, q)
        if codes:
            more = (len(codes), max_entries) if max_entries and len(codes) > max_entries else None
            return {"kind": "entries", "codes": codes[:max_entries] if max_entries else codes, "note": None, "more": more}
        codes = find_hist(db, q)
        if codes:
            return {"kind": "entries", "codes": codes[:limit], "more": None,
                    "note": f"'{q}' 는 표제어가 아니지만 역사 속 표기로 나옵니다:"}
        codes = find_prefix(db, q, limit)
        if codes:
            return {"kind": "list", "codes": codes, "more": None,
                    "note": f"'{q}' 로 시작하는 표제어 {len(codes)}개 (최대 {limit}):"}
        raise Miss(f"'{q}': 찾지 못했습니다. 뜻풀이 안을 찾으려면 --by def 를 쓰세요.")
    elif by == "hist":
        codes = find_hist(db, q)
    elif by == "prefix":
        codes = find_prefix(db, q, limit)
    else:
        codes = find_def(db, q, limit)
    if not codes:
        raise Miss(f"'{q}': 찾지 못했습니다.")
    if len(codes) <= 3 and by != "prefix":
        return {"kind": "entries", "codes": codes, "note": None, "more": None}
    return {"kind": "list", "codes": codes, "more": None, "note": f"{len(codes)}개 (최대 {limit}):"}


def query(db, by, q, examples, limit, max_entries=None):
    """조회 결과를 stdout 으로 출력한다. 찾지 못하면 Miss. (CLI 와 MCP 서버가 함께 쓴다.)"""
    r = resolve(db, by, q, limit, max_entries)
    if r["kind"] == "list":
        print(r["note"])
        show_list(db, r["codes"])
        return
    if r["note"]:
        print(r["note"] + "\n")
    for c in r["codes"]:
        show_entry(db, c, examples)
    if r["more"]:
        print(f"(동형 표제어 {r['more'][0]}개 중 {r['more'][1]}개만 표시)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", nargs="?", help="검색어")
    ap.add_argument("--by", choices=("form", "hist", "def", "prefix"), default="form",
                    help="찾는 방식 (기본 form: 표제어 → 역사 속 표기 → 앞부분 일치 순)")
    ap.add_argument("--code", type=int, help="target_code 로 조회")
    ap.add_argument("--examples", default="2", help="세기별 용례 수: 0 | 숫자 | all (기본 2)")
    ap.add_argument("--limit", type=int, default=50, help="목록 최대 개수 (기본 50)")
    ap.add_argument("--html", action="store_true",
                    help="결과를 글꼴이 지정된 HTML 로 만들어 연다(옛한글·한자 자형 표시용, ~/.cache/urimal-db/html, 하루 지난 파일은 자동 삭제)")
    ap.add_argument("--no-open", action="store_true", help="--html 로 파일만 만들고 브라우저로 열지 않는다")
    args = ap.parse_args()
    if args.no_open and not args.html:
        ap.error("--no-open 은 --html 과 함께 쓰세요")
    if args.examples != "all":
        try:
            args.examples = int(args.examples)
        except ValueError:
            ap.error("--examples 는 0, 숫자, all 중 하나")
    db = connect()
    if not args.code and not args.query:
        ap.error("검색어나 --code 가 필요합니다")
    if args.html:                                   # 구조를 살린 문서형 HTML(urimal_page): 용례는 접기로 모두 들어간다
        import urimal_html as uh
        import urimal_page as up
        try:
            if args.code:
                resolved = {"kind": "entries", "codes": [args.code], "note": None, "more": None}
                page = up.render_page(db, resolved, str(args.code))
            else:
                page = up.render_query(db, args.by, args.query, args.limit)
        except Miss as e:
            sys.exit(str(e))
        path, opened = uh.save_rendered(page, str(args.code or args.query), open_it=not args.no_open)
        print(f"HTML 저장: {path}")
        if not args.no_open and not opened:
            print("브라우저를 열지 못했습니다. 위 파일을 직접 여세요.")
        return
    try:
        if args.code:
            show_entry(db, args.code, args.examples)
        else:
            query(db, args.by, args.query, args.examples, args.limit)
    except Miss as e:
        sys.exit(str(e))


def write_html(text, label, open_it):
    """조회 결과 text 를 HTML 로 저장하고(필요하면 브라우저로 연다) 위치를 알려 준다."""
    import urimal_html as uh
    path, opened = uh.save_html(text, label, open_it)
    print(f"HTML 저장: {path}")
    if open_it and not opened:
        print("브라우저를 열지 못했습니다. 위 파일을 직접 여세요.")


if __name__ == "__main__":
    main()
