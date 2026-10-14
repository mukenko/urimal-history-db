#!/usr/bin/env python3
"""우리말역사어휘사전 Open API 원본 XML 수집기 (표준 라이브러리만 사용)

사전 내용 API(/history/api/view, method=target_code)의 응답 XML을 그대로
SQLite(data/raw.sqlite)에 압축 저장한다. 스키마 변환은 이 스크립트의 책임이 아니다.

사용 예
  python3 urimal_fetch.py fetch --range 1 20000      # 코드 범위 스캔(이어받기, 하루 한도에서 멈춤)
  python3 urimal_fetch.py fetch --codes 4548 14232   # 코드 지정
  python3 urimal_fetch.py status                     # 진행 현황

인증키는 환경변수 URIMAL_HISTORY_API_KEY 또는 이 파일 옆의 .env 에서 읽는다.
"""
import argparse
import datetime as dt
import http.client
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zlib
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("URIMAL_HISTORY_DB", ROOT / "data" / "raw.sqlite"))
BASE_URL = "https://kdict.korean.go.kr/history"
KEY_NAME = "URIMAL_HISTORY_API_KEY"
DEFAULT_LIMIT = 45000          # 하루 공식 한도 50,000건보다 여유를 둔다
MAX_CONSECUTIVE_ERRORS = 3     # 연속 오류가 이 횟수에 이르면 중단


class LimitReached(Exception):
    pass


class ApiError(Exception):
    pass


class FatalApiError(ApiError):
    """재시도해도 소용없는 API 오류(미등록 키, 사용 중지 키, 잘못된 요청 등)."""


# ---------------------------------------------------------------- 설정/DB
def load_key():
    key = os.environ.get(KEY_NAME, "").strip()
    if key:
        return key
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(KEY_NAME + "="):
                return line.split("=", 1)[1].strip().strip("'\"")
    sys.exit(f"인증키를 찾을 수 없습니다. .env 에 {KEY_NAME}=... 를 적어 주세요.")


