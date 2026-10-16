"""조회 결과를 클릭할 때 HTML 로 보여 주는, 이 컴퓨터에서만 열리는 작은 웹 서버. 표준 라이브러리만 사용.

채팅 화면은 옛한글(조합 자모)과 한자 자형을 시스템 글꼴 폴백으로 그려서 깨진다(2026-10-15). 답변 끝의 링크를 누르면 그때 글꼴이 지정된 HTML 이 열린다.
조회마다 파일을 만들지 않고(키는 결과 텍스트의 해시, 최근 MAX_VIEWS 개만 메모리에 둔다), 바른고딕 글꼴은 /f/font.otf 로 한 번만 보낸다.

  GET /v/<32자리 16진 키>  -> HTML (lang="ko", 글꼴 지정, 결과 글자 그대로)
  GET /f/font.otf          -> 바른고딕 옛한글판 파일(설치된 것을 읽을 뿐)

안전: 127.0.0.1 에만 바인드, 포트는 자동, 키는 해시(추측하려면 결과 텍스트를 알아야 함), Host 헤더 검사(DNS 재바인딩 방지), GET 만 허용,
다른 경로는 404. 보여 주는 것은 공개 사전 데이터(읽기 전용)뿐이다. 서버는 MCP 서버 프로세스가 끝나면 같이 끝난다(데몬 스레드).
"""
import hashlib
import http.server
import re
import threading
from collections import OrderedDict

import json
import urllib.parse

import urimal_html as uh
import urimal_lookup as ul
import urimal_page as up

MAX_VIEWS = 50
HOST = "127.0.0.1"
_KEY = re.compile(r"^/v/([0-9a-f]{32})$")
SEARCH_BYS = ("form", "hist", "prefix", "def")
_RAW = re.compile(r"^/raw/(\d{1,9})$")


