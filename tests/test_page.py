"""urimal_page: 조회 결과를 문서형 HTML 로 그리는 모듈(2026-10-16). 실제 data/urimal.sqlite 를 읽기 전용으로 쓴다(DB는 저장소에 없다).

실행(저장소 루트): python3 -m unittest tests.test_page -v
"""
import re
import sys
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import urimal_lookup as ul  # noqa: E402
import urimal_page as up  # noqa: E402
import urimal_view as uv  # noqa: E402

needs_db = unittest.skipUnless(ul.DB_PATH.exists(), "조회용 DB가 없습니다 (python3 urimal_build.py)")


class Pieces(unittest.TestCase):
    def test_compress_centuries(self):
        self.assertEqual(up.compress_centuries("15세기, 16세기, 17세기, 미상"), "15~17세기 · 미상")
        self.assertEqual(up.compress_centuries("15세기, 17세기"), "15세기 · 17세기")
        self.assertEqual(up.compress_centuries("20세기"), "20세기")
        self.assertEqual(up.compress_centuries("19세기, 18세기"), "18~19세기")
        self.assertEqual(up.compress_centuries(None), "")

    def test_paragraphs_split_on_marker_and_lines(self):
        out = up.paragraphs("첫째 문단.##둘째 문단.\n[예] 보기 글\n[현대어 역] 번역")
        self.assertEqual(out.count("<p"), 4)
        self.assertEqual(out.count('class="ex"'), 2)                 # [예]·[현대어 역] 줄은 구분해 그린다
        self.assertEqual(up.paragraphs(None), "")

    def test_example_keeps_only_strong_and_escapes_the_rest(self):
        out = up.example_html("a <strong>고드름</strong> <script>x</script> & b\nc")
        self.assertIn("<strong>고드름</strong>", out)
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;script&gt;", out)
        self.assertIn("&amp;", out)
        self.assertIn("<br>", out)

    def test_text_is_escaped(self):
        self.assertEqual(up.esc("a<b>&"), "a&lt;b&gt;&amp;")


