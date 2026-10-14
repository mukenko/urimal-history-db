#!/usr/bin/env python3
"""우리말역사어휘사전 조회 (표준 라이브러리만 사용, DB는 읽기 전용)

  python3 urimal_lookup.py 고드름                 # 표제어로 조회 (없으면 역사 속 표기 → 앞부분 일치 순으로 찾음)
  python3 urimal_lookup.py 곳어름 --by hist       # 옛 표기(역사 속 표기)로 찾기
  python3 urimal_lookup.py 얼어붙은 --by def      # 뜻풀이 안에서 찾기 (2글자 이하도 가능)
  python3 urimal_lookup.py 고드 --by prefix       # 표제어 앞부분 일치 목록
  python3 urimal_lookup.py --code 4548            # 코드로 조회
  python3 urimal_lookup.py 고드름 --examples 0    # 용례를 빼고 보기 (기본: 세기마다 2개, --examples all 은 전부)

표제어의 하이픈(가-건물)은 검색어에 넣지 않아도 됩니다.
주의: API가 어휘 역사 설명·부가 정보·기타 정보를 256자에서 잘라 보내므로, 긴 설명은 끝이 잘려 있을 수 있습니다.
"""
import argparse
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("URIMAL_DB", ROOT / "data" / "urimal.sqlite"))


def connect():
    if not DB_PATH.exists():
        sys.exit(f"조회용 DB가 없습니다: {DB_PATH}\n  python3 urimal_build.py 로 먼저 만드세요.")
    db = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


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
            shown = f"{total}개 중 {len(rows)}개" if total > len(rows) else f"{total}개"
            print(f"    {c['century']} {c['form']} ({shown})")
            for r in rows:
                print(f"      · {r['example'].replace(chr(10), ' ')}  <{r['source']}>")
                if r["original"]:
                    print(f"        원문: {r['original'].replace(chr(10), ' ')}")
                if r["translation"]:
                    print(f"        번역: {r['translation'].replace(chr(10), ' ')}")


def show_list(db, codes, q=None):
    for code in codes:
        e = db.execute("SELECT form, word_number, type, target_code FROM entries WHERE target_code=?", (code,)).fetchone()
        s = db.execute("SELECT definition FROM senses WHERE target_code=? ORDER BY idx LIMIT 1", (code,)).fetchone()
        d = (s[0] if s else "").replace("\n", " ")
        print(f"  #{code:<6} {e['form']}({e['word_number']}) [{e['type']}]  {d[:60]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", nargs="?", help="검색어")
    ap.add_argument("--by", choices=("form", "hist", "def", "prefix"), default="form",
                    help="찾는 방식 (기본 form: 표제어 → 역사 속 표기 → 앞부분 일치 순)")
    ap.add_argument("--code", type=int, help="target_code 로 조회")
    ap.add_argument("--examples", default="2", help="세기별 용례 수: 0 | 숫자 | all (기본 2)")
    ap.add_argument("--limit", type=int, default=50, help="목록 최대 개수 (기본 50)")
    args = ap.parse_args()
    if args.examples != "all":
        try:
            args.examples = int(args.examples)
        except ValueError:
            ap.error("--examples 는 0, 숫자, all 중 하나")
    db = connect()

    if args.code:
        show_entry(db, args.code, args.examples)
        return
    if not args.query:
        ap.error("검색어나 --code 가 필요합니다")
    q = norm(args.query)

    if args.by == "form":
        codes = find_form(db, q)
        if codes:
            for c in codes:
                show_entry(db, c, args.examples)
            return
        codes = find_hist(db, q)
        if codes:
            print(f"'{q}' 는 표제어가 아니지만 역사 속 표기로 나옵니다:\n")
            for c in codes[:args.limit]:
                show_entry(db, c, args.examples)
            return
        codes = find_prefix(db, q, args.limit)
        if codes:
            print(f"'{q}' 로 시작하는 표제어 {len(codes)}개 (--limit {args.limit}):")
            show_list(db, codes)
            return
        sys.exit(f"'{q}': 찾지 못했습니다. --by def 로 뜻풀이 안을 찾아보세요.")
    elif args.by == "hist":
        codes = find_hist(db, q)
    elif args.by == "prefix":
        codes = find_prefix(db, q, args.limit)
    else:
        codes = find_def(db, q, args.limit)
    if not codes:
        sys.exit(f"'{q}': 찾지 못했습니다.")
    if len(codes) <= 3:
        for c in codes:
            show_entry(db, c, args.examples)
    else:
        print(f"{len(codes)}개 (--limit {args.limit}):")
        show_list(db, codes)


if __name__ == "__main__":
    main()
