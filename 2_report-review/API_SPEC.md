# API 명세 (검수 에이전트)

주소는 `http://127.0.0.1:{포트}`. 모든 응답은 JSON이고 오류는 `{"error": {"code", "message", "retryable", "nextAction"}}` 형식이다. 화면은 `GET /api/projects/{id}`를 폴링(작업 중 2초, 아니면 5초)해서 그린다.

## 단계(stage)
`analyzing`(분석 중) → `findings_ready`(지적 확인·선택) → `revising`(수정·재검수 중) → `done`(결과·파일) → (선택) `findings_ready`로 돌아가 다음 라운드(최대 3번). 분석이 실패·취소·중단되면 `created`(다시 분석 가능), 수정이 실패·취소·중단되면 `findings_ready`(선택이 그대로 남은 채 다시 시작 가능)로 돌아간다.

## 엔드포인트
| 요청 | 설명 |
|---|---|
| `POST /api/projects` `{title, conditions, body}` | 글을 저장하고 분석 작업을 시작한다. 201 `{projectId, runId, stage:"analyzing", paragraphs}`. 본문 30,000자·제목 200자·조건 1,000자 이하, 빈 글은 400. |
| `GET /api/projects` | 최근 프로젝트 목록(복구 진입용). |
| `GET /api/projects/{id}` | 전체 상태: `stage, version, paragraphs[{id,text}], findings, choices, result, currentRun{runId,kind,status,step,message,error}, outputs`. |
| `POST /api/projects/{id}/analyze` `{version}` | 분석이 실패·중단돼 `created`일 때 다시 분석한다(202). |
| `PUT /api/projects/{id}/choices` `{choices}` | `findings_ready`에서 지적별 선택을 저장한다. 값은 `"a"`, `"b"`, `"skip"`, `{"custom": 글}`. 없는 지적·이상한 값은 버리거나 `skip`으로 정리한 결과를 돌려준다. |
| `POST /api/projects/{id}/revise` `{version}` | 고른 지적이 하나 이상일 때 수정·재검수를 시작한다(202). 아무것도 안 골랐으면 400, 단계가 다르면 409 `wrong_stage`, 화면이 오래됐으면 409 `stale_version`. |
| `POST /api/runs/{runId}/cancel` | 진행 중인 작업을 취소하고 `claude` 프로세스까지 종료한다. `{cleanup:{processesTerminated}}`. 이미 끝났으면 409. |
| `GET /api/projects/{id}/outputs/{final.md\|review_report.md}` | `done`에서만. 다른 이름은 404. |
| `GET /api/health` | `{engine, checks:{claude, search}, settings}`. 확인하지 않은 항목은 `unknown`. |

## 지적(finding) 한 건
```json
{ "id": "f1", "priority": 1, "category": "근거 부족", "paragraphId": "P2", "quote": "글에 있는 문장 그대로", "problem": "…", "why": "…",
  "directions": [ {"id":"a","label":"…","detail":"…","needsSearch":true}, {"id":"b","label":"…","detail":"…","needsSearch":false} ], "recommended": "a" }
```
- `category`는 구조 / 논리 / 근거 부족 / 비약 / 조건 미충족. 지적은 최대 5개이고 우선순위 순서다.
- `quote`는 그 문단에 실제로 있는 문장이어야 한다. 없으면 모델에게 한 번 고쳐 쓰게 하고, 그래도 없으면 분석을 실패로 처리한다(`engine_failed`).

## 작업과 모델 호출 (엔진)
`claude -p --restricted --tools <도구> --permission-mode dontAsk --strict-mcp-config --model sonnet --effort medium --output-format stream-json --verbose --json-schema <규격>`, 프롬프트는 표준입력.
| 작업 | 도구 | 설명 |
|---|---|---|
| 분석 | 없음 | 문단 번호가 붙은 글과 제목·조건을 주고 지적·방향을 받는다. |
| 수정(지적마다) | `needsSearch`인 방향만 WebSearch·WebFetch | 문단 하나를 고친 `newText`와 근거 `sources`(주소·제목·인용), 또는 자료가 없으면 `insufficient`와 `gap`. |
| 재검수 | 없음 | 고친 문단마다 해결(resolved)/일부(partial)/미해결(unresolved)과 이유, 새로 생긴 문제. |
- 형식이 틀리면 같은 세션에서 한 번만 고쳐 쓰게 하고, 그래도 틀리면 실패로 처리한다(완료로 치지 않는다).
- 검색 도구가 한 번도 성공하지 못했는데 검색이 필요한 방향이면 `search_unavailable`로 실패한다. 로그인 문제는 `auth_required`.
- 수정 결과의 `sources` 주소는 이번 검색·열람 기록에 있는 것만 인정한다. 본문의 `[n]`은 `sources`와 짝이 맞아야 한다. 근거 인용문과 원문에 없는 수치는 `warnings`로 남긴다.
- 같은 수정 작업 안에서 출처 번호는 글 전체에서 이어 붙인다(같은 주소는 같은 번호).

## 결과(result)
`items[]`(지적 순서): `finding, directionLabel, detail, verdict(resolved|partial|unresolved|insufficient|skipped), before, after, sources[{n,url,title}], warnings, reason, gap, newIssues`, `sources[]`(전체 참고 자료), `gaps[]`(자료 부족 목록), `checks{untouchedChanged, changed}`, `finishedAt`, `files`.
- `checks.untouchedChanged`는 고르지 않은 문단 중 바뀐 문단 id 목록이다. 정상이면 빈 배열.
- `insufficient`인 지적은 원문을 그대로 두고 `⚠[자료 보강 필요: …]`만 덧붙이며 재검수를 모델에 묻지 않는다.

