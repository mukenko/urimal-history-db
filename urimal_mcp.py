#!/usr/bin/env python3
"""우리말역사어휘사전 MCP 서버. 조회는 모두 urimal_lookup.py(조회 코어)가 하고, 이 파일은 MCP 도구로 감싸기만 한다.

  .venv/bin/python urimal_mcp.py                      # stdio (LM Studio, Claude 데스크톱이 프로세스를 띄움)
  .venv/bin/python urimal_mcp.py --http [--port 8767] # Streamable HTTP (127.0.0.1 에만 바인드, Bearer 토큰 필수)
  python3 urimal_mcp.py --call lookup_word 고드름     # SDK 없이 도구 결과 확인(시스템 python 도 가능)
  python3 urimal_mcp.py --call search_dictionary 곳어름 historical_form

읽기 전용 도구 2개. DB 경로는 환경변수 URIMAL_DB(기본 data/urimal.sqlite).
모델에 전달하는 문구(지침·도구 설명)는 영어다: 작은 로컬 모델이 더 안정적으로 따르고 토큰이 덜 든다(stdict-db 와 같은 방침).
stdout 은 MCP 프로토콜 전용이므로 도구 실행 중의 출력은 따로 받아 문자열로 돌려준다. 도구가 실패해도 서버는 죽지 않고 오류 문장을 돌려준다.

HTTP 모드(서버에서 상시 실행하는 용도. 앞에 tailscale serve 같은 프록시를 두어 신뢰하는 네트워크에만 노출):
  - 바인드는 127.0.0.1 로 고정(옵션 없음). 외부 노출은 프록시가 맡는다.
  - 토큰 파일(URIMAL_MCP_TOKEN_FILE, 기본 ~/.config/urimal-mcp/token)이 **반드시** 있어야 시작한다. 없거나 비어 있거나
    권한이 600 보다 느슨하면 시작하지 않는다. 요청마다 'Authorization: Bearer <토큰>' 을 요구하며 토큰은 어디에도 출력하지 않는다.
  - DNS rebinding 보호는 켠 채, 허용 Host 는 127.0.0.1/localhost 와 환경변수 URIMAL_MCP_HOSTS(쉼표로 구분, 예: myhost:8767)이다.
  - 도구는 스레드로 실행하고, 조회마다 DB 연결을 새로 연다(읽기 전용).
"""
import argparse
import asyncio
import contextlib
import functools
import hmac
import inspect
import io
import os
import sys
from pathlib import Path
from typing import Literal

import urimal_lookup as ul
import urimal_view as view

SERVER_NAME = "urimal-history"
HTTP_HOST = "127.0.0.1"                 # 바인드 주소는 고정한다(외부에 직접 열지 않는다)
HTTP_DEFAULT_PORT = 8767
HTTP_MODE = False                       # --http 로 띄우면 True(원격 서버는 사용자 화면에서 브라우저를 열 수 없다)
TOKEN_FILE = Path(os.environ.get("URIMAL_MCP_TOKEN_FILE") or Path.home() / ".config" / "urimal-mcp" / "token")

INSTRUCTIONS = (
    "Historical Korean Vocabulary Dictionary (우리말역사어휘사전, National Institute of Korean Language): about 15,000 "
    "modern Korean words, each with how its spelling changed over the centuries (15th to 20th century, including "
    "Old Hangul forms), the etymology/history notes, and dated usage examples with their sources. "
    "Use these tools to check the history, old spellings and etymology of a word instead of answering from memory. "
    "One spelling can have several homonyms (shown with a number, e.g. 예(1) 예(2)); read ALL of them and choose by context. "
    "Keep three tiers of your answer clearly apart. "
    "(1) Dictionary facts: quote them exactly from the tool output and never invent or \"recall\" them: entries and "
    "homonym numbers, old spellings and forms (Old Hangul letters, e.g. ᄀᆞ, ᅌ, must be copied exactly as shown), "
    "dates, sources, examples and etymology notes. Never attach glosses, classifications or usage registers to a "
    "word unless the tool output states them. "
    "(2) \"(추론)\": your own comparison or judgement based on the listed entries. "
    "(3) \"[사전 외 설명]\": general explanation, background or advice that is not based on the tool output, in a "
    "separate part. "
    "Match your research effort to the question and keep the user in control of how long it takes. A simple check "
    "(an old spelling, one word's history) needs no extra lookups. For broader questions (related words, comparing "
    "the histories of several words), first report what the entries themselves list, then offer in one line what you "
    "could look up next (definition or prefix searches with different key phrases, related headwords); do not start "
    "that search yet. When the user agrees, or asks from the start for a broad or thorough search (e.g. \"더 찾아줘\", "
    "\"자세히\"), do a bounded search of about 3 to 4 extra tool calls: one or two definition or prefix searches and "
    "lookups of the two or three most promising headwords. Mark your own comparison as \"(추론)\"; then stop and say "
    "what you did not cover. Go wider only when the user asks. "
    "When a tool result contains a line starting with \"원본 보기:\" followed by a link, end your answer with that link "
    "as a Markdown link ([원본 보기](URL)), without asking and without explaining why; a found entry always has it. "
    "Copy Old Hangul letters exactly as shown and never retype them into an artifact. "
    "Some long notes may be cut off in the source data, and text appended from the bulk download lacks double quotes. "
    "If a tool says a word is not an entry, say so; do not invent dictionary content. "
    "Answer in the user's language (Korean unless the user writes otherwise) and mention that the source is the "
    "Historical Korean Vocabulary Dictionary (우리말역사어휘사전; CC BY-SA 2.0 KR)."
)