def open_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS raw_xml(
            target_code INTEGER PRIMARY KEY,
            fetched_at  TEXT NOT NULL,
            xml         BLOB NOT NULL);           -- zlib 압축한 원본 XML
        CREATE TABLE IF NOT EXISTS fetch_state(
            target_code INTEGER PRIMARY KEY,
            status      TEXT NOT NULL,            -- ok | empty | error
            fetched_at  TEXT NOT NULL,
            note        TEXT);
        CREATE TABLE IF NOT EXISTS api_usage(
            day   TEXT PRIMARY KEY,
            calls INTEGER NOT NULL);
        """
    )
    return db


def now():
    return dt.datetime.now().isoformat(timespec="seconds")


def today():
    return dt.date.today().isoformat()


# ---------------------------------------------------------------- XML
# 응답에 실제로 쓰이는 태그 이름(2026-10-14 전체 수집 기준). 본문에 든 <Pensées d'Août ...> 같은 것은 태그가 아니다.
_TAGS = ("annotations authors centuries century channel definition definitions description example examples "
         "form forms_changed historical_examples historical_info item language_info lastBuildDate link origin "
         "origin_info original other_forms other_info publisher realted_forms references references_info "
         "sense_info source strong target_code title total translation type word_info word_no word_number year br")
_STRAY_LT = re.compile(r"<(?!/?(?:%s)[\s/>]|[?!])" % "|".join(_TAGS.split()), re.I)
_BARE_AMP = re.compile(r"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)")
_BR = re.compile(r"<br\s*>", re.I)
_CDATA = re.compile(r"(<!\[CDATA\[.*?\]\]>)", re.S)


def repair_xml(text):
    """이스케이프되지 않은 '<'(출전 표기 <1575 천자문 …> 처럼 알려진 태그 이름이 뒤따르지 않는 것)와 '&'를 본문 글자로 보고, 닫히지 않는 <br>은 <br/>로 고친다. CDATA 안은 그대로 둔다."""
    parts = _CDATA.split(text)
    for i in range(0, len(parts), 2):
        parts[i] = _BR.sub("<br/>", _BARE_AMP.sub("&amp;", _STRAY_LT.sub("&lt;", parts[i])))
    return "".join(parts)


def parse_xml(text):
    text = text.strip()
    try:
        return ET.fromstring(text)
    except ET.ParseError:
        pass
    try:
        return ET.fromstring(repair_xml(text))
    except ET.ParseError:
        raise ApiError("응답이 올바른 XML이 아닙니다")


def check_error(root):
    if root.tag == "error" or root.find("error_code") is not None:
        code = (root.findtext("error_code") or "?").strip()
        msg = (root.findtext("message") or "").strip()
        text = f"API 오류 {code}: {msg}"
        if code == "021":
            text += " (일시 사용 중지된 키입니다. 한도 초과나 과다 호출일 수 있으니 시간을 두고 확인하세요)"
        # 000(시스템 오류)만 일시적 오류로 보고, 나머지는 재시도해도 같은 결과이므로 즉시 중단한다.
        raise (ApiError if code == "000" else FatalApiError)(text)


# ---------------------------------------------------------------- API 클라이언트
class Client:
    def __init__(self, key, db, limit, sleep):
        self.key, self.db, self.limit, self.sleep = key, db, limit, sleep

    def used_today(self):
        row = self.db.execute("SELECT calls FROM api_usage WHERE day=?", (today(),)).fetchone()
        return row[0] if row else 0

    def _count_call(self):
        self.db.execute(
            "INSERT INTO api_usage(day, calls) VALUES(?, 1) "
            "ON CONFLICT(day) DO UPDATE SET calls = calls + 1",
            (today(),),
        )
        self.db.commit()

    def call(self, path, params):
        """한 번의 API 호출(재시도 포함). 시도마다 호출 수에 포함한다.
        주소에 키가 들어 있으므로 주소는 어디에도 출력하지 않는다."""
        params = dict(params, key=self.key)
        url = f"{BASE_URL}{path}?{urllib.parse.urlencode(params)}"
        last = "알 수 없는 오류"
        for wait in (0, 2, 5):
            if wait:
                time.sleep(wait)
            if self.used_today() >= self.limit:
                raise LimitReached()
            self._count_call()
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "urimal-history-db/0.1"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    text = resp.read().decode("utf-8")
                if self.sleep:
                    time.sleep(self.sleep)
                return text
            except urllib.error.HTTPError as e:
                last = f"HTTP {e.code}"
            except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as e:
                last = f"네트워크 오류 ({type(e).__name__})"
            except UnicodeDecodeError:
                last = "응답 디코딩 오류"
        raise ApiError(last)

    def fetch_code(self, code):
        text = self.call("/api/view", {"method": "target_code", "q": str(code), "req_type": "xml"})
        root = parse_xml(text)
        check_error(root)
        total = int((root.findtext("total") or "0").strip() or 0)
        item = root.find("item")
        if total == 0 or item is None:
            set_state(self.db, code, "empty", None)
            return "empty"
        got = (item.findtext("target_code") or "").strip()
        if got != str(code):
            raise ApiError(f"요청 코드와 응답 코드가 다릅니다 ({code} != {got})")
        self.db.execute(
            "INSERT OR REPLACE INTO raw_xml(target_code, fetched_at, xml) VALUES(?,?,?)",
            (code, now(), zlib.compress(text.encode("utf-8"), 6)),
        )
        set_state(self.db, code, "ok", None)
        return "ok"


def set_state(db, code, status, note):
    db.execute(
        "INSERT OR REPLACE INTO fetch_state(target_code, status, fetched_at, note) VALUES(?,?,?,?)",
        (code, status, now(), note))
    db.commit()


# ---------------------------------------------------------------- 수집 루프
def run_codes(client, codes, force=False, verbose=False):
    db = client.db
    known = dict(db.execute("SELECT target_code, status FROM fetch_state"))
    counts = Counter()
    consecutive_err = 0
    total = len(codes)
    try:
        for i, code in enumerate(codes, 1):
            if not force and known.get(code) in ("ok", "empty"):
                counts["skipped"] += 1
            else:
                try:
                    result = client.fetch_code(code)
                    counts[result] += 1
                    consecutive_err = 0
                    if verbose:
                        print(f"  {code}: {result}")
                except FatalApiError as e:
                    print(f"  {code}: {e}\n재시도해도 해결되지 않는 오류라서 중단합니다.")
                    break
                except ApiError as e:
                    counts["error"] += 1
                    consecutive_err += 1
                    set_state(db, code, "error", str(e))
                    print(f"  {code}: 오류 - {e}")
                    if consecutive_err >= MAX_CONSECUTIVE_ERRORS:
                        print(f"연속 오류 {MAX_CONSECUTIVE_ERRORS}회, 중단합니다. 잠시 후 다시 실행하세요.")
                        break
            if i % 500 == 0:
                print(f"  진행 {i}/{total}  (오늘 호출 {client.used_today()}/{client.limit})", flush=True)
    except LimitReached:
        print(f"오늘 호출 한도({client.limit})에 도달했습니다. 내일 같은 명령을 다시 실행하면 이어서 진행합니다.")
    except KeyboardInterrupt:
        print("\n중단했습니다. 같은 명령을 다시 실행하면 이어서 진행합니다.")
    finally:
        db.commit()
    print("요약:", ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "처리한 코드 없음",
          f"| 오늘 호출 {client.used_today()}/{client.limit}")


# ---------------------------------------------------------------- 명령
def cmd_fetch(args):
    db = open_db()
    client = Client(load_key(), db, args.limit, args.sleep)
    codes = args.codes if args.codes else list(range(args.range[0], args.range[1] + 1))
    run_codes(client, codes, force=args.force, verbose=args.verbose)


def cmd_status(args):
    db = open_db()
    print("상태별 코드 수:")
    for status, n in db.execute("SELECT status, COUNT(*) FROM fetch_state GROUP BY status"):
        print(f"  {status:<6} {n}")
    row = db.execute("SELECT MIN(target_code), MAX(target_code), COUNT(*) FROM raw_xml").fetchone()
    print(f"원본 XML: {row[2]}건 (코드 {row[0]} ~ {row[1]})")
    row = db.execute("SELECT calls FROM api_usage WHERE day=?", (today(),)).fetchone()
    print(f"오늘 호출: {row[0] if row else 0}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="코드로 수집")
    g = f.add_mutually_exclusive_group(required=True)
    g.add_argument("--codes", nargs="+", type=int)
    g.add_argument("--range", nargs=2, type=int, metavar=("START", "END"))
    f.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="하루 호출 상한 (기본 %(default)s)")
    f.add_argument("--sleep", type=float, default=0.05, help="호출 사이 대기 초 (기본 %(default)s)")
    f.add_argument("--force", action="store_true", help="이미 받은 코드도 다시 받기")
    f.add_argument("-v", "--verbose", action="store_true")
    f.set_defaults(func=cmd_fetch)
    s = sub.add_parser("status", help="진행 현황")
    s.set_defaults(func=cmd_status)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
