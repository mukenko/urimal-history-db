"""조회 결과를 글꼴이 지정된 HTML 로 만들어 여는 기능(urimal_html, `urimal_lookup.py … --html`).

Claude 앱 같은 채팅 화면은 옛한글(조합 자모)과 한자 자형을 시스템 글꼴 폴백으로 그려 깨지거나 자형이 달라진다(2026-10-15).
글꼴을 지정한 HTML 에서는 누리집처럼 나온다. 글자는 DB 원문 그대로 넣고, 모델은 개입하지 않는다.

실행(저장소 루트): python3 -m unittest tests.test_html -v
"""
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import urimal_html as uh  # noqa: E402

DB_OK = (ROOT / "data" / "urimal.sqlite").exists()
SAMPLE = "■ 고래(1) [단어]\n  [용례]\n      · 브ᅀᅥᆨ 구듨 골샛 거믜여ᇰ.  <1489(17세기) 구급간이방언해 06:77ㄱ>\n        원문: 竈突內煤.\n        번역: 부엌 구들의 고래에 있는 검댕."


class Render(unittest.TestCase):
    def test_korean_language_and_old_hangul_fonts(self):
        html = uh.render_html(SAMPLE, "고래")
        self.assertIn('<html lang="ko">', html)                          # 한자를 한국식 자형으로
        self.assertIn("NanumBarunGothicOTF YetHangul", html)
        self.assertIn("NanumMyeongjoOTF YetHangul", html)               # 바른고딕에 없는 글자를 채우는 예비
        self.assertIn('<meta charset="utf-8">', html)

    def test_original_text_is_kept_character_for_character(self):
        html = uh.render_html(SAMPLE, "고래")
        for piece in ("브ᅀᅥᆨ 구듨 골샛 거믜여ᇰ.", "竈突內煤", "06:77ㄱ"):
            self.assertIn(piece, html, piece)

    def test_html_is_escaped(self):
        html = uh.render_html("a < b & <script>x</script>", "t<>")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("a &lt; b &amp;", html)
        self.assertIn("<title>t&lt;&gt;</title>", html)

    def test_nbsp_entities_in_the_data_become_spaces(self):
        html = uh.render_html("雄曰鯨&nbsp;&nbsp;&nbsp;鯢", "t")
        self.assertNotIn("&amp;nbsp;", html)
        self.assertIn("雄曰鯨   鯢", html)

    def test_line_breaks_and_indentation_are_preserved(self):
        html = uh.render_html("a\n  b", "t")
        self.assertIn("white-space: pre-wrap", html)
        self.assertIn("<pre>a\n  b</pre>", html)

    def test_attribution_is_included(self):
        self.assertIn("CC BY-SA 2.0 KR", uh.render_html(SAMPLE, "고래"))


class EmbeddedFont(unittest.TestCase):
    """Safari 는 웹 페이지가 사용자가 설치한 글꼴을 쓰지 못하게 막아(지문 방지) 옛한글이 깨진다(2026-10-16).
    그래서 글꼴 파일(바른고딕 옛한글판)을 HTML 안에 웹 글꼴로 직접 넣는다. Chrome 은 설치된 글꼴을 그대로 써도 된다."""

    def with_font_dir(self, files):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        for name, data in files.items():
            (Path(d.name) / name).write_bytes(data)
        old = os.environ.get("URIMAL_FONT_DIR")
        os.environ["URIMAL_FONT_DIR"] = d.name
        self.addCleanup(lambda: os.environ.pop("URIMAL_FONT_DIR") if old is None else os.environ.__setitem__("URIMAL_FONT_DIR", old))
        return d.name

    def test_font_file_is_embedded_as_a_web_font(self):
        import base64
        self.with_font_dir({"NanumBarunGothic-YetHangul.otf": b"FAKEFONTBYTES"})
        html = uh.render_html("옛", "t")
        self.assertIn("@font-face", html)
        self.assertIn("font-family: 'UrimalYetHangul'", html)
        self.assertIn("data:font/otf;base64," + base64.b64encode(b"FAKEFONTBYTES").decode(), html)

    def test_barun_gothic_is_the_primary_font(self):
        self.with_font_dir({"NanumBarunGothic-YetHangul.otf": b"x"})
        html = uh.render_html("옛", "t")
        stack = html.split("body { font-family:")[1].split(";")[0]
        names = [n.strip(" '") for n in stack.split(",")]
        self.assertEqual(names[0], "UrimalYetHangul")                 # 내장한 바른고딕이 먼저
        self.assertLess(names.index("NanumBarunGothicOTF YetHangul"), names.index("NanumMyeongjoOTF YetHangul"))
        self.assertEqual(names[-1], "sans-serif")                      # 고딕 계열로 끝난다

    def test_without_the_font_file_it_falls_back_to_installed_fonts(self):
        self.with_font_dir({})
        html = uh.render_html("옛", "t")
        self.assertNotIn("@font-face", html)
        self.assertIn("NanumBarunGothicOTF YetHangul", html)

    def test_embedding_can_be_switched_off(self):
        self.with_font_dir({"NanumBarunGothic-YetHangul.otf": b"x"})
        os.environ["URIMAL_HTML_EMBED_FONT"] = "0"
        self.addCleanup(os.environ.pop, "URIMAL_HTML_EMBED_FONT", None)
        self.assertNotIn("@font-face", uh.render_html("옛", "t"))


