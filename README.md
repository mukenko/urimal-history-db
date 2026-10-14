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

1. 내려받기 파일 / API로 원본 수집 → `data/raw.sqlite` (원본 JSON 그대로 보관)
2. 스키마: 표제어, 뜻풀이, 역사 속 표기, 관련어, 세기별 용례, 참고 자료
3. 조회용 DB 빌드(FTS5) → 조회 CLI → (선택) MCP 서버
4. 정기 업데이트(변경 감지)

## 상태

폴더만 생성됨. 아직 코드 없음.

## 데이터 출처

국립국어원 우리말역사어휘사전. 이 저장소에는 코드만 올리고 사전 데이터는 포함하지 않습니다.