class ViewServer:
    def __init__(self):
        self._views = OrderedDict()                 # 키 -> (제목, 텍스트), 오래된 것부터
        self._lock = threading.Lock()
        self.httpd = None
        self.thread = None
        self.port = None

    def start(self):
        """한 번만 띄운다(이미 떠 있으면 아무것도 안 한다)."""
        with self._lock:
            if self.httpd is not None:
                return
            server = self

            class Handler(http.server.BaseHTTPRequestHandler):
                server_version = "urimal-view"

                def log_message(self, *args):       # 로그를 남기지 않는다(stdout 은 MCP 프로토콜 전용)
                    pass

                def _send(self, status, body=b"", ctype="text/plain; charset=utf-8", extra=None):
                    self.send_response(status)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("Referrer-Policy", "no-referrer")
                    for k, v in (extra or {}).items():
                        self.send_header(k, v)
                    self.end_headers()
                    self.wfile.write(body)

                def do_GET(self):
                    host = (self.headers.get("Host") or "").lower()
                    if host not in (f"{HOST}:{server.port}", f"localhost:{server.port}"):
                        return self._send(421, b"misdirected request")
                    path = self.path.split("?", 1)[0]
                    m = _KEY.match(path)
                    if m:
                        view = server.get(m.group(1))
                        if view is None:
                            return self._send(404, b"not found")
                        font_url = "/f/font.otf" if uh.find_font() else None
                        if isinstance(view[1], dict):      # 검색 조건: 클릭할 때 DB 에서 읽어 구조를 살려 그린다
                            html = server.render_spec(view[0], view[1], font_url)
                            if html is None:
                                return self._send(404, b"not found")
                        else:
                            html = uh.render_html(view[1], view[0], font_url=font_url)
                        return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8", {"Cache-Control": "no-store"})
                    if path == "/s":                       # 화면 안의 링크(관련어·옛 표기)가 부르는 조회: /s?by=form&q=낱말
                        qs = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "", keep_blank_values=True)
                        by, q = (qs.get("by") or ["form"])[0], (qs.get("q") or [""])[0].strip()
                        if by not in SEARCH_BYS or not q or len(q) > 60:
                            return self._send(400, b"bad request")
                        font_url = "/f/font.otf" if uh.find_font() else None
                        html = server.render_spec(q, {"by": by, "q": q, "limit": 30, "max_entries": 8}, font_url)
                        if html is None:
                            return self._send(404, "찾지 못했습니다.".encode("utf-8"))
                        return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8", {"Cache-Control": "no-store"})
                    m = _RAW.match(path)
                    if m:                                  # 이 DB에 저장된 원본 데이터(raw.sqlite)
                        font_url = "/f/font.otf" if uh.find_font() else None
                        html = server.render_raw_page(int(m.group(1)), font_url)
                        if html is None:
                            return self._send(404, b"not found")
                        return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8", {"Cache-Control": "no-store"})
                    if path == "/f/font.otf":
                        font = uh.find_font()
                        if font is None:
                            return self._send(404, b"not found")
                        try:
                            data = font.read_bytes()
                        except OSError:
                            return self._send(404, b"not found")
                        return self._send(200, data, "font/otf", {"Cache-Control": "max-age=86400"})
                    return self._send(404, b"not found")

                def _deny(self):
                    self._send(405, b"method not allowed", extra={"Allow": "GET"})

                do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _deny

            httpd = http.server.ThreadingHTTPServer((HOST, 0), Handler)
            httpd.daemon_threads = True
            self.httpd, self.port = httpd, httpd.server_address[1]
            self.thread = threading.Thread(target=httpd.serve_forever, name="urimal-view", daemon=True)
            self.thread.start()

    def stop(self):
        with self._lock:
            httpd, self.httpd = self.httpd, None
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()

    def add(self, title, text):
        """결과 텍스트를 등록하고 링크(URL)를 돌려준다. 같은 결과는 같은 링크다."""
        self.start()
        key = hashlib.sha256((title + "\0" + text).encode("utf-8")).hexdigest()[:32]
        with self._lock:
            self._views.pop(key, None)
            self._views[key] = (title, text)
            while len(self._views) > MAX_VIEWS:
                self._views.popitem(last=False)
        return f"http://{HOST}:{self.port}/v/{key}"

    def add_query(self, title, by, q, limit=50, max_entries=None):
        """검색 조건(찾는 방식·검색어·개수)을 등록하고 링크를 돌려준다. 클릭하면 그때 DB 에서 읽어 구조를 살린 HTML(urimal_page)로 보여 준다
        (채팅 답에 든 결과가 줄여져 있어도 링크에서는 용례를 모두 볼 수 있다). 같은 조건은 같은 링크다."""
        self.start()
        spec = {"by": by, "q": q, "limit": limit, "max_entries": max_entries}
        key = hashlib.sha256((title + "\0" + json.dumps(spec, sort_keys=True, ensure_ascii=False)).encode("utf-8")).hexdigest()[:32]
        with self._lock:
            self._views.pop(key, None)
            self._views[key] = (title, spec)
            while len(self._views) > MAX_VIEWS:
                self._views.popitem(last=False)
        return f"http://{HOST}:{self.port}/v/{key}"

    def render_spec(self, title, spec, font_url):
        """등록된 검색 조건으로 HTML 을 만든다. DB 가 없거나 이제 찾지 못하면 None."""
        try:
            db = ul.connect()
        except SystemExit:
            return None
        try:
            return up.render_query(db, spec["by"], spec["q"], spec["limit"], spec["max_entries"], title=title, font_url=font_url, link_base="")
        except ul.Miss:
            return None
        finally:
            db.close()

    def render_raw_page(self, code, font_url):
        """한 항목의 원본 데이터 화면. 조회용 DB 에 없는 코드이면 None(원본 DB 가 없어도 안내만 하는 화면을 돌려준다)."""
        try:
            db = ul.connect()
        except SystemExit:
            return None
        rdb = up.open_raw()
        try:
            return up.render_raw(db, rdb, code, font_url)
        finally:
            db.close()
            if rdb is not None:
                rdb.close()

    def get(self, key):
        with self._lock:
            return self._views.get(key)


_server = None
_server_lock = threading.Lock()


def get_server():
    """프로세스당 하나의 서버."""
    global _server
    with _server_lock:
        if _server is None:
            _server = ViewServer()
        _server.start()
        return _server
