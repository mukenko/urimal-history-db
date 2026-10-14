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
4. 정기 업데이트(변경 감지)

## 상태

수집기 `urimal_fetch.py` 완성(원본 XML을 `data/raw.sqlite`에 저장, 이어받기, 하루 45,000건 가드). `urimal_build.py`로 조회용 DB(`data/urimal.sqlite`)를 만듭니다. `urimal_lookup.py`로 조회합니다.

```
python3 urimal_fetch.py fetch --range 1 20000
python3 urimal_fetch.py status
python3 urimal_build.py
python3 urimal_lookup.py 고드름           # 표제어 조회 (--by hist|def|prefix, --code, --examples)
```

## 데이터 출처

국립국어원 우리말역사어휘사전. 이 저장소에는 코드만 올리고 사전 데이터는 포함하지 않습니다.

## 데이터 출처와 병합

- **Open API(`urimal_fetch.py`)**: 기본 원본. 다만 어휘 역사 설명·부가 정보·기타 정보를 256자에서 자르고, 표제어 8개는 내주지 않습니다.
- **누리집 전체 내려받기(`urimal_import.py`)**: 검색 결과 화면의 '내려받기' → '전체 사전 내려받기'(XML, 매달 1회 생성). 전문이 들어 있고 표제어도 15,034개 전부지만, 큰따옴표(“ ”)가 빠지고 줄바꿈이 없으며 용례 강조(`<strong>`)가 없습니다.
- **빌드(`urimal_build.py`)**: API를 기본으로 하고, 잘린 설명의 뒷부분과 API에 없는 표제어 8개만 내려받기 파일로 채웁니다. `entries.source`(`api`/`download`)와 `entries.merged_fields`로 어디서 왔는지 남깁니다. 이어 붙인 뒷부분에는 큰따옴표와 줄바꿈이 없습니다.

```
python3 urimal_fetch.py fetch --range 1 20000
python3 urimal_import.py data/download/full_20261001.zip
python3 urimal_build.py
```