@needs_db
class Rendered(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = ul.connect()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def page(self, by, q, **kw):
        return up.render_query(self.db, by, q, **kw)

    def test_entry_structure(self):
        html = self.page("form", "고드름")
        self.assertIn('lang="ko"', html)
        self.assertIn("<h2>고드름", html)
        self.assertIn('class="timeline"', html)                      # 어휘 역사: 표기 칸
        self.assertEqual(html.count('<span class="f">'), 3)           # 곳어름 → 고도름 → 고드름
        self.assertIn("17~19세기 · 미상", html)
        self.assertIn("<strong>곳어름", html)                          # 용례의 표제어 강조
        self.assertIn("굳어져 현재까지 이어진다", html)                 # 보충된 어휘 역사 설명 전문
        self.assertIn("CC BY-SA 2.0 KR", html)

    def test_examples_collapsed_when_many_and_open_when_few(self):
        many = self.page("form", "고드름")                              # 용례 15개
        self.assertNotRegex(many, r"<details open")
        self.assertIn("모두 펼치기", many)
        code = self.db.execute("SELECT target_code FROM entries e WHERE NOT EXISTS "
                               "(SELECT 1 FROM examples x WHERE x.target_code=e.target_code) LIMIT 1").fetchone()
        if code:                                                       # 용례가 없는 항목: 용례 구역 자체가 없다
            self.assertNotIn('class="examples"', up.entry_html(up.load_entry(self.db, code[0])))

    def test_small_example_set_is_open(self):
        row = self.db.execute(
            "SELECT target_code FROM examples GROUP BY target_code HAVING COUNT(*) BETWEEN 1 AND ? LIMIT 1", (up.OPEN_ALL_UNDER,)).fetchone()
        d = up.load_entry(self.db, row[0])
        html = up.examples_html(d)
        self.assertRegex(html, r"<details open")
        self.assertIn("모두 접기", html)

    def test_example_count_matches_database(self):
        d = up.load_entry(self.db, 4548)
        n = self.db.execute("SELECT COUNT(*) FROM examples WHERE target_code=4548").fetchone()[0]
        self.assertEqual(d["n_examples"], n)
        html = up.entry_html(d)
        self.assertEqual(len(re.findall(r"<li><span class=\"txt\">", html)), n)

    def test_homonyms_show_numbers(self):
        html = self.page("form", "서리")
        self.assertIn("<sup>1</sup>", html)
        self.assertIn("<sup>2</sup>", html)

    def test_old_form_note_and_list_page(self):
        self.assertIn("역사 속 표기로 나옵니다", self.page("form", "곳어름"))
        lst = self.page("prefix", "고드")
        self.assertIn('class="hits"', lst)
        self.assertIn("고드름", lst)
        self.assertNotIn("<article", lst)

    def test_homonym_toc(self):
        many = self.page("form", "서리")
        self.assertIn('<nav class="toc"', many)
        self.assertEqual(len(re.findall(r'<a href="#e\d+"><b>서리</b>', many)), 2)       # 동형이의어마다 바로가기
        for code in re.findall(r'<a href="#e(\d+)">', many):
            self.assertIn(f'<article id="e{code}">', many)                              # 목차의 닻이 실제로 있다
        self.assertNotIn('<nav class="toc"', self.page("form", "고드름"))                  # 항목이 하나면 목차가 없다

    def test_links_only_with_link_base_and_only_to_existing_pages(self):
        static = self.page("form", "씨름")
        self.assertNotIn("/s?by=", static)                                              # 저장 파일(링크 서버 없음)에는 링크가 없다
        live = up.render_query(self.db, "form", "씨름", link_base="")
        self.assertIn('href="/s?by=form&amp;q=%EC%9E%85%EC%94%A8%EB%A6%84"', live)        # 관련어 입씨름(표제어로 있음)
        self.assertIn('href="/s?by=hist&amp;q=%EC%8B%9C%EB%A6%84"', live)                # 옛 표기 시름: 다른 표제어에도 쓰임
        # 표제어가 아닌 관련어와 다른 표제어에 안 쓰인 옛 표기는 링크로 만들지 않는다
        for code, rel in self.db.execute("SELECT target_code, related_forms FROM entries WHERE related_forms IS NOT NULL LIMIT 400"):
            missing = [w for w in up.split_words(rel) if not ul.find_form(self.db, ul.norm(w))]
            if missing:
                html = up.entry_html(up.load_entry(self.db, code), up.Ctx(self.db, ""))
                for w in missing:
                    self.assertNotIn(f"q={up.urllib.parse.quote(ul.norm(w))}", html)
                break
        old_only = up.entry_html(up.load_entry(self.db, 4548), up.Ctx(self.db, ""))
        self.assertNotIn("/s?by=hist&amp;q=%EA%B3%B3", old_only)                         # 곳어름: 고드름에만 쓰임

    def test_other_forms_listed_when_not_in_timeline(self):
        d = up.load_entry(self.db, 4548)
        html = up.timeline_html(d)
        self.assertIn("그 밖의 표기:", html)
        self.assertIn("고두룸", html)                                                     # other_forms 에만 있는 표기
        self.assertEqual(html.count("고도름"), 1)                                         # 어휘 역사 칸에 있는 표기는 되풀이하지 않는다

    def test_list_page_headwords_link_only_when_live(self):
        self.assertNotIn("/s?by=", self.page("prefix", "고드"))
        self.assertIn('href="/s?by=form&amp;q=', up.render_query(self.db, "prefix", "고드", link_base=""))

    def test_site_link_goes_to_the_exact_entry_and_there_is_no_raw_button(self):
        html = self.page("form", "서리")
        self.assertEqual(html.count("우리말역사어휘사전 ↗"), 2)
        self.assertIn("searchResultDetail?searchWord=%EC%84%9C%EB%A6%AC&amp;etymNo=861", html)    # 항목 바로가기(etymNo = target_code)
        self.assertIn("etymNo=2161", html)
        self.assertIn('target="_blank" rel="noopener noreferrer"', html)               # 새 창, 출처 정보 안 보냄
        live = up.render_query(self.db, "form", "서리", link_base="")
        self.assertNotIn("/raw/", live)                                                # 원본 데이터 버튼·화면은 두지 않는다
        self.assertNotIn("원본 데이터", live)

    def test_notes_for_merged_truncated_and_download_only(self):
        merged = self.db.execute("SELECT target_code FROM entries WHERE merged_fields LIKE '%language_info%' LIMIT 1").fetchone()[0]
        self.assertIn("뒷부분은 누리집 전체 내려받기 파일에서 이어 붙인 것", up.entry_html(up.load_entry(self.db, merged)))
        cut = self.db.execute("SELECT target_code FROM entries WHERE truncated_fields IS NOT NULL LIMIT 1").fetchone()[0]
        html = up.entry_html(up.load_entry(self.db, cut))
        self.assertIn("중간에서 잘려 있습니다", html)
        self.assertIn("우리말역사어휘사전 ↗</a>에서 볼 수 있습니다", html)
        down = self.db.execute("SELECT target_code FROM entries WHERE source='download' LIMIT 1").fetchone()[0]
        self.assertIn("내려받기 파일의 내용입니다", up.entry_html(up.load_entry(self.db, down)))
        plain = self.db.execute("SELECT target_code FROM entries WHERE source='api' AND merged_fields IS NULL AND truncated_fields IS NULL "
                                "AND language_info IS NOT NULL LIMIT 1").fetchone()[0]
        self.assertNotIn('class="warn"', up.entry_html(up.load_entry(self.db, plain)))   # 문제없는 항목에는 안내가 없다

    def test_truncated_count_matches_known_leftovers(self):
        n = self.db.execute("SELECT COUNT(*) FROM entries WHERE truncated_fields IS NOT NULL").fetchone()[0]
        self.assertGreater(n, 0)
        self.assertLess(n, 100)                                                         # 병합이 거의 다 되고 소수만 남는다(약 23개)

    def test_miss(self):
        with self.assertRaises(ul.Miss):
            self.page("form", "쀏쀏쀏쀏")

    def test_no_script_injection_from_title(self):
        html = up.render_page(self.db, ul.resolve(self.db, "form", "고드름", 30), "<script>alert(1)</script>")
        self.assertNotIn("<script>alert(1)", html)


@needs_db
class ViewServerQuery(unittest.TestCase):
    def setUp(self):
        self.srv = uv.ViewServer()
        self.addCleanup(self.srv.stop)

    def test_link_serves_structured_page_with_all_examples(self):
        url = self.srv.add_query("고드름", "form", "고드름", 30, 8)
        page = urllib.request.urlopen(url, timeout=5).read().decode("utf-8")
        self.assertIn('class="timeline"', page)
        self.assertEqual(len(re.findall(r"<li><span class=\"txt\">", page)), 15)   # 채팅 결과가 줄여져도 링크에는 용례가 모두 있다

    def test_same_query_same_link_and_other_query_other_link(self):
        a = self.srv.add_query("t", "form", "고드름", 30, 8)
        self.assertEqual(a, self.srv.add_query("t", "form", "고드름", 30, 8))
        self.assertNotEqual(a, self.srv.add_query("t", "form", "고드름", 30, 5))

    def test_search_route(self):
        base = self.srv.add_query("t", "form", "고드름", 30, 8).rsplit("/v/", 1)[0]
        ok = urllib.request.urlopen(base + "/s?by=form&q=%EA%B3%A0%EB%93%9C%EB%A6%84", timeout=5)
        self.assertEqual(ok.status, 200)
        self.assertIn('class="timeline"', ok.read().decode("utf-8"))
        for url, code in ((base + "/s?by=zzz&q=a", 400), (base + "/s?by=form", 400), (base + "/s?by=form&q=" + "a" * 61, 400),
                          (base + "/s?by=form&q=%EC%80%8F%EC%80%8F%EC%80%8F", 404)):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(url, timeout=5)
            self.assertEqual(cm.exception.code, code, url)

    def test_links_inside_served_page_point_to_search_route(self):
        url = self.srv.add_query("씨름", "form", "씨름", 30, 8)
        page = urllib.request.urlopen(url, timeout=5).read().decode("utf-8")
        self.assertIn('href="/s?by=form&amp;q=', page)

    def test_raw_route_is_gone(self):
        base = self.srv.add_query("t", "form", "고드름", 30, 8).rsplit("/v/", 1)[0]
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(base + "/raw/4548", timeout=5)
        self.assertEqual(cm.exception.code, 404)

    def test_vanished_word_gives_404(self):
        url = self.srv.add_query("x", "form", "쀏쀏쀏쀏", 30, 8)
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(url, timeout=5)
        self.assertEqual(cm.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
