"""조회 결과를 글꼴이 지정된 HTML 로 만든다. 표준 라이브러리만 사용.

Claude 앱 같은 채팅 화면은 옛한글(조합 자모: ᄒᆞ, ᄯ, ᇰ …)과 한자 자형을 시스템 글꼴 폴백으로 그려서, 옛한글은 빈 칸(▒)이나 갈라진 글자로
보이고 한자는 다른 지역 자형으로 보인다(2026-10-15). 데이터(조합 자모, 한자 코드)는 누리집과 같아서 정상이다. 글꼴을 지정하고
`lang="ko"` 를 주면 누리집처럼 나온다. 글자는 조회 결과 그대로 넣고(모델이 다시 쓰지 않는다) 줄바꿈·들여쓰기를 보존한다.

옛한글: 나눔명조/나눔바른고딕 옛한글판(NanumMyeongjoOTF YetHangul, NanumBarunGothicOTF YetHangul)이 설치되어 있어야 한다.
함초롬(HCR)은 일부 옛 글자가 비어서 뒤쪽 예비로만 둔다. 없는 글꼴은 브라우저가 건너뛴다.
"""
import base64
import html
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# 글꼴: 바른고딕 옛한글판을 HTML 안에 웹 글꼴(UrimalYetHangul)로 직접 넣고, 설치된 같은 글꼴(Chrome 용)과 명조판을 예비로 둔다.
# Safari 는 웹 페이지가 사용자가 설치한 글꼴을 쓰지 못하게 막아(지문 방지) 설치만으로는 옛한글이 깨지므로 내장이 필요하다(2026-10-16).
FONT_STACK = ("'UrimalYetHangul', 'NanumBarunGothicOTF YetHangul', 'NanumMyeongjoOTF YetHangul', "
              "'Apple SD Gothic Neo', sans-serif")
FONT_FILE = "NanumBarunGothic-YetHangul.otf"
FONT_DIRS = (Path.home() / "Library" / "Fonts", Path("/Library/Fonts"), Path("/System/Library/Fonts/Supplemental"))
ATTRIBUTION = "출처: 국립국어원 우리말역사어휘사전 (CC BY-SA 2.0 KR)"
DEFAULT_DIR = Path.home() / ".cache" / "urimal-db" / "html"
KEEP_DAYS = 1                                      # 로그 정리 원칙(오래된 것은 자동 삭제)


def html_dir():
    return Path(os.environ.get("URIMAL_HTML_DIR") or DEFAULT_DIR)


def _embedding_off():
    return os.environ.get("URIMAL_HTML_EMBED_FONT", "1").strip().lower() in ("0", "off", "false", "no")


def find_font():
    """바른고딕 옛한글판 글꼴 파일의 경로(없거나 URIMAL_HTML_EMBED_FONT=0 이면 None). 사용자의 컴퓨터에 설치된 것을 읽을 뿐이고 저장소에는 글꼴이 없다."""
    if _embedding_off():
        return None
    dirs = [Path(os.environ["URIMAL_FONT_DIR"])] if os.environ.get("URIMAL_FONT_DIR") else list(FONT_DIRS)
    for d in dirs:
        path = d / FONT_FILE
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    return None


def _font_face_css(font_url=None):
    """@font-face CSS. font_url 이 있으면 그 주소로 불러오고(웹 서버가 글꼴을 따로 보낼 때), 없으면 글꼴 파일을 base64 데이터 주소로
    HTML 안에 직접 넣는다(로컬 파일용). 글꼴 파일이 없으면 빈 문자열."""
    path = find_font()
    if path is None:
        return ""
    if font_url:
        return f"  @font-face {{ font-family: 'UrimalYetHangul'; src: url({font_url}) format('opentype'); }}\n"
    try:
        data = base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError:
        return ""
    return (f"  @font-face {{ font-family: 'UrimalYetHangul'; "
            f"src: url(data:font/otf;base64,{data}) format('opentype'); }}\n")


def render_html(text, title, font_url=None):
    """text 를 <pre> 안에 그대로 넣은 HTML. 데이터에 섞인 &nbsp; 는 공백으로 바꾼다. font_url 이 있으면 글꼴을 그 주소에서 불러온다."""
    body = html.escape(text.replace("&nbsp;", " "), quote=False)
    return f"""<!doctype html>
<html lang="ko">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title, quote=False)}</title>
<style>
{_font_face_css(font_url)}  :root {{ color-scheme: light dark; }}
  body {{ font-family: {FONT_STACK}; font-size: 18px; line-height: 1.75; max-width: 64rem; margin: 2rem auto; padding: 0 1rem; }}
  pre {{ white-space: pre-wrap; font: inherit; margin: 0; }}
  footer {{ margin-top: 2rem; font-size: 14px; opacity: .7; }}
</style>
<pre>{body}</pre>
<footer>{ATTRIBUTION}</footer>
"""


def html_path(label, base):
    """검색어로 안전한 파일 이름을 만든다(같은 검색어는 같은 파일을 덮어써서 쌓이지 않는다)."""
    name = re.sub(r"[^0-9A-Za-z가-힣ㄱ-ㆎ一-鿿\-]", "_", label.strip())[:40] or "result"
    return Path(base) / f"{name}.html"


def cleanup_old(base, days=KEEP_DAYS):
    """base 안의 오래된 .html 을 지운다(다른 파일은 건드리지 않는다)."""
    cutoff = time.time() - days * 86400
    for f in Path(base).glob("*.html"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


def save_html(text, label, open_it):
    """text 를 HTML 로 저장하고(오래된 파일은 지운다) 필요하면 기본 브라우저로 연다. (경로, 열었는지)를 돌려준다. 아무것도 출력하지 않는다.
    환경변수 URIMAL_HTML_OPEN=0 이면 열지 않는다(시험·원격 환경용)."""
    return save_rendered(render_html(text, label), label, open_it)


def save_rendered(page, label, open_it):
    """이미 만들어진 HTML 문자열 page 를 저장하고(오래된 파일은 지운다) 필요하면 기본 브라우저로 연다. (경로, 열었는지)."""
    base = html_dir()
    base.mkdir(parents=True, exist_ok=True)
    cleanup_old(base)
    path = html_path(label, base)
    path.write_text(page, encoding="utf-8")
    opened = False
    if open_it and os.environ.get("URIMAL_HTML_OPEN", "1").strip().lower() not in ("0", "off", "false", "no"):
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        try:
            opened = subprocess.run([opener, str(path)], check=False, timeout=15,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        except (OSError, subprocess.SubprocessError):
            opened = False
    return path, opened
