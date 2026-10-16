"""urimal_mcp.py(stdio) 테스트. 실제 data/urimal.sqlite 를 읽기 전용으로 쓴다(DB는 저장소에 없다).

실행(저장소 루트): .venv/bin/python -m unittest tests.test_mcp_stdio -v
"""
import contextlib
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import urimal_lookup as ul  # noqa: E402

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    HAVE_SDK = True
except ImportError:
    HAVE_SDK = False

needs = unittest.skipUnless(HAVE_SDK and ul.DB_PATH.exists(), "mcp SDK 또는 조회용 DB가 없습니다")
SERVER = str(ROOT / "urimal_mcp.py")


def text(res):
    return "".join(c.text for c in res.content if getattr(c, "type", "") == "text")


@contextlib.asynccontextmanager
async def session(env=None):
    params = StdioServerParameters(command=sys.executable, args=[SERVER], env={**os.environ, **(env or {})})
    with open(os.devnull, "w") as err:
        async with stdio_client(params, errlog=err) as (r, w):
            async with ClientSession(r, w) as s:
                init = await s.initialize()
                yield s, init


@needs
class Protocol(unittest.IsolatedAsyncioTestCase):
    async def test_info_and_tools(self):
        async with session() as (s, init):
            self.assertEqual(init.server_info.name, "urimal-history")
            self.assertIn("Old Hangul", init.instructions)
            self.assertIn("[사전 외 설명]", init.instructions)         # 사전 밖 설명은 표시하면 덧붙일 수 있다
            self.assertIn("never invent", init.instructions.lower())
            tools = {t.name: t for t in (await s.list_tools()).tools}
            self.assertEqual(set(tools), {"lookup_word", "search_dictionary"})
            by = tools["search_dictionary"].input_schema["properties"]["by"]
            self.assertEqual(set(by["enum"]), {"historical_form", "definition", "prefix"})

    async def test_three_tiers_and_proportional_research_guidance(self):
        async with session() as (s, init):
            ins = init.instructions
            for needle in ("(추론)", "[사전 외 설명]", "never invent", "bounded", "3 to 4", "no extra lookups",
                           "offer", "do not start that search yet", "stop"):
                self.assertIn(needle, ins, needle)
            self.assertNotIn("without asking permission", ins)               # 먼저 제안하고 동의하면 탐색한다(2026-10-15)
            tools = {t.name: t for t in (await s.list_tools()).tools}
            self.assertIn("key phrases", tools["search_dictionary"].description)
            for name in ("lookup_word", "search_dictionary"):                       # 도구 설명에도 표시 규칙과 전형적인 예
                d = tools[name].description
                self.assertIn("[사전 외 설명]", d, name)
                self.assertIn("source line", d, name)
            self.assertIn("why a letter or sound changed", tools["lookup_word"].description)
            notice = text(await s.call_tool("lookup_word", {"word": "고드름", "examples_per_century": 0})).split("\n")[-1]
            for needle in ("(추론)", "[사전 외 설명]"):
                self.assertIn(needle, notice, needle)

    def test_old_hangul_count_ignores_modern_text(self):
        """모델이 '옛한글이 들어 있는지'를 스스로 판단하기 어려워 서버가 사실로 알려 준다(2026-10-16, 나무 시험에서 제안이 안 나옴)."""
        import unicodedata
        import urimal_mcp as um
        self.assertEqual(um._old_hangul_count("현대 한글과 漢字 고래 [단어]"), 0)
        self.assertEqual(um._old_hangul_count(unicodedata.normalize("NFD", "한글 고래")), 0)   # 분해된 현대 한글은 옛한글이 아니다
        self.assertGreater(um._old_hangul_count("브ᅀᅥᆨ 구듨 골샛 거믜여ᇰ."), 0)
        self.assertGreater(um._old_hangul_count("ᄂᆞ모"), 0)

    async def test_result_says_when_it_contains_old_hangul(self):
        async with session(env={"URIMAL_MCP_NOTICE": "0"}) as (s, init):
            out = text(await s.call_tool("lookup_word", {"word": "나무"}))
            lines = out.rstrip().splitlines()
            note = [l for l in lines if l.startswith("옛한글 포함:")]
            self.assertEqual(len(note), 1, note)
            self.assertRegex(note[0], r"조합 자모 \d+자")
            self.assertEqual(lines[-1], "출처: 국립국어원 우리말역사어휘사전 (CC BY-SA 2.0 KR)")   # 출처 줄이 마지막
            self.assertLess(lines.index(note[0]), len(lines) - 1)
            # 표시는 사실이고 지시가 아니다(도구 결과 안의 지시는 Claude 가 주입으로 본다)
            for forbidden in ("open_in_browser", "offer", "call ", "--html"):
                self.assertNotIn(forbidden, note[0])
            # 제안 규칙은 서버 지침과 도구 설명에 있다
            ins = init.instructions
            self.assertIn("옛한글 포함", ins)
            tools = {t.name: t for t in (await s.list_tools()).tools}
            for name in ("lookup_word", "search_dictionary"):
                d = tools[name].description
                self.assertIn("옛한글 포함", d, name)
                self.assertIn("urimal_lookup.py <word> --html", d, name)     # 원격 서버일 때의 안내
                self.assertNotIn("open_in_browser", d, name)

    OLD = "\u1107\u1173\u1140\u1165\u11A8 \uAD6C\uB4E8 \uACE8\uC0D8 \uAC70\uBBDC\uC5EC\u11F0."

    def test_footer_link_rules(self):
        """옛한글이 있고 로컬 서버일 때만 '정확한 글자로 보기' 링크를 붙인다. 링크는 지시가 아니라 URL 만 담은 데이터다."""
        import urimal_mcp as um

        class Fake:
            def add(self, label, text):
                return "http://127.0.0.1:1/v/" + "a" * 32
        old = um.view.get_server
        um.view.get_server = lambda: Fake()
        self.addCleanup(setattr, um.view, "get_server", old)
        old_mode = um.HTTP_MODE
        self.addCleanup(setattr, um, "HTTP_MODE", old_mode)
        link = "정확한 글자로 보기: http://127.0.0.1:1/v/" + "a" * 32
        lines = um._with_footer(self.OLD, "고래").split("\n")
        self.assertEqual(lines[0], self.OLD)
        self.assertTrue(lines[1].startswith("옛한글 포함:"))
        self.assertEqual(lines[2], link)
        self.assertTrue(lines[3].startswith("출처:"))
        modern = um._with_footer("현대 한글 고래", "고래")                          # 옛한글 없음: 표시도 링크도 없다
        self.assertNotIn("옛한글 포함", modern)
        self.assertNotIn("정확한 글자로 보기", modern)
        um.HTTP_MODE = True                                                        # 원격 서버: 표시는 있고 링크는 없다
        remote = um._with_footer(self.OLD, "고래")
        self.assertIn("옛한글 포함", remote)
        self.assertNotIn("정확한 글자로 보기", remote)
        um.HTTP_MODE = False
        os.environ["URIMAL_VIEW_LINK"] = "0"
        self.addCleanup(os.environ.pop, "URIMAL_VIEW_LINK", None)
        self.assertNotIn("정확한 글자로 보기", um._with_footer(self.OLD, "고래"))
        os.environ.pop("URIMAL_VIEW_LINK")
        um.view.get_server = lambda: (_ for _ in ()).throw(OSError("bind failed"))   # 서버를 못 띄워도 조회는 된다
        failed = um._with_footer(self.OLD, "고래")
        self.assertIn("옛한글 포함", failed)
        self.assertNotIn("정확한 글자로 보기", failed)

    async def test_result_link_opens_the_exact_page(self):
        import html as htmllib
        import urllib.request
        async with session(env={"URIMAL_MCP_NOTICE": "0"}) as (s, init):
            out = text(await s.call_tool("lookup_word", {"word": "나무"}))
            lines = out.rstrip().splitlines()
            self.assertTrue(lines[-1].startswith("출처:"))
            self.assertRegex(lines[-2], r"^정확한 글자로 보기: http://127\.0\.0\.1:\d+/v/[0-9a-f]{32}$")
            self.assertTrue(lines[-3].startswith("옛한글 포함:"))
            url = lines[-2].split(": ", 1)[1]
            page = urllib.request.urlopen(url, timeout=5).read().decode("utf-8")
            self.assertIn('lang="ko"', page)
            sample = next(l.strip().split("  <")[0] for l in lines if any(0x1100 <= ord(c) <= 0x11FF for c in l) and "[" not in l[:6])
            sample = sample.lstrip("· ")                                          # 텍스트 결과의 글머리 기호는 HTML 에 없다
            import re
            self.assertIn(sample, htmllib.unescape(re.sub(r"<[^>]+>", "", page)))  # 결과의 옛한글 줄이 그대로 들어 있다(강조 태그만 뺌)
            # 규칙은 서버 지침과 도구 설명에 있다(링크가 있으면 묻지 않고 답변 끝에 적는다)
            self.assertIn("정확한 글자로 보기", init.instructions)
            self.assertIn("without asking", init.instructions)
            tools = {t.name: t for t in (await s.list_tools()).tools}
            for name in ("lookup_word", "search_dictionary"):
                self.assertIn("정확한 글자로 보기", tools[name].description, name)

    async def test_link_always_present_for_found_entries(self):
        """옛한글이 없어도 찾았으면 항상 보기 링크가 붙는다(원본 데이터 버튼이 있는 화면). 못 찾았으면 없다."""
        async with session(env={"URIMAL_MCP_NOTICE": "0"}) as (s, init):
            for call, args in (("lookup_word", {"word": "가-건물"}),
                               ("search_dictionary", {"query": "고드", "by": "prefix", "limit": 5})):
                out = text(await s.call_tool(call, args))
                self.assertNotIn("옛한글 포함", out)
                self.assertRegex(out, r"\n정확한 글자로 보기: http://127\.0\.0\.1:\d+/v/[0-9a-f]{32}\n출처:", call)
            miss = text(await s.call_tool("lookup_word", {"word": "쀏쀏쀏쀏"}))
            self.assertNotIn("정확한 글자로 보기", miss)
            self.assertNotIn("원본 사전에서 보기", out)                                   # 누리집 줄은 두지 않는다
            self.assertIn("a button for the stored original data", init.instructions)

    async def test_link_can_be_switched_off(self):
        async with session(env={"URIMAL_MCP_NOTICE": "0", "URIMAL_VIEW_LINK": "0"}) as (s, _):
            out = text(await s.call_tool("lookup_word", {"word": "나무"}))
            self.assertIn("옛한글 포함:", out)
            self.assertNotIn("정확한 글자로 보기", out)
            self.assertTrue(out.rstrip().splitlines()[-1].startswith("출처:"))

    async def test_open_in_browser_is_gone_and_remote_hint_replaces_offer(self):
        """링크가 항상 붙으므로 브라우저를 여는 도구와 '열어 드릴까요' 제안 절차는 없다. 링크가 없는 원격 서버일 때만 --html 안내."""
        async with session() as (s, init):
            self.assertNotIn("open_in_browser", init.instructions)
            self.assertNotIn("offering to open", init.instructions)         # (탐색 제안의 "offer"는 다른 규칙이라 남아 있다)
            self.assertIn("urimal_lookup.py <word> --html", init.instructions)
            self.assertIn("Do not retype Old Hangul", init.instructions)
            tools = {t.name for t in (await s.list_tools()).tools}
            self.assertNotIn("open_in_browser", tools)

    async def test_notice_has_no_process_directives(self):
        """도구 결과 안의 행동 지시(호출 횟수 등)는 Claude 가 주입으로 보고 따르지 않는다. 탐색 기준은 서버 지침·도구 설명에만."""
        async with session() as (s, _):
            notice = text(await s.call_tool("lookup_word", {"word": "고드름", "examples_per_century": 0})).split("\n")[-1]
            self.assertTrue(notice.startswith("[Note to assistant"))
            for forbidden in ("extra lookups", "3-4", "extra calls", "proportionate", "stop once", "search wider"):
                self.assertNotIn(forbidden, notice, forbidden)

    async def test_notice_can_be_switched_off(self):
        for value in ("0", "off", "false"):
            async with session(env={"URIMAL_MCP_NOTICE": value}) as (s, init):
                out = text(await s.call_tool("lookup_word", {"word": "고드름", "examples_per_century": 0}))
                self.assertNotIn("Note to assistant", out, value)
                self.assertIn("고드름", out)
                self.assertEqual(out.rstrip().splitlines()[-1], "출처: 국립국어원 우리말역사어휘사전 (CC BY-SA 2.0 KR)")
                self.assertIn("(추론)", init.instructions)
        async with session(env={"URIMAL_MCP_NOTICE": "1"}) as (s, _):
            out = text(await s.call_tool("lookup_word", {"word": "고드름", "examples_per_century": 0}))
            self.assertIn("Note to assistant", out)
            self.assertEqual(out.splitlines()[-2], "출처: 국립국어원 우리말역사어휘사전 (CC BY-SA 2.0 KR)")   # 알림 바로 앞

    async def test_lookup_word(self):
        async with session() as (s, _):
            out = text(await s.call_tool("lookup_word", {"word": "고드름", "examples_per_century": 0}))
            self.assertIn("고드름", out)
            self.assertIn("곳어름", out)
            self.assertIn("Note to assistant", out)
            self.assertIn("[사전 외 설명]", out.split("\n")[-1])
            self.assertNotIn("answer only from the entries", out)
            self.assertNotIn("[용례]", out)

    async def test_old_form_and_miss(self):
        async with session() as (s, _):
            out = text(await s.call_tool("lookup_word", {"word": "곳어름", "examples_per_century": 0}))
            self.assertIn("역사 속 표기로 나옵니다", out)
            out = text(await s.call_tool("lookup_word", {"word": "없는말쓰"}))
            self.assertIn("찾지 못했습니다", out)

    async def test_search(self):
        async with session() as (s, _):
            out = text(await s.call_tool("search_dictionary", {"query": "고드", "by": "prefix"}))
            self.assertIn("고드름", out)
            out = text(await s.call_tool("search_dictionary", {"query": "곳어름", "by": "historical_form"}))
            self.assertIn("고드름", out)


if __name__ == "__main__":
    unittest.main()