## 서버 재시작
시작할 때 `queued`/`running`인 작업을 `interrupted`로 바꾸고 프로젝트를 다시 시작할 수 있는 단계로 돌린다(분석 → `created`, 수정 → `findings_ready`, 선택은 유지). 서버 프로세스가 강제 종료돼도 `claude` 하위 프로세스는 Windows Job Object로 함께 끝난다.

## 라운드(새 문제를 이어서 고치기)
`done` 단계에서 재검수가 찾은 새 문제 중 중요도 높음·보통을 다음 라운드의 지적으로 잇는다.

`POST /api/projects/{id}/next-round` `{version, issues: ["f2.1", ...]}` — 이번 라운드 지적 id와 그 안의 새 문제 번호(`f2.1` = f2의 첫 새 문제). 성공하면 `{round, findings, stage:"findings_ready"}`.
- 이어지는 것: 고친 글(`result.finalParagraphs`)이 다음 라운드의 글이 되고, 새 문제 하나가 지적 하나(`category: "새 문제"`, `severity`)가 된다. 수정 방향 2개와 추천은 재검수가 이미 붙여 두어서 추가 모델 호출이 없다. 중요도 높음이 먼저 오고 같으면 고른 순서다.
- 이전 라운드의 지적·선택·결과는 `history`로 보관된다. 이후 선택(`PUT choices`)·수정(`POST revise`)은 처음과 같다.
- 오류: 400(없는 번호·중요도 낮음·방향 없는 새 문제·빈 목록), 409 `stale_version` / `wrong_stage`(done이 아님) / `round_limit`(이미 3번째 라운드).

상태(`GET /api/projects/{id}`)에는 `round`, `maxRounds`(3), `history[{round, findings, result}]`가 추가된다. `paragraphs`는 분석 때 저장한 문단(라운드가 지나면 고친 글)이다.

## 새 문제(newIssues)의 모양
```json
{ "id": "1", "text": "한 문장", "severity": "high|medium|low", "paragraphId": "P3",
  "directions": [ {"id":"a","label":"…","detail":"…","needsSearch":true}, {"id":"b", "...": "..."} ], "recommended": "a" }
```
- 이번 수정이 만든 문제만, 항목당 최대 2개. 높음·보통은 `directions`(정확히 2개)와 `recommended`가 반드시 있고 낮음은 없다.
- 결과의 `remaining`: `{newIssues:{high,medium,low}, unresolved, partial, insufficient, usable}`. `usable`은 high·unresolved·insufficient가 모두 0일 때만 true.

## 재검수에 주는 정보
지적마다 문제·선택한 방향·고치기 전·후와 함께 **쓴 출처(번호·제목·주소)**와 **앱의 기계 검사 결과**를 준다. 새 문제로 치지 않는 것: 원래 있던 문제, 문체 취향, 쓴 출처 목록에 있는 `[n]`을 "출처 없음"이라 하는 것, 기계 검사에 이미 적힌 수치의 반복.

## 참고 자료 번호
수정 프롬프트에 "지금까지 참고 자료 N개"를 알려 주고, 새 자료는 `[N+1]`부터 이어 쓰게 한다. 이미 쓴 자료(주소가 같음)를 다시 쓰면 그 번호로 바꾼다. 문단에 이미 있는 `[n]`은 그대로 둘 수 있다. 번호 규칙을 어기면 형식 오류로 한 번 고쳐 쓰게 한다.

## 근거가 부족할 때: 보류(held)와 추가 자료
수정 결과(모델 응답)에 `held: [{claim, needed}]`(최대 3개)가 있다. `status`는 근거가 일부라도 있으면 `revised`(근거 있는 부분만 고침) 없으면 `insufficient`(문단 그대로). 앱은 보류가 있으면 글에 `⚠[자료 보강 필요: …]`를 덧붙이고, 결과 항목의 `held`, 결과의 `gaps`(글에서 읽은 표시 목록)와 `remaining.held`(보류한 내용 수)에 기록한다. `usable`은 `held`가 0일 때만 true.

`POST /api/projects/{id}/materials` `{version, materials: {"f3": "붙여 넣은 자료", ...}}` — `done`에서 보류한 항목(`held`가 있는 지적 id)에 사용자가 자료를 붙여 넣는다(항목당 8,000자 이하). 성공하면 `{round, findings, stage:"findings_ready"}`: 그 항목들이 다음 라운드의 지적(원래 문제 그대로, `material`과 `fromFinding`, 방향 a=제공한 자료로 보강·b=제공한 자료+웹 검색)이 되고 선택·수정은 처음과 같다. 오류: 400(보류 항목이 아님·빈 자료·8,000자 초과), 409 `stale_version` / `wrong_stage` / `round_limit`.
- 수정 프롬프트는 자료를 "데이터로만 취급"하게 하고 그 자료에 있는 내용만 근거로 쓰게 한다. 출처는 `url: "user-material"`로 받아 번호를 붙이고 화면과 `final.md`에는 `(사용자 제공 자료)`로 보인다. 인용문(`quote`)이 붙여 넣은 자료에 글자 그대로 없으면 `warnings`에 "제공한 자료에 그대로 없는 인용문"이 들어간다.
- 자료를 받아 고칠 때 그 문단의 이전 ⚠ 표시는 지우고 시작한다. 이번 자료로도 확인하지 못한 것은 모델이 `held`로 다시 적는다.