class PathAndCleanup(unittest.TestCase):
    def test_filename_is_safe_and_stable(self):
        with tempfile.TemporaryDirectory() as d:
            p = uh.html_path("고래", Path(d))
            self.assertEqual(p.parent, Path(d))
            self.assertEqual(p.name, "고래.html")
            self.assertEqual(uh.html_path("a/b:c*?", Path(d)).name, "a_b_c__.html")
            self.assertLessEqual(len(uh.html_path("가" * 100, Path(d)).stem), 40)
            self.assertEqual(uh.html_path("", Path(d)).name, "result.html")

    def test_old_files_are_removed_and_recent_ones_kept(self):
        with tempfile.TemporaryDirectory() as d:
            old, new, other = Path(d) / "old.html", Path(d) / "new.html", Path(d) / "keep.txt"
            for f in (old, new, other):
                f.write_text("x")
            two_days_ago = time.time() - 2 * 86400
            os.utime(old, (two_days_ago, two_days_ago))
            os.utime(other, (two_days_ago, two_days_ago))
            uh.cleanup_old(Path(d), days=1)
            self.assertFalse(old.exists())
            self.assertTrue(new.exists())
            self.assertTrue(other.exists())                              # html 이 아닌 파일은 건드리지 않는다


@unittest.skipUnless(DB_OK, "조회용 DB가 없습니다")
class Cli(unittest.TestCase):
    def test_html_option_writes_the_file_without_opening_it(self):
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, URIMAL_HTML_DIR=d)
            r = subprocess.run([sys.executable, str(ROOT / "urimal_lookup.py"), "고래", "--html", "--no-open"],
                               capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            files = list(Path(d).glob("*.html"))
            self.assertEqual([f.name for f in files], ["고래.html"])
            html = files[0].read_text(encoding="utf-8")
            import sqlite3
            db = sqlite3.connect(f"file:{ROOT / 'data' / 'urimal.sqlite'}?mode=ro", uri=True)
            original = db.execute("SELECT example FROM examples WHERE example LIKE '%구듨 골샛%' ORDER BY id LIMIT 1").fetchone()[0]
            db.close()
            self.assertIn(original.rstrip("."), html)                    # DB 원문 그대로(분해된 자모 순서까지 같다)
            self.assertTrue(any(0x1100 <= ord(c) <= 0x11FF for c in original))   # 실제로 조합 자모가 들어 있는 줄이다
            self.assertIn('lang="ko"', html)
            self.assertIn("고래.html", r.stdout)                           # 저장 위치를 알려 준다

    def test_plain_lookup_is_unchanged(self):
        r = subprocess.run([sys.executable, str(ROOT / "urimal_lookup.py"), "고래", "--examples", "0"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("고래(1)", r.stdout)
        self.assertNotIn("<html", r.stdout)


if __name__ == "__main__":
    unittest.main()
