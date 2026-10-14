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
python3 urimal_update.py --status      # 마지막 결과·변경 기록·로그
```

이 사전의 API에는 '고친 날짜' 검색이 없어서 변경은 다시 받아 비교합니다.
매일: ① 누리집 전체 내려받기 파일의 생성일이 새로우면(매달 1회 생성) 받아서 가져오기 → ② 내려받기 파일에만 있는 표제어 확인 → ③ 가장 큰 코드 뒤 100개로 새 표제어 감지 → ④ 가장 오래 확인하지 않은 3,000개를 다시 받아 비교(수정·삭제 감지, 약 5일에 한 바퀴) → ⑤ 바뀐 것이 있을 때만 조회용 DB 재빌드(검증 통과 시에만 교체).
하루 API 호출은 20,000건까지(한도 50,000건), 삭제는 두 번 연속 빈 응답일 때만 확정하고 원본은 `raw_removed`에 남깁니다.
로그는 `data/update.log`(15일 지난 줄은 자동 삭제). 새 표제어·삭제 확정이 있으면 macOS 알림을 띄웁니다.
매일 실행은 launchd로 합니다: `com.lexlabs.urimalupdate.plist`(08:45)를 `~/Library/LaunchAgents/`에 복사하고
`launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.lexlabs.urimalupdate.plist`.

## 테스트

조회용 DB를 빌드한 뒤 실행합니다(DB는 저장소에 없습니다).
```
python3 -m unittest tests.test_lookup tests.test_update
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