# 모든 조회 결과 끝에 붙이는 알림. 모델에 대한 부탁이며 강제는 아니다.
# 인용·표시 방법만 담는다. 탐색 횟수 같은 행동 지시는 도구 결과 안에 넣지 않는다(Claude 가 프롬프트 주입으로 보고 따르지 않고
# 매번 사용자에게 알린다, 2026-10-15). 그런 기준은 서버 지침(INSTRUCTIONS)과 도구 설명에만 둔다.
# 서버 지침을 읽는 클라이언트(Claude 등)에는 환경변수 URIMAL_MCP_NOTICE=0 으로 알림을 끌 수 있다.
NOTICE = ("[Note to assistant: quote dictionary facts exactly and never invent them or attach labels, glosses or "
          "registers the output does not state; copy Old Hangul exactly. Mark your own judgement as \"(추론)\" and "
          "anything beyond the tool output as \"[사전 외 설명]\". State that the source is the Historical Korean "
          "Vocabulary Dictionary (우리말역사어휘사전).]")


ATTRIBUTION = "출처: 국립국어원 우리말역사어휘사전 (CC BY-SA 2.0 KR)"


def _view_link_enabled():
    """이 컴퓨터의 로컬 서버일 때만 링크를 만든다(원격 서버의 127.0.0.1 은 사용자 컴퓨터가 아니다). URIMAL_VIEW_LINK=0 이면 끈다."""
    return not HTTP_MODE and os.environ.get("URIMAL_VIEW_LINK", "1").strip().lower() not in ("0", "off", "false", "no")


def _with_footer(text, label="", spec=None, found=True):
    """결과 뒤에 (로컬 서버이고 찾았으면) 보기 링크 한 줄과 출처 줄을 붙인다. 링크 줄은 지시가 아니라 주소만 담은 사실이다: 링크를 적는
    규칙은 서버 지침·도구 설명에 둔다(도구 결과 안의 지시는 Claude 가 주입으로 본다). spec 은 링크 서버가 클릭할 때 다시 그릴 검색 조건이다."""
    extra = ""
    if spec is not None and found and _view_link_enabled():
        try:
            extra += f"\n원본 보기: {view.get_server().add_query(label or 'result', **spec)}"
        except Exception:  # noqa: BLE001  서버를 못 띄워도 조회는 돌려준다
            pass
    return text + extra + "\n" + ATTRIBUTION


def _notice_tail():
    """결과 끝에 붙일 알림(줄바꿈 포함). URIMAL_MCP_NOTICE 가 0/off/false/no 면 붙이지 않는다."""
    if os.environ.get("URIMAL_MCP_NOTICE", "1").strip().lower() in ("0", "off", "false", "no"):
        return ""
    return "\n" + NOTICE


BY_MAP = {"historical_form": "hist", "definition": "def", "prefix": "prefix"}


def _query_text(by, q, examples, limit, max_entries=None):
    """조회 코어는 stdout 으로 출력하므로 그 출력을 모아 (찾았는지, 문자열)로 돌려준다(프로토콜 stdout 은 건드리지 않는다)."""
    buf = io.StringIO()
    try:
        db = ul.connect()
    except SystemExit:
        return False, "The dictionary database is not available. Build it with: python3 urimal_build.py"
    try:
        with contextlib.redirect_stdout(buf):
            ul.query(db, by, q, examples, limit, max_entries)
    except ul.Miss as e:
        return False, str(e)
    finally:
        db.close()
    return True, buf.getvalue().strip()


def _run(by, q, examples, limit, max_entries=None):
    return _query_text(by, q, examples, limit, max_entries)[1]


