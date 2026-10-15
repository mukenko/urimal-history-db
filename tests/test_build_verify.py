"""urimal_build.verify: (표제어, 어깨번호) 중복 오류가 정기 업데이트가 읽을 수 있는 코드 목록을 담는지.

실행(저장소 루트): python3 -m unittest tests.test_build_verify -v
"""
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import urimal_build as ub  # noqa: E402
import urimal_update as uu  # noqa: E402


def make(entries):
    db = sqlite3.connect(":memory:")
    db.executescript(ub.SCHEMA)
    raw = sqlite3.connect(":memory:")
    raw.execute("CREATE TABLE raw_xml(target_code INTEGER PRIMARY KEY, fetched_at TEXT, xml BLOB)")
    for code, form, num in entries:
        db.execute("INSERT INTO entries(target_code, form, word_number, type, source) VALUES(?,?,?,?,?)",
                   (code, form, num, "단어", "api"))
        db.execute("INSERT INTO senses(target_code, idx, definition) VALUES(?,?,?)", (code, 0, "뜻"))
        raw.execute("INSERT INTO raw_xml VALUES(?,?,?)", (code, "t", b""))
    return db, raw


class DuplicateMessage(unittest.TestCase):
    def test_no_duplicates_no_error(self):
        db, raw = make([(10, "예", 1), (11, "예", 2), (20, "눈", 1)])
        self.assertEqual(ub.verify(db, raw), [])

    def test_duplicate_error_lists_the_codes(self):
        db, raw = make([(10, "예", 1), (11, "예", 2), (99, "예", 2), (20, "눈", 1)])
        errs = ub.verify(db, raw)
        self.assertEqual(len(errs), 1)
        self.assertIn("중복 코드: 11,99", errs[0])

    def test_update_can_read_what_build_prints(self):
        db, raw = make([(11, "예", 2), (99, "예", 2), (12, "눈", 1), (13, "눈", 1)])
        message = "검증 실패, 기존 DB는 그대로 둡니다:\n  " + "\n  ".join(ub.verify(db, raw))
        self.assertEqual(uu.duplicate_codes(message), [11, 12, 13, 99])


if __name__ == "__main__":
    unittest.main()
