# urimal-history-db

국립국어원 **우리말역사어휘사전**(https://kdict.korean.go.kr/history/) Open API로 만드는 개인용 로컬 사전 DB입니다.
[stdict-db](../stdict-db)와 같은 구조(원본 수집 → 조회용 DB 빌드 → 조회/MCP)를 따릅니다.

## 사전 규모 (시범 운영 중, 통계 페이지 기준)

- 표제어 15,034개 (단어 15,013 · 구 21)
- 역사 속 표기 49,106개, 용례 142,924개
- 전체 사전은 누리집에서 엑셀·XML·JSON으로 내려받을 수 있음 → 수집 전에 먼저 확인

## Open API

| 구분 | URL |
|---|---|
| 검색 | `https://kdict.korean.go.kr/history/api/search` (q, start 1~1000, num 10~100, target 1~9, method …) |
| 내용 | `https://kdict.korean.go.kr/history/api/view` (`method=word_info` 또는 `target_code`) |

- 인증키는 사용자당 1개, **하루 50,000건** 제한. 스크립트는 상한 전에 멈추도록 가드를 둡니다.
- 키는 `.env`의 `URIMAL_HISTORY_API_KEY`에만 두고 저장소에 올리지 않습니다.
- 검색 API는 start가 최대 1000이라 검색어당 1,000건까지만 → 전수 수집은 `target_code` 또는 내려받기 파일로.

## 계획

1. (완료) API로 원본 수집 → `data/raw.sqlite` (원본 JSON 그대로 보관)
2. 스키마: 표제어, 뜻풀이, 역사 속 표기, 관련어, 세기별 용례, 참고 자료
3. 조회용 DB 빌드(FTS5) → 조회 CLI → (선택) MCP 서버
4. (완료) 정기 업데이트

## 상태

수집기 `urimal_fetch.py` 완성(원본 XML을 `data/raw.sqlite`에 저장, 이어받기, 하루 45,000건 가드). `urimal_build.py`로 조회용 DB(`data/urimal.sqlite`)를 만듭니다. `urimal_lookup.py`로 조회하고 `urimal_mcp.py`로 MCP 서버를 띄웁니다.

```
python3 urimal_fetch.py fetch --range 1 20000
python3 urimal_fetch.py status
python3 urimal_build.py
python3 urimal_lookup.py 고드름           # 표제어 조회 (--by hist|def|prefix, --code, --examples)
python3 urimal_lookup.py 고래 --examples all --html   # 글꼴을 지정한 HTML 로 만들어 브라우저로 열기(--no-open 은 파일만)
```

## 데이터 출처

국립국어원 우리말역사어휘사전. 이 저장소에는 코드만 올리고 사전 데이터는 포함하지 않습니다.

## MCP 서버

LLM이 기억이 아니라 사전을 보고 낱말의 옛 표기·어원·용례를 확인하게 하는 읽기 전용 MCP 서버입니다.
도구 2개: `lookup_word`(동형이의어 전부, 어휘 역사·설명·용례·참고 자료. 옛 표기로 찾아도 표제어를 보여 줌)와
`search_dictionary`(`historical_form` 옛 표기 / `definition` 뜻풀이 / `prefix` 앞부분으로 역검색).

```
python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt   # mcp 2.x 는 파이썬 3.10 이상
python3 urimal_mcp.py --call lookup_word 고드름        # SDK 없이 도구 결과 확인
```

- **stdio**(LM Studio, Claude 데스크톱): `{"mcpServers": {"urimal-history": {"command": "/경로/urimal-history-db/.venv/bin/python", "args": ["/경로/urimal-history-db/urimal_mcp.py"]}}}`
- **Streamable HTTP**: `.venv/bin/python urimal_mcp.py --http --port 8767`. 127.0.0.1에만 바인드하고 Bearer 토큰이 필수입니다
  (`~/.config/urimal-mcp/token`, 권한 600; 만드는 법은 서버가 알려 줌). 외부 노출은 프록시가 맡고, 접속에 쓰는 이름은 `URIMAL_MCP_HOSTS`.

## 정기 업데이트

```
python3 urimal_update.py --dry-run     # 할 일만 보기
python3 urimal_update.py               # 오늘 할 일 전체
python3 urimal_update.py --status      # 마지막 실행 결과(ok/incomplete/failed)·변경 기록·로그
```

이 사전의 API에는 '고친 날짜' 검색이 없어서 변경은 다시 받아 비교합니다.
매일: ① 누리집 전체 내려받기 파일의 생성일이 새로우면(매달 1회 생성) 받아서 가져오기 → ② 내려받기 파일에만 있는 표제어 확인 → ③ 가장 큰 코드 뒤 100개로 새 표제어 감지 → ④ 전날 빈 응답이 와 삭제 확인을 기다리는 코드 다시 확인 → ⑤ 가장 오래 확인하지 않은 3,000개를 다시 받아 비교(수정·삭제 감지, 약 5일에 한 바퀴) → ⑥ 바뀐 것이 있을 때만 조회용 DB 재빌드(검증 통과 시에만 교체).
- **동형이의어 번호 보호:** 새 표제어가 생기거나 항목의 표제어·어깨번호가 바뀌면, 같은 표제어의 다른 항목(최대 10개 안팎)을 같은 실행에서 바로 다시 받습니다. 사전이 번호를 밀면 옛 번호의 항목이 며칠 남아 빌드 검증("(표제어, 어깨번호) 중복")이 실패하는 것을 막기 위해서입니다. 그래도 중복으로 빌드가 실패하면 빌드가 알려 준 중복 코드만 다시 받아 **한 번만** 다시 빌드합니다.
- **삭제는 이틀에 걸쳐 확정:** 이전에 있던 코드가 비어 오면 같은 날에는 `fetch_state.note`에 `empty@날짜`로 표시만 하고, **다른 날** 또 비어 오면 삭제로 확정합니다(일시적인 빈 응답이 같은 순간에 되풀이될 수 있어서). 원본은 `raw_removed`에 남깁니다.
하루 API 호출은 20,000건까지(한도 50,000건)입니다.
로그는 `data/update.log`(15일 지난 줄은 자동 삭제). 새 표제어·삭제 확정이 있으면, 실패(빌드 실패)하면 macOS 알림을 띄웁니다.
마지막 실행의 시작·종료 시각, 결과(`ok`/`incomplete`/`failed`/`interrupted`), 요약은 원본 DB의 `sync_meta`에 남고 `--status` 맨 위에 보입니다(stdict-db와 같은 형태). 실패하면 종료 코드 1, 중단하면 130입니다.
매일 실행은 launchd로 합니다: `com.lexlabs.urimalupdate.plist`(08:45)를 `~/Library/LaunchAgents/`에 복사하고
`launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.lexlabs.urimalupdate.plist`.

## 테스트

조회용 DB를 빌드한 뒤 실행합니다(DB는 저장소에 없습니다).
```
python3 -m unittest tests.test_lookup tests.test_update tests.test_page
.venv/bin/python -m unittest tests.test_mcp_stdio
```

## 데이터 출처와 병합

- **Open API(`urimal_fetch.py`)**: 기본 원본. 다만 어휘 역사 설명·부가 정보·기타 정보를 256자에서 자르고, 표제어 8개는 내주지 않습니다.
- **누리집 전체 내려받기(`urimal_import.py`)**: 검색 결과 화면의 '내려받기' → '전체 사전 내려받기'(XML, 매달 1회 생성). 전문이 들어 있고 표제어도 15,034개 전부지만, 큰따옴표(“ ”)가 빠지고 줄바꿈이 없으며 용례 강조(`<strong>`)가 없습니다.
- **빌드(`urimal_build.py`)**: API를 기본으로 하고, 잘린 설명의 뒷부분과 API에 없는 표제어 8개만 내려받기 파일로 채웁니다. `entries.source`(`api`/`download`)와 `entries.merged_fields`로 어디서 왔는지 남깁니다. 이어 붙인 뒷부분에는 큰따옴표와 줄바꿈이 없습니다. API는 문단(`##`)마다 256자에서 자르므로 문단별로 맞춰 이어 붙이며, 옛한글 자모 표기 차이(ㆍ/ᆞ)와 출전 꺾쇠 글(`<…>`) 유무를 보정합니다. 그래도 맞지 않는 23개 필드는 잘린 채 남습니다.

```
python3 urimal_fetch.py fetch --range 1 20000
python3 urimal_import.py data/download/full_20261001.zip
python3 urimal_build.py
```

## 옛한글·한자가 깨져 보일 때

용례의 옛한글은 표준 유니코드 조합 자모(ᄒ+ᆞ, ᄯ, ᇰ …)라 데이터는 정상이지만, 채팅 앱(Claude 앱 등)은 시스템 글꼴 폴백으로 그려서 빈 칸(▒)이나 갈라진 글자로 보이고
한자도 다른 지역 자형으로 보일 수 있습니다. 정확히 보려면 `--html`(위)로 글꼴이 지정된 HTML을 만들어 여세요. `lang="ko"`와 **나눔바른고딕 옛한글판**을 지정해서 누리집처럼 나옵니다. 글꼴 파일(`~/Library/Fonts/NanumBarunGothic-YetHangul.otf`)이 있으면 HTML 안에 웹 글꼴로
**직접 넣습니다**(약 4MB; Safari는 웹 페이지가 사용자가 설치한 글꼴을 쓰지 못하게 막아 설치만으로는 옛한글이 깨집니다, Chrome은 설치된 글꼴을 그대로 씀). 파일이 없으면 설치된
글꼴 이름(바른고딕 → 나눔명조 옛한글판)으로 예비 처리합니다. `URIMAL_HTML_EMBED_FONT=0` 이면 내장하지 않습니다.
파일은 `~/.cache/urimal-db/html/`(환경변수 `URIMAL_HTML_DIR`)에 검색어별로 하나씩 만들어지고 하루 지난 것은 자동으로 지웁니다.

**문서형 화면(`urimal_page.py`):** `--html`·결과 링크는 결과 글자를 한 덩어리로 넣지 않고 DB에서 항목별로 읽어 사전 항목처럼 그립니다.
어휘 역사는 표기 칸을 화살표로 이어 세기를 작게 보이고(`15~17세기 · 미상`), 용례의 표제어는 강조하며, 긴 설명은 문단(`##`)과 `[예]`·`[현대어 역]` 줄로 나눕니다.
용례는 세기별로 접었다 펼 수 있고 "모두 펼치기" 버튼이 있습니다(용례가 8개 이하면 처음부터 펼침). 채팅에 나온 결과가 줄여져 있어도 링크·`--html` 화면에는 용례가 모두 들어 있고, 어두운 화면과 인쇄도 맞췄습니다.
같은 표제어가 둘 이상(동형이의어)이면 맨 위에 바로가기 목차가 생깁니다.
**눌러서 이동:** 채팅 결과 끝의 링크로 열린 화면(로컬 링크 서버가 보냄)에서는 관련어를 누르면 그 낱말의 화면으로, 어휘 역사의 옛 표기(와 '그 밖의 표기')를 누르면 그 표기를 쓴 다른 표제어 화면으로 갑니다.
없는 곳으로 가는 링크는 만들지 않습니다(관련어는 표제어일 때만, 옛 표기는 다른 표제어에도 쓰였을 때만). 파일로 저장하는 `--html` 화면에는 링크 서버가 없어 링크가 없습니다.

**항상 붙는 보기 링크와 원본 데이터:** 조회해서 표제어를 찾으면 결과 끝에 `정확한 글자로 보기: …` 한 줄이 **항상** 붙습니다(옛한글이 없어도. 못 찾으면 없음). 모델이 이 링크를 답변 끝에 적고, 누르면 글꼴이 정확한 문서형 화면이 열립니다.
화면의 항목마다 **"원본 데이터 ↗"** 버튼이 있어서 누르면 이 맥의 `raw.sqlite`에 저장된 **원본 그대로**(Open API 응답 XML과 내려받기 파일의 같은 항목)와, 설명 글이 API에서 어디까지 잘렸는지 보여 주는 글자 수 비교표가 나옵니다(조회용 DB가 무엇을 읽어 만들었는지 대조용).
"누리집 ↗" 버튼은 국립국어원 누리집에서 그 항목 하나를 여는 주소입니다(`searchResultDetail?…&etymNo=<target_code>`). 링크 서버가 이 맥에서만 열리므로 원격(`--http`, 파이) MCP에는 링크가 없고, 파이에는 `raw.sqlite`도 없습니다(동기화 제외).
**자료 출처 안내:** 내려받기 파일로 뒷부분을 이어 붙인 설명에는 "큰따옴표와 줄바꿈이 없다", 이어 붙이지 못해 중간에서 잘린 설명(`entries.truncated_fields`, 약 23개)에는 "잘려 있다, 전문은 누리집 원본에서", Open API가 주지 않아 내려받기 파일에서만 온 표제어 8개에는 그 사실을 작게 알립니다.

**링크로 열기:** 찾은 표제어의 조회 결과에는(옛한글이 없어도) `정확한 글자로 보기: http://127.0.0.1:포트/v/…` 한 줄이 붙고, 모델이 그 링크를 답변 끝에 적습니다. 누르면 그때 글꼴이 지정된 HTML이
열립니다(조회마다 파일을 만들지 않습니다: MCP 서버가 `127.0.0.1`에만 여는 작은 웹 서버를 띄워 클릭할 때 HTML을 만들고, 바른고딕은 `/f/font.otf`로 한 번만 보냅니다).
링크는 **그 MCP 서버가 살아 있는 동안만** 열립니다(Claude 앱을 종료하면 이전 대화의 링크는 죽으니 다시 조회하거나 "열어 줘"라고 하세요). 이 컴퓨터의 로컬 서버에서만 만들어지고
원격(`--http`) 서버는 링크를 붙이지 않습니다. `URIMAL_VIEW_LINK=0` 이면 링크를 붙이지 않습니다(`옛한글 포함` 표시는 그대로).
