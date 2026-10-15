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


# ---------------------------------------------------------------- DB 를 쓰는 단계 (가짜 API + 임시 DB)
import os  # noqa: E402
import zlib  # noqa: E402

import urimal_fetch as uf  # noqa: E402


def entry_xml(code, form, num, extra=""):
    return (f"<channel><lastBuildDate>t</lastBuildDate><total>1</total><item><target_code>{code}</target_code>"
            f"<word_info><form>{form}</form><word_number>{num}</word_number><type>단어</type></word_info>{extra}</item></channel>")


EMPTY = "<channel><lastBuildDate>t</lastBuildDate><total>0</total></channel>"


class FakeClient(uf.Client):
    """view API 만 흉내 낸다. 서버 상태(self.server: 코드 -> XML)와 요청 횟수를 기록한다."""

    def __init__(self, db, server):
        super().__init__("k", db, 100000, 0)
        self.server, self.requested = dict(server), []

    def call(self, path, params):
        code = int(params["q"])
        self.requested.append(code)
        return self.server.get(code, EMPTY)


class DbCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for obj, name, val in ((uf, "DB_PATH", Path(self.tmp.name) / "raw.sqlite"), (uu, "LOG_PATH", Path(self.tmp.name) / "u.log")):
            old = getattr(obj, name)
            setattr(obj, name, val)
            self.addCleanup(setattr, obj, name, old)
        self.db = uu.open_db()
        self.addCleanup(self.db.close)

    def seed(self, code, form, num, status="ok", note=None):
        self.db.execute("INSERT OR REPLACE INTO raw_xml VALUES(?,?,?)", (code, "2026-10-01T00:00:00",
                        zlib.compress(entry_xml(code, form, num).encode())))
        self.db.execute("INSERT OR REPLACE INTO fetch_state VALUES(?,?,?,?)", (code, status, "2026-10-01T00:00:00", note))
        self.db.commit()

    def numbers(self, form):
        out = {}
        for code, blob in self.db.execute("SELECT target_code, xml FROM raw_xml"):
            xml = zlib.decompress(blob).decode()
            if uu.form_of(xml) == form:
                out[code] = uu.number_of(xml)
        return out

    def state(self, code):
        return self.db.execute("SELECT status, note FROM fetch_state WHERE target_code=?", (code,)).fetchone()

    def has_raw(self, code):
        return self.db.execute("SELECT 1 FROM raw_xml WHERE target_code=?", (code,)).fetchone() is not None


class GroupRefresh(DbCase):
    """동형이의어가 끼어들어 번호가 밀리면 같은 표제어의 다른 항목을 바로 다시 받는다(1-a)."""

    def setUp(self):
        super().setUp()
        self.seed(10, "예", 1)
        self.seed(11, "예", 2)
        self.seed(12, "예", 3)
        self.seed(20, "눈", 1)
        # 사전이 예(2)를 새로 끼워 넣음: 신규 99 가 2번, 옛 2번(11)은 3번, 옛 3번(12)은 4번
        self.server = {10: entry_xml(10, "예", 1), 99: entry_xml(99, "예", 2),
                       11: entry_xml(11, "예", 3), 12: entry_xml(12, "예", 4), 20: entry_xml(20, "눈", 1)}

    def run_step(self, codes, previously_ok=False, client=None):
        client = client or FakeClient(self.db, self.server)
        counts = uu.Counter()
        ok = uu.run_refresh(client, codes, "t", counts, previously_ok=previously_ok)
        return client, counts, ok

    def test_added_homonym_refreshes_the_whole_group(self):
        client = FakeClient(self.db, self.server)
        counts = uu.Counter()
        uu.refresh_group_steps(client, [99], counts)
        self.assertEqual(self.numbers("예"), {10: 1, 99: 2, 11: 3, 12: 4})
        self.assertEqual(sorted(set(client.requested)), [10, 11, 12, 99])
        self.assertNotIn(20, client.requested)                   # 다른 표제어는 건드리지 않는다

    def test_content_only_change_does_not_trigger(self):
        self.server[10] = entry_xml(10, "예", 1, "<sense_info>달라짐</sense_info>")
        client = FakeClient(self.db, self.server)
        counts = uu.Counter()
        uu.refresh_group_steps(client, [10], counts)
        self.assertEqual(client.requested, [10])                 # 자기 자신만

    def test_number_change_triggers_group(self):
        client = FakeClient(self.db, self.server)
        counts = uu.Counter()
        uu.refresh_group_steps(client, [11], counts)
        self.assertEqual(self.numbers("예"), {10: 1, 11: 3, 12: 4})   # 11 의 번호가 바뀐 것을 보고 12 도 다시 받음
        self.assertIn(12, client.requested)

    def test_group_is_not_fetched_twice(self):
        client = FakeClient(self.db, self.server)
        counts = uu.Counter()
        uu.refresh_group_steps(client, [99, 11], counts)         # 같은 그룹의 두 변경
        self.assertEqual(sorted(client.requested), [10, 11, 12, 99])

    def test_changes_are_recorded_and_counted(self):
        client = FakeClient(self.db, self.server)
        counts = uu.Counter()
        uu.refresh_group_steps(client, [99], counts)
        self.assertEqual(counts["added"], 1)
        self.assertEqual(counts["changed"], 2)                   # 11, 12 의 번호가 바뀜
        kinds = dict(self.db.execute("SELECT target_code, kind FROM changes"))
        self.assertEqual(kinds, {99: "added", 11: "changed", 12: "changed"})


