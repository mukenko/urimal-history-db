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

    def test_footer_link_rules(self):
        """찾았고 로컬 서버일 때만 '원본 보기' 링크를 붙인다(옛한글 여부와 상관없이). 링크는 지시가 아니라 URL 만 담은 데이터다."""
        import urimal_mcp as um

        class Fake:
            def add_query(self, label, **spec):
                self.last = (label, spec)
                return "http://127.0.0.1:1/v/" + "a" * 32
        fake = Fake()
        old = um.view.get_server
        um.view.get_server = lambda: fake
        self.addCleanup(setattr, um.view, "get_server", old)
        old_mode = um.HTTP_MODE
        self.addCleanup(setattr, um, "HTTP_MODE", old_mode)
        spec = {"by": "form", "q": "고래", "limit": 30, "max_entries": 8}
        link = "원본 보기: http://127.0.0.1:1/v/" + "a" * 32
        lines = um._with_footer("결과 글", "고래", spec).split("\n")
        self.assertEqual(lines[0], "결과 글")
        self.assertEqual(lines[1], link)
        self.assertTrue(lines[2].startswith("출처:"))
        self.assertEqual(fake.last, ("고래", spec))                                    # 클릭할 때 다시 그릴 검색 조건을 넘긴다
        self.assertNotIn("원본 보기", um._with_footer("결과 글", "고래", spec, found=False))   # 못 찾았으면 링크 없음
        self.assertNotIn("원본 보기", um._with_footer("결과 글", "고래"))                      # 검색 조건이 없으면 링크 없음
        um.HTTP_MODE = True                                                          # 원격 서버: 링크 없음
        self.assertNotIn("원본 보기", um._with_footer("결과 글", "고래", spec))
        um.HTTP_MODE = False
        os.environ["URIMAL_VIEW_LINK"] = "0"
        self.addCleanup(os.environ.pop, "URIMAL_VIEW_LINK", None)
        self.assertNotIn("원본 보기", um._with_footer("결과 글", "고래", spec))
        os.environ.pop("URIMAL_VIEW_LINK")
        um.view.get_server = lambda: (_ for _ in ()).throw(OSError("bind failed"))   # 서버를 못 띄워도 조회는 된다
        failed = um._with_footer("결과 글", "고래", spec)
        self.assertNotIn("원본 보기", failed)
        self.assertTrue(failed.startswith("결과 글"))

    async def test_result_link_opens_the_page(self):
        import html as htmllib
        import re
        import urllib.request
        async with session(env={"URIMAL_MCP_NOTICE": "0"}) as (s, init):
            out = text(await s.call_tool("lookup_word", {"word": "나무"}))
            lines = out.rstrip().splitlines()
            self.assertTrue(lines[-1].startswith("출처:"))
            self.assertRegex(lines[-2], r"^원본 보기: http://127\.0\.0\.1:\d+/v/[0-9a-f]{32}$")
            self.assertNotIn("옛한글 포함", out)                                        # 깨질 수 있다는 안내 줄은 두지 않는다
            url = lines[-2].split(": ", 1)[1]
            page = urllib.request.urlopen(url, timeout=5).read().decode("utf-8")
            self.assertIn('lang="ko"', page)
            sample = next(l.strip().split("  <")[0] for l in lines if any(0x1100 <= ord(c) <= 0x11FF for c in l) and "[" not in l[:6])
            sample = sample.lstrip("· ")                                              # 텍스트 결과의 글머리 기호는 HTML 에 없다
            self.assertIn(sample, htmllib.unescape(re.sub(r"<[^>]+>", "", page)))      # 결과의 옛한글 줄이 그대로 들어 있다(강조 태그만 뺌)
            self.assertNotIn("원본 데이터 ↗", page)                                    # 원본 데이터 버튼은 없다
            self.assertIn("누리집 ↗", page)                                            # 누리집 버튼은 있다
            # 규칙은 서버 지침과 도구 설명에 있다(링크가 있으면 묻지 않고, 이유를 설명하지 않고 답변 끝에 적는다)
            ins = init.instructions
            self.assertIn("원본 보기", ins)
            self.assertIn("without asking and without explaining why", ins)
            self.assertNotIn("may display wrongly", ins)                              # 깨질 수 있다는 설명을 모델이 따라 말하지 않게
            tools = {t.name: t for t in (await s.list_tools()).tools}
            for name in ("lookup_word", "search_dictionary"):
                d = " ".join(tools[name].description.split())                     # 설명의 줄바꿈은 무시한다
                self.assertIn("원본 보기", d, name)
                self.assertIn("without asking and without explaining why", d, name)

    async def test_link_always_present_for_found_entries(self):
        """옛한글이 없어도 찾았으면 항상 '원본 보기' 링크가 붙는다. 못 찾았으면 없다."""
        async with session(env={"URIMAL_MCP_NOTICE": "0"}) as (s, init):
            for call, args in (("lookup_word", {"word": "가-건물"}),
                               ("search_dictionary", {"query": "고드", "by": "prefix", "limit": 5})):
                out = text(await s.call_tool(call, args))
                self.assertRegex(out, r"\n원본 보기: http://127\.0\.0\.1:\d+/v/[0-9a-f]{32}\n출처:", call)
            miss = text(await s.call_tool("lookup_word", {"word": "쀏쀏쀏쀏"}))
            self.assertNotIn("원본 보기", miss)

    async def test_link_can_be_switched_off(self):
        async with session(env={"URIMAL_MCP_NOTICE": "0", "URIMAL_VIEW_LINK": "0"}) as (s, _):
            out = text(await s.call_tool("lookup_word", {"word": "나무"}))
            self.assertNotIn("원본 보기", out)
            self.assertTrue(out.rstrip().splitlines()[-1].startswith("출처:"))

    async def test_no_browser_tool_and_no_offer_flow(self):
        """링크가 항상 붙으므로 브라우저를 여는 도구와 '열어 드릴까요' 제안 절차는 없다."""
        async with session() as (s, init):
            self.assertNotIn("open_in_browser", init.instructions)
            self.assertNotIn("offering to open", init.instructions)         # (탐색 제안의 "offer"는 다른 규칙이라 남아 있다)
            tools = {t.name for t in (await s.list_tools()).tools}
            self.assertEqual(tools, {"lookup_word", "search_dictionary"})

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