def _safe(fn):
    """도구 실패가 서버를 죽이지 않게 한다."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            print(f"[urimal MCP] {fn.__name__} 오류: {type(e).__name__}", file=sys.stderr)
            return "사전 조회 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."
    return wrapper


@_safe
def lookup_word(word: str, max_entries: int = 8, examples_per_century: int = 1) -> str:
    """Look up a modern Korean word in the Historical Korean Vocabulary Dictionary (우리말역사어휘사전).
    Returns ALL homonyms of the spelling (numbered), each with its definition(s), the chain of historical spellings with
    the centuries they appear in (e.g. 곳어름 17-19c -> 고도름 -> 고드름), the history/etymology notes, related words,
    dated usage examples grouped by century (with source, Hanzi original and modern-Korean translation when given) and
    academic references. If the word is not an entry but appears as an OLD spelling of an entry (e.g. 곳어름 -> 고드름),
    that entry is shown; otherwise entries starting with the text are listed.
    Hyphens typed by the user are handled; do not add them yourself.
    If the result has a line starting with "원본 보기:", it is a link to a page that shows the found entries from the
    local database; end your answer with that link as a Markdown link ([원본 보기](URL)), without asking and without
    explaining why.
    Anything that explains WHY or HOW beyond what this tool returns (for example why a letter or sound changed,
    historical-linguistics background, usage advice) is not dictionary content: put it in a separate part labeled
    "[사전 외 설명]". End the answer with the source line.
    Args: word = the Korean word (modern or old spelling), e.g. "고드름"; max_entries = how many homonyms to show
    (default 8); examples_per_century = usage examples shown per century and spelling (default 1, 0 = none, max 5).
    With the default, each century says how many more examples exist ("4개 중 1개 표시, 3개 더 있음") and a total line
    follows; tell the user so. To see more of them, raise examples_per_century (max 5); the link page shows all."""
    n = max(0, min(int(examples_per_century), 5))
    cap = max(1, min(int(max_entries), 20))
    found, out = _query_text("form", word, n, 30, cap)
    return _with_footer(out, word, {"by": "form", "q": word, "limit": 30, "max_entries": cap}, found) + _notice_tail()


@_safe
def search_dictionary(query: str, by: Literal["historical_form", "definition", "prefix"], limit: int = 10) -> str:
    """Reverse search in the Historical Korean Vocabulary Dictionary (우리말역사어휘사전).
    by="historical_form": find modern words that were once written with the given OLD spelling, e.g. "곳어름" or "ᄀᆞᆺ".
    by="definition": find words whose definition contains the phrase (any length; 3+ characters use a fast index).
    by="prefix": list entries whose headword starts with the text, e.g. "고드".
    To find related words, vary the query: use key phrases taken from a definition; a few calls are enough.
    A result with up to 3 hits shows the full entries; more hits are listed with their first definition. Use lookup_word
    with a listed headword to see one entry in full.
    If the result has a line starting with "원본 보기:", it is a link to a page that shows the found entries from the
    local database; end your answer with that link as a Markdown link ([원본 보기](URL)), without asking and without
    explaining why.
    Explanations beyond the returned entries belong in a separate part labeled "[사전 외 설명]"; end the answer
    with the source line.
    Args: query = the text to search; by = the search kind above; limit = maximum results (default 10, max 50)."""
    lim = max(1, min(int(limit), 50))
    found, out = _query_text(BY_MAP[by], query, 1, lim)
    return _with_footer(out, query, {"by": BY_MAP[by], "q": query, "limit": lim}, found) + _notice_tail()


TOOLS = (lookup_word, search_dictionary)


def _check_db():
    """시작할 때 DB 를 한 번 열어 본다. 안 열리면 이유를 stderr 에 남기고 바로 끝낸다."""
    try:
        db = ul.connect()
    except SystemExit:
        sys.exit(f"[urimal MCP] 사전 DB를 열 수 없습니다: {ul.DB_PATH} (URIMAL_DB 환경변수 또는 data/urimal.sqlite 확인, urimal_build.py 로 만드세요)")
    try:
        n = db.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    finally:
        db.close()
    return n


def _cli_call(argv):
    """--call 도구이름 인자...: SDK 없이 도구 결과를 확인한다."""
    names = {t.__name__: t for t in TOOLS}
    if not argv or argv[0] not in names:
        sys.exit(f"사용법: --call {{{'|'.join(names)}}} 인자...  (인자는 도구 설명의 순서대로)")
    fn = names[argv[0]]
    params = list(inspect.signature(fn).parameters.values())
    args = [int(raw) if p.annotation is int else raw for p, raw in zip(params, argv[1:])]
    print(fn(*args))


def _load_token():
    """HTTP 는 토큰이 필수다. 없거나, 비어 있거나, 다른 사용자가 읽을 수 있으면 시작하지 않는다(ssh 키와 같은 규칙)."""
    try:
        st = TOKEN_FILE.stat()
    except FileNotFoundError:
        sys.exit(f"[urimal MCP] 토큰 파일이 없습니다: {TOKEN_FILE}\n"
                 "  만드는 법: mkdir -p ~/.config/urimal-mcp && chmod 700 ~/.config/urimal-mcp && "
                 "(umask 077; openssl rand -hex 24 > ~/.config/urimal-mcp/token)")
    if st.st_mode & 0o077:
        sys.exit(f"[urimal MCP] 토큰 파일의 권한이 느슨합니다({oct(st.st_mode & 0o777)}): {TOKEN_FILE}  → chmod 600")
    token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    if not token:
        sys.exit(f"[urimal MCP] 토큰 파일이 비어 있습니다: {TOKEN_FILE}")
    return token


def _allowed_hosts():
    extra = [h.strip() for h in os.environ.get("URIMAL_MCP_HOSTS", "").split(",") if h.strip()]
    return ["127.0.0.1:*", "localhost:*", "[::1]:*"] + extra


def _http_security():
    """DNS rebinding 보호는 켠 채 허용 Host 만 정한다."""
    from mcp.server.transport_security import TransportSecuritySettings
    hosts = _allowed_hosts()
    return TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts,
                                     allowed_origins=[f"http://{h}" for h in hosts])


class _BearerAuth:
    """모든 HTTP 요청에 'Authorization: Bearer <토큰>' 을 요구하는 ASGI 래퍼(lifespan 등 다른 scope 는 그대로 통과)"""

    def __init__(self, app, token):
        self.app = app
        self.expected = f"Bearer {token}".encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            got = dict(scope["headers"]).get(b"authorization", b"")
            if not hmac.compare_digest(got, self.expected):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"text/plain; charset=utf-8"), (b"www-authenticate", b"Bearer")]})
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
        await self.app(scope, receive, send)


def _threaded(fn):
    """동기 도구를 스레드에서 실행한다. 조회마다 연결을 새로 열므로 안전하다."""
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)
    return wrapper


def main():
    argv = sys.argv[1:]
    if argv and argv[0] == "--call":
        return _cli_call(argv[1:])
    ap = argparse.ArgumentParser(prog="urimal_mcp.py", description="우리말역사어휘사전 MCP 서버(기본 stdio). 도구 확인은 --call 도구 인자...")
    ap.add_argument("--http", action="store_true", help=f"Streamable HTTP 로 실행({HTTP_HOST} 에만 바인드, 토큰 필수)")
    ap.add_argument("--port", type=int, help=f"--http 의 포트(기본 {HTTP_DEFAULT_PORT})")
    args = ap.parse_args(argv)
    if args.port is not None and not args.http:
        ap.error("--port 는 --http 와 함께 쓰세요.")
    port = args.port or HTTP_DEFAULT_PORT
    try:
        from mcp.server.mcpserver import MCPServer as FastMCP    # mcp 2.x (FastMCP 의 새 이름)
    except ImportError:
        if args.http:
            sys.exit("--http 는 mcp 2.x 가 필요합니다: pip install 'mcp>=2.2,<3'")
        try:
            from mcp.server.fastmcp import FastMCP               # mcp 1.x
        except ImportError:
            sys.exit("MCP SDK가 없습니다. 저장소의 .venv 로 실행하세요: .venv/bin/python urimal_mcp.py")
    global HTTP_MODE
    HTTP_MODE = bool(args.http)
    token = _load_token() if args.http else None               # 토큰 문제는 DB 보다 먼저 알려 준다
    n = _check_db()
    server = FastMCP(SERVER_NAME, instructions=INSTRUCTIONS)
    for fn in TOOLS:
        server.tool(description=inspect.cleandoc(fn.__doc__ or ""))(_threaded(fn))
    info = f"표제어 {n}개"
    if args.http:
        import uvicorn
        print(f"[urimal MCP] {info} / HTTP http://{HTTP_HOST}:{port}/mcp / 인증: 토큰 필요 / "
              f"허용 Host: {', '.join(_allowed_hosts())}", file=sys.stderr)
        app = _BearerAuth(server.streamable_http_app(host=HTTP_HOST, transport_security=_http_security()), token)
        uvicorn.run(app, host=HTTP_HOST, port=port, log_level="warning", timeout_graceful_shutdown=5)
        return
    print(f"[urimal MCP] {info}", file=sys.stderr)
    server.run()      # 기본 transport = stdio


if __name__ == "__main__":
    main()
