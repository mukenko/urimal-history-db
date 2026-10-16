"""urimal_view: 조회 결과를 클릭할 때 HTML 로 보여 주는 이 컴퓨터 전용 작은 웹 서버(2026-10-16).

채팅 답변 끝의 링크(http://127.0.0.1:포트/v/키)를 누르면 그때 HTML 이 만들어진다. 조회마다 파일을 만들지 않고, 글꼴은 별도 주소(/f/font.otf)로
한 번만 보낸다. 127.0.0.1 에만 열리고, 추측할 수 없는 키·Host 검사·GET 만 허용한다.

실행(저장소 루트): python3 -m unittest tests.test_view -v
"""
import http.client
import os
import re
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import urimal_view as uv  # noqa: E402

# 옛한글은 코드 포인트로 적는다(소스에 글자를 직접 쓰면 NFC 로 합쳐져 DB 원문과 달라질 수 있다)
OLD = "브ᅀᅥᆨ 구듨 골샘 거믜여ᇰ."


def fetch(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


class ServerCase(unittest.TestCase):
    def setUp(self):
        self.srv = uv.ViewServer()
        self.srv.start()
        self.addCleanup(self.srv.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old_env = {k: os.environ.get(k) for k in ("URIMAL_FONT_DIR", "URIMAL_HTML_EMBED_FONT")}
        self.addCleanup(self.restore_env)
        os.environ["URIMAL_FONT_DIR"] = self.tmp.name          # 기본은 글꼴 없음(각 테스트가 필요하면 만든다)
        os.environ.pop("URIMAL_HTML_EMBED_FONT", None)

    def restore_env(self):
        for k, v in self.old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class Basics(ServerCase):
    def test_binds_to_loopback_only_in_a_daemon_thread(self):
        self.assertEqual(self.srv.httpd.server_address[0], "127.0.0.1")
        self.assertTrue(self.srv.thread.daemon)
        self.assertGreater(self.srv.port, 0)

    def test_start_is_idempotent(self):
        port = self.srv.port
        self.srv.start()
        self.assertEqual(self.srv.port, port)

    def test_add_returns_a_link_and_the_page_has_the_exact_text(self):
        url = self.srv.add("고래", OLD)
        self.assertRegex(url, rf"^http://127\.0\.0\.1:{self.srv.port}/v/[0-9a-f]{{32}}$")
        status, headers, body = fetch(url)
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn("charset=utf-8", headers["Content-Type"].lower())
        html = body.decode("utf-8")
        self.assertIn('lang="ko"', html)
        self.assertIn(OLD, html)                                   # 글자 그대로
        self.assertIn("<title>고래</title>", html)

    def test_same_text_gives_the_same_link(self):
        self.assertEqual(self.srv.add("고래", OLD), self.srv.add("고래", OLD))
        self.assertNotEqual(self.srv.add("고래", OLD), self.srv.add("고래", OLD + "x"))

    def test_oldest_views_are_dropped(self):
        old_max = uv.MAX_VIEWS
        uv.MAX_VIEWS = 3
        self.addCleanup(setattr, uv, "MAX_VIEWS", old_max)
        urls = [self.srv.add("t", f"{OLD} {i}") for i in range(4)]
        self.assertEqual(fetch(urls[0])[0], 404)                  # 가장 오래된 것
        for u in urls[1:]:
            self.assertEqual(fetch(u)[0], 200)


class Safety(ServerCase):
    def test_unknown_and_malformed_paths_are_404(self):
        base = f"http://127.0.0.1:{self.srv.port}"
        for path in ("/", "/v/", "/v/" + "0" * 32, "/v/not-a-key", "/v/../etc/passwd", "/x", "/f/other.otf"):
            self.assertEqual(fetch(base + path)[0], 404, path)

    def test_only_get_is_allowed(self):
        url = self.srv.add("t", OLD)
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            conn = http.client.HTTPConnection("127.0.0.1", self.srv.port, timeout=5)
            conn.request(method, url.split(str(self.srv.port))[1], body=b"x")
            self.assertEqual(conn.getresponse().status, 405, method)
            conn.close()

    def test_foreign_host_header_is_refused(self):
        url = self.srv.add("t", OLD)
        status, _, _ = fetch(url, headers={"Host": "evil.example"})           # DNS 재바인딩 방지
        self.assertEqual(status, 421)
        status, _, _ = fetch(url, headers={"Host": f"localhost:{self.srv.port}"})
        self.assertEqual(status, 200)

    def test_page_headers(self):
        _, headers, _ = fetch(self.srv.add("t", OLD))
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(headers.get("Referrer-Policy"), "no-referrer")

    def test_html_is_escaped(self):
        url = self.srv.add("t<>", "<script>alert(1)</script> & x")
        html = fetch(url)[2].decode("utf-8")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)


class Font(ServerCase):
    def make_font(self, data=b"FONTBYTES"):
        (Path(self.tmp.name) / "NanumBarunGothic-YetHangul.otf").write_bytes(data)

    def test_font_is_served_separately_and_the_page_links_to_it(self):
        self.make_font(b"FONTBYTES")
        base = f"http://127.0.0.1:{self.srv.port}"
        status, headers, body = fetch(base + "/f/font.otf")
        self.assertEqual((status, body), (200, b"FONTBYTES"))
        self.assertEqual(headers["Content-Type"], "font/otf")
        self.assertIn("max-age", headers.get("Cache-Control", ""))
        html = fetch(self.srv.add("t", OLD))[2].decode("utf-8")
        self.assertIn("@font-face", html)
        self.assertIn("url(/f/font.otf)", html)
        self.assertNotIn("base64", html)                          # 글꼴을 HTML 에 넣지 않아 작다

    def test_without_the_font_file_there_is_no_font_face(self):
        base = f"http://127.0.0.1:{self.srv.port}"
        self.assertEqual(fetch(base + "/f/font.otf")[0], 404)
        html = fetch(self.srv.add("t", OLD))[2].decode("utf-8")
        self.assertNotIn("@font-face", html)
        self.assertIn("NanumBarunGothicOTF YetHangul", html)      # 설치된 글꼴 이름으로 예비

    def test_embedding_switch_also_turns_off_the_font_route(self):
        self.make_font()
        os.environ["URIMAL_HTML_EMBED_FONT"] = "0"
        self.assertEqual(fetch(f"http://127.0.0.1:{self.srv.port}/f/font.otf")[0], 404)


if __name__ == "__main__":
    unittest.main()
