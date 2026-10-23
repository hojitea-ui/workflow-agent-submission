# 워크플로 에이전트 제출 — 카드뉴스 + 확장(보고서·과제 검수)

두 개의 앱이 들어 있습니다. 각 폴더 안의 `README.md`에 실행 방법이 있습니다(둘 다 Windows, Python, 로그인된 Claude CLI 필요).

| 폴더 | 내용 | 먼저 볼 파일 |
|---|---|---|
| `1_cardnews/` | **카드뉴스 에이전트**(실습 본체). 주제 → 후보 조사 → 선택 → 스토리보드 → 카드 PNG·ZIP | `README.md`, `PRD.md`, `ACCEPTANCE.md`, `VALIDATION.md`, `results/` |
| `2_report-review/` | **확장: 보고서·과제 글 검수 에이전트**. 글 입력 → 구조·논리·근거 지적 → 방향 선택 → 자료 검색·수정 → 재검수 → `final.md`·`review_report.md` | `README.md`, `PRD.md`, `VALIDATION.md`, `evidence/` |

## 완료 범위와 확장 범위
- **카드뉴스(완료 범위)**: 실제 실행 확인 결과는 `1_cardnews/ACCEPTANCE.md`(통과 10 · 일부 통과 2 · 실패 1 · 미확인 2)에 있습니다. 결과 PNG·ZIP은 `1_cardnews/results/` 두 폴더(`claude-duet_story`, `deepinfer_list`)입니다.
- **확장(검수 에이전트)**: 가장 작은 범위로 만들었고, 정상 입력 한 번과 자료 부족 입력 한 번을 실제 Claude로 돌렸습니다. 결과 파일과 화면은 `2_report-review/evidence/`, 기록은 `2_report-review/VALIDATION.md`입니다. 확장 앱은 카드뉴스 앱을 복사해 뼈대(서버·DB·Claude CLI 연결·진행 상태)를 재사용하고 카드·이미지 부분을 뺐습니다.

## 제출에서 뺀 것
앱이 결과를 저장하는 `data/`(내 PC 경로와 실행 기록), 가상환경 `.venv/`, 캐시는 포함하지 않았습니다. 카드뉴스 결과는 `data/`에서 내보낸 PNG·ZIP만 `results/`에 복사했습니다.

## 읽는 순서 (10분)
1. 이 파일 → 완료 범위와 확장 범위를 먼저 봅니다.
2. `1_cardnews/ACCEPTANCE.md` (카드뉴스 15항목 판정) → `1_cardnews/results/` (결과 PNG·ZIP)
3. `2_report-review/README.md`와 `VALIDATION.md` (확장 앱과 두 번 실행한 기록)
4. 더 보고 싶다면 각 폴더의 `PRD.md`, `DECISIONS.md`. `API_SPEC.md`는 찾아보는 참조 문서라 처음부터 읽을 필요가 없습니다. 증거 화면은 `evidence/` 폴더에 있습니다.