class PendingRemoval(DbCase):
    """이전에 있던 코드가 비어 와도 같은 실행에서 바로 지우지 않고, 다른 날 또 비면 확정한다(2)."""

    def setUp(self):
        super().setUp()
        self.seed(30, "삭제될말", 1)
        self.client = FakeClient(self.db, {})                    # 서버에 없음 = 빈 응답

    def run_codes(self, codes=(30,)):
        counts = uu.Counter()
        uu.run_refresh(self.client, list(codes), "t", counts, previously_ok=True)
        return counts

    def test_first_empty_only_marks(self):
        counts = self.run_codes()
        self.assertTrue(self.has_raw(30))
        self.assertEqual(self.state(30), ("ok", f"empty@{uf.today()}"))
        self.assertEqual(counts["removed"], 0)
        self.assertEqual(self.client.requested, [30])            # 같은 실행에서 두 번째 확인을 하지 않는다

    def test_same_day_second_empty_does_not_remove(self):
        self.run_codes()
        self.run_codes()
        self.assertTrue(self.has_raw(30))

    def test_empty_on_a_later_day_confirms_removal(self):
        self.db.execute("UPDATE fetch_state SET note='empty@2026-10-01' WHERE target_code=30")
        counts = self.run_codes()
        self.assertFalse(self.has_raw(30))
        self.assertEqual(counts["removed"], 1)
        self.assertEqual(self.state(30)[0], "gone")
        self.assertIsNotNone(self.db.execute("SELECT 1 FROM raw_removed WHERE target_code=30").fetchone())
        self.assertEqual(self.db.execute("SELECT kind FROM changes WHERE target_code=30").fetchone()[0], "removed")

    def test_content_returning_clears_the_mark(self):
        self.db.execute("UPDATE fetch_state SET note='empty@2026-10-01' WHERE target_code=30")
        self.client.server[30] = entry_xml(30, "삭제될말", 1)
        self.run_codes()
        self.assertTrue(self.has_raw(30))
        self.assertEqual(self.state(30), ("ok", None))

    def test_pending_codes_selects_only_earlier_days(self):
        self.seed(31, "a", 1, note=f"empty@{uf.today()}")
        self.seed(32, "b", 1, note="empty@2026-10-01")
        self.seed(33, "c", 1)
        self.assertEqual(uu.codes_pending(self.db), [32])

    def test_pending_is_a_step_before_oldest(self):
        self.assertIn("pending", uu.ALL_STEPS)
        self.assertLess(uu.ALL_STEPS.index("pending"), uu.ALL_STEPS.index("oldest"))


class BuildDuplicateRetry(DbCase):
    """빌드가 (표제어, 어깨번호) 중복으로 실패하면 그 코드들만 다시 받고 빌드를 한 번 더 한다(1-b)."""

    def test_duplicate_codes_are_parsed_from_the_build_message(self):
        msg = "검증 실패, 기존 DB는 그대로 둡니다:\n  (표제어, 어깨번호) 중복이 있습니다 — 중복 코드: 11,12,99"
        self.assertEqual(uu.duplicate_codes(msg), [11, 12, 99])
        self.assertEqual(uu.duplicate_codes("용례에 태그가 남아 있습니다"), [])

    def test_build_retries_once_after_refreshing_the_duplicates(self):
        self.seed(11, "예", 2)
        self.seed(99, "예", 2)
        client = FakeClient(self.db, {11: entry_xml(11, "예", 3), 99: entry_xml(99, "예", 2)})
        calls = []

        def fake_build():
            calls.append(1)
            if len(calls) == 1:
                return 1, "검증 실패\n  (표제어, 어깨번호) 중복이 있습니다 — 중복 코드: 11,99"
            return 0, "완료"
        ok = uu.build_with_retry(client, fake_build)
        self.assertTrue(ok)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.numbers("예"), {11: 3, 99: 2})

    def test_retry_happens_only_once(self):
        self.seed(11, "예", 2)
        client = FakeClient(self.db, {11: entry_xml(11, "예", 2)})
        calls = []

        def always_fail():
            calls.append(1)
            return 1, "중복이 있습니다 — 중복 코드: 11"
        self.assertFalse(uu.build_with_retry(client, always_fail))
        self.assertEqual(len(calls), 2)

    def test_other_failures_are_not_retried(self):
        client = FakeClient(self.db, {})
        calls = []

        def fail():
            calls.append(1)
            return 1, "integrity_check 실패"
        self.assertFalse(uu.build_with_retry(client, fail))
        self.assertEqual(len(calls), 1)
        self.assertEqual(client.requested, [])


if __name__ == "__main__":
    unittest.main()
