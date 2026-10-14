"""urimal_lookup.py 테스트. 실제 data/urimal.sqlite 를 읽기 전용으로 쓴다(DB는 저장소에 없다).

실행(저장소 루트): python3 -m unittest tests.test_lookup -v
"""
import contextlib
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import urimal_lookup as ul  # noqa: E402

needs = unittest.skipUnless(ul.DB_PATH.exists(), "조회용 DB가 없습니다 (python3 urimal_build.py)")


def run(by, q, examples=0, limit=20, max_entries=None):
    buf = io.StringIO()
    db = ul.connect()
    with contextlib.redirect_stdout(buf):
        ul.query(db, by, q, examples, limit, max_entries)
    return buf.getvalue()


@needs
class Lookup(unittest.TestCase):
    def test_form_and_hyphen(self):
        self.assertIn("#14238", run("form", "가건물"))          # 표제어는 '가-건물'

    def test_homonyms_all_shown(self):
        out = run("form", "서리")
        self.assertIn("서리(1)", out)
        self.assertIn("서리(2)", out)

    def test_max_entries(self):
        out = run("form", "서리", max_entries=1)
        self.assertEqual(out.count("■"), 1)
        self.assertIn("중 1개만 표시", out)

    def test_old_form(self):
        self.assertIn("역사 속 표기로 나옵니다", run("form", "곳어름"))
        self.assertIn("#4548", run("hist", "곳어름"))

    def test_prefix_is_list(self):
        out = run("prefix", "고드름")
        self.assertIn("#4548", out)
        self.assertNotIn("■", out)

    def test_definition_search(self):
        self.assertIn("고드름", run("def", "길게 얼어붙은 얼음"))

    def test_miss(self):
        with self.assertRaises(ul.Miss):
            run("form", "없는말쓰")

    def test_examples_cap(self):
        out = run("form", "고드름", examples=1)
        self.assertIn("3개 중 1개", out)

    def test_full_text_merged(self):
        out = run("form", "고드름")
        self.assertIn("굳어져 현재까지 이어진다", out)          # API가 잘라 보낸 뒷부분(내려받기 파일로 보충)
        self.assertEqual(len(ul.connect().execute("SELECT 1 FROM entries WHERE source='download'").fetchall()), 8)


if __name__ == "__main__":
    unittest.main()
