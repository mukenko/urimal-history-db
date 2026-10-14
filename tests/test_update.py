"""urimal_update.py 의 순수 함수 테스트(네트워크·DB 없이).

실행(저장소 루트): python3 -m unittest tests.test_update -v
"""
import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import urimal_update as uu  # noqa: E402


class ItemText(unittest.TestCase):
    def test_ignores_build_date(self):
        a = "<channel><lastBuildDate>1</lastBuildDate><item><x>가</x></item></channel>"
        b = "<channel><lastBuildDate>2</lastBuildDate><item><x>가</x></item></channel>"
        c = "<channel><lastBuildDate>2</lastBuildDate><item><x>나</x></item></channel>"
        self.assertEqual(uu.item_text(a), uu.item_text(b))
        self.assertNotEqual(uu.item_text(a), uu.item_text(c))

    def test_form_of(self):
        self.assertEqual(uu.form_of("<word_info><form>고드름</form></word_info>"), "고드름")
        self.assertEqual(uu.form_of("<form><![CDATA[가]]></form>"), "가")


class TrimLog(unittest.TestCase):
    def test_trim_keeps_recent_and_continuations(self):
        now = dt.datetime(2026, 10, 30, 12, 0, 0)
        text = ("[2026-10-01 08:00:00] 오래됨\n  이어지는 줄\n"
                "[2026-10-20 08:00:00] 최근\n  이어지는 줄2\n")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "u.log"
            p.write_text(text, encoding="utf-8")
            uu.trim_log(p, days=15, now=now)
            self.assertEqual(p.read_text(encoding="utf-8"), "[2026-10-20 08:00:00] 최근\n  이어지는 줄2\n")


if __name__ == "__main__":
    unittest.main()
