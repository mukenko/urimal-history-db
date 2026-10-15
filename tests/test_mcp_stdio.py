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
            notice = text(await s.call_tool("lookup_word", {"word": "고드름", "examples_per_century": 0})).split("\n")[-1]
            for needle in ("(추론)", "[사전 외 설명]"):
                self.assertIn(needle, notice, needle)

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
                self.assertIn("(추론)", init.instructions)
        async with session(env={"URIMAL_MCP_NOTICE": "1"}) as (s, _):
            out = text(await s.call_tool("lookup_word", {"word": "고드름", "examples_per_century": 0}))
            self.assertIn("Note to assistant", out)

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
