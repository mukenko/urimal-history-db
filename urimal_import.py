#!/usr/bin/env python3
"""누리집 '전체 사전 내려받기' XML(zip) → data/raw.sqlite 의 raw_download 표 (표준 라이브러리만 사용)

누리집(검색 결과 화면의 '내려받기' → '전체 사전 내려받기')에서 받은 zip 안의 XML을 표제어(item)별로 나눠 저장한다.
이 파일은 Open API와 달리 어휘 역사 설명·부가 정보·기타 정보가 잘리지 않지만, 큰따옴표(“ ”)가 빠지고
줄바꿈이 없어지며 용례의 강조 표시(<strong>)가 없다. 그래서 빌드(urimal_build.py)는 API 원본을 기본으로 하고,
API가 256자에서 자른 부분과 API가 내주지 않는 표제어만 이 파일로 채운다.

사용 예
  python3 urimal_import.py data/download/full_20261001.zip
  python3 urimal_import.py data/download/xml/1605514_15034.xml     # 이미 푼 XML도 가능

zip 은 내용을 풀어 디스크에 쓰지 않고 메모리에서 바로 읽는다.
"""
import argparse
import os
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET
import zipfile
import zlib
import datetime as dt
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW_PATH = Path(os.environ.get("URIMAL_HISTORY_DB", ROOT / "data" / "raw.sqlite"))


def load_root(path):
    """zip(안의 XML 1개를 메모리에서 바로 읽음) 또는 XML 파일 → <channel> 요소."""
    path = Path(path)
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            names = [i for i in z.infolist() if i.filename.lower().endswith(".xml")]
            if len(names) != 1:
                raise ValueError(f"zip 안에 XML 이 1개여야 합니다 (현재 {len(names)}개)")
            if "/" in names[0].filename or ".." in names[0].filename:
                raise ValueError("zip 안의 파일 이름이 안전하지 않습니다")
            with z.open(names[0]) as f:
                return ET.parse(f).getroot()
    return ET.parse(path).getroot()


def import_root(db, root, source, created):
    """내려받기 XML(<channel>)을 raw_download 표에 통째로 바꿔 넣는다. 항목 수가 안내(total)와 다르면 ValueError."""
    items = root.findall("item")
    total = int((root.findtext("total") or "0").strip() or 0)
    if not items or len(items) != total:
        raise ValueError(f"항목 수가 안내({total})와 다릅니다: {len(items)}")
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS raw_download(
            target_code INTEGER PRIMARY KEY,
            source      TEXT NOT NULL,      -- 파일 이름
            created     TEXT,               -- 파일 생성일(YYYYMMDD)
            imported_at TEXT NOT NULL,
            xml         BLOB NOT NULL);     -- zlib 압축한 <item> XML
        """)
    now = dt.datetime.now().isoformat(timespec="seconds")
    rows = [(int((it.findtext("target_code") or "").strip()), source, created, now,
             zlib.compress(ET.tostring(it, encoding="unicode").encode("utf-8"), 6)) for it in items]
    with db:                                    # 한 번에 바꾼다: 중간에 실패하면 이전 내용이 그대로 남는다
        db.execute("DELETE FROM raw_download")
        db.executemany("INSERT INTO raw_download VALUES(?,?,?,?,?)", rows)
    return len(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", type=Path, help="내려받은 zip 또는 XML 파일")
    args = ap.parse_args()
    root = load_root(args.path)
    m = re.search(r"(\d{8})", args.path.name)
    created = m.group(1) if m else (root.findtext("lastBuildDate") or "").split()[0]
    db = sqlite3.connect(RAW_PATH)
    try:
        n = import_root(db, root, args.path.name, created)
    except ValueError as e:
        sys.exit(str(e))
    print(f"{n}개 표제어를 raw_download 에 저장했습니다 (파일 생성일 {created}).")


if __name__ == "__main__":
    main()
