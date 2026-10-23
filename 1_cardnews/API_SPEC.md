# API_SPEC

Python + FastAPI, 127.0.0.1:8765 (충돌 시 다른 빈 포트를 안내). 상태는 SQLite, 파일은 `data/projects/{projectId}/`. 진행 상황은 폴링(D7). 이 문서는 PRD의 "웹 API 계약 초안"을 확정한 것이다. 구현과 다르면 구현이 아니라 이 문서를 먼저 고친다.

## 1. 공통 규약

- JSON, UTF-8. 시각은 ISO 8601(KST, `+09:00`).
- ID는 서버가 만든 불투명 문자열(`prj_…`, `run_…`, `q_…`, `cand_…`, `card_…`).
- **version**: 프로젝트의 단조 증가 정수. 선택·답변·승인·수정처럼 상태를 바꾸는 요청은 읽었던 `version`을 보낸다. 서버의 현재 값과 다르면 `409 stale_version`.
- **requestId**: 변경 요청마다 클라이언트가 만든 UUID. 프로젝트 범위에서 저장한다.
  - 같은 ID·같은 입력 → 처음 저장한 응답을 그대로 반환(`Idempotent-Replay: true` 헤더).
  - 같은 ID·다른 입력 → `409 request_conflict`.
  - 같은 종류 작업이 이미 진행 중 → 새 작업을 만들지 않고 진행 중인 `runId` 반환.
- 오류 응답 형식:

```json
{ "error": { "code": "stale_version", "message": "화면이 오래되었습니다.", "retryable": false, "nextAction": "새로고침 후 다시 시도", "detail": {} } }
```

### 오류 코드

| HTTP | code | 의미 / 화면 안내 |
|---|---|---|
| 400 | invalid_input | 형식 오류(주제 빈 값, 선택 0개 또는 4개 이상 등) |
| 404 | not_found | 프로젝트·작업·질문 없음 |
| 409 | stale_version | 오래된 version. 새로고침 안내 |
| 409 | stale_question | 이미 답했거나 교체된 질문 ID/버전. 현재 질문 표시 |
| 409 | request_conflict | 같은 requestId, 다른 입력 |
| 409 | wrong_stage | 현재 단계에서 허용되지 않는 요청(선택 전 제작 시작, 스토리보드 승인 전 이미지 제작 등) |
| 409 | not_approved | exports 요청인데 최종 승인된 버전이 없음 |
| 424 | search_unavailable | 엔진 검색 도구 없음/호출 실패. 원인 표시 + 재시도 |
| 424 | auth_required | Claude CLI 또는 agy 로그인 필요. 사용자가 직접 로그인하도록 안내(자격 증명은 앱이 다루지 않음) |
| 424 | engine_failed | 엔진 비정상 종료·형식 오류(완료로 처리하지 않음) |
| 424 | image_failed | 해당 카드 이미지 생성/검증 실패. 카드별 재시도 |
| 424 | agy_unavailable | agy 미설치·권한 없음. 성공한 척하지 않음 |
| 499 | cancelled | 사용자가 취소 |

## 2. 상태 모델

### 프로젝트 단계 `stage`

```
created → researching → candidates_ready → selected → deep_research
        → [waiting_for_user ↔ deep_research] → storyboard_ready
        → (approve storyboard) → producing → review → (approve final) → approved
```

수정 요청 시 `review`/`approved` → `revising` → `review`(승인 해제). 어떤 단계에서든 `failed`(재시도 가능 여부 포함)와 `cancelled`로 갈 수 있다.

### 작업 `run.status`

`queued | running | waiting_for_user | succeeded | failed | cancelled | interrupted`

- `waiting_for_user`는 실패가 아니다. 현재 CLI 호출은 끝났고 답변 후 같은 엔진 세션에 다음 호출로 이어간다.
- `interrupted`: 서버 재시작 시 `running`이던 작업에 붙인다. 화면은 "이어서 진행 / 처음부터 재시도"를 보여 준다.

### 엔진 정규화 결과 (앱 내부 규약. CLI 공통 규격이 아님)

| status | 필드 |
|---|---|
| progress | message, step |
| need_input | questionId, question, options[{id,label,description}], multiSelect, allowFreeText, recommendation{optionId, reason} |
| result | kind(candidates/research_notes/storyboard/...), payload |
| error | code, message, retryable |

CLI 출력이 이 형식으로 해석되지 않으면 `engine_failed`. 완료로 처리하지 않는다.

## 3. 엔드포인트

### POST /api/projects
주제로 프로젝트 생성. 조사는 시작하지 않는다.

요청: `{ "topic": "AI 최신소식", "period": { "days": 7 }, "requestId": "…" }` (`period` 생략 시 최근 7일, 허용 값 7 또는 30)

응답 201: `{ "projectId": "prj_…", "version": 1, "stage": "created", "researchWindow": { "days": 7, "basis": "2026-10-23T10:00:00+09:00" } }`

- 조사 기준 시각은 이 시점이 아니라 research 실행 시점으로 확정해 `researchWindow`를 갱신한다.
- 주제는 셸 문자열에 이어 붙이지 않고 인자 배열로만 전달한다. 길이 1~200자.

### POST /api/projects/{id}/research
넓은 조사 시작(또는 30일 확장/재시도). 즉시 반환.

요청: `{ "requestId": "…", "version": 1, "days": 7 }`

응답 202: `{ "runId": "run_…", "status": "queued", "stage": "researching" }`

- 결과 부족 시 서버가 `need_input`(질문: 30일로 넓힐까요?)을 만든다. 사용자가 답하면 `days: 30`으로 다시 조사한다.
- 후보 7~12개 목표. 부족하면 개수와 이유를 `candidatesNote`에 담는다.

### GET /api/projects/{id}
화면이 폴링하는 단일 상태 조회. 새로고침 복구의 기준.

응답 200:

```json
{
  "projectId": "prj_…", "topic": "AI 최신소식", "version": 7, "stage": "waiting_for_user",
  "engine": "claude-cli", "mock": false,
  "researchWindow": { "days": 7, "from": "2026-10-16", "to": "2026-10-23", "basis": "2026-10-23T10:00:00+09:00" },
  "currentRun": { "runId": "run_…", "status": "waiting_for_user", "step": "audience", "message": "…", "startedAt": "…", "error": null },
  "candidatesNote": null,
  "candidates": [
    { "id": "cand_1", "title": "…", "summary": "…", "publishedAt": "2026-10-21", "eventDate": null,
      "dateStatus": "confirmed|unconfirmed", "category": "model|product|research|devtool|other",
      "sources": [{ "title": "…", "url": "https://…", "publisher": "…" }],
      "whySelect": "…", "uncertainty": "…", "groupedCount": 3 }
  ],
  "selection": { "candidateIds": ["cand_1"], "savedAtVersion": 4 },
  "question": { "questionId": "q_…", "version": 7, "question": "어떤 독자에게 전달할까요?",
    "options": [{ "id": "general", "label": "일반 사용자", "description": "쉬운 말과 일상 활용 중심" }],
    "multiSelect": false, "allowFreeText": true,
    "recommendation": { "optionId": "general", "reason": "…" }, "status": "open" },
  "researchNotes": { "facts": [], "sources": [], "limits": [], "conflicts": [] },
  "proposal": { "audience": "…", "whyUseful": "…", "format": "list|story",
    "formatComparison": [
      { "format": "list", "fit": "…", "cards": 6, "evidenceDifficulty": "…" },
      { "format": "story", "fit": "…", "cards": 6, "evidenceDifficulty": "…" } ],
    "formatRecommendation": { "format": "list|story", "reason": "…" },
    "freshnessBasis": { "researchBasis": "2026-10-23", "publishedAt": ["2026-10-21"], "titleMayUseLatest": true },
    "hooks": ["…","…"] },
  "storyboard": { "version": 1, "approved": false, "cards": [
    { "id": "card_1", "order": 1, "role": "cover|body|summary", "title": "…", "keyline": "…",
      "evidence": [{ "url": "…", "note": "…" }], "imagePlan": "…" } ] },
  "cards": [
    { "id": "card_1", "currentVersion": 2, "approvedVersion": 1, "status": "pending|generating|ready|failed",
      "error": null, "imageUrl": "/api/projects/prj_…/files/cards/card_1/v2/final.png",
      "checks": { "fact": "pass|fail|unchecked", "layout": "pass|fail|unchecked", "notes": [] } } ],
  "finalApproval": { "approved": false, "resultVersion": null, "approvedAt": null },
  "exportsReady": false
}
```

- `mock: true`이면 모든 후보·질문·카드가 모의 데이터이며 화면에 배지를 붙인다.
- `question.recommendation`은 표시용이다. 사용자가 답하기 전에는 응답으로 간주하지 않는다.
- 이 응답만으로 새로고침 뒤 후보·선택·진행 상태·질문이 복구돼야 한다.

### POST /api/projects/{id}/selection
후보 1~3개 선택 저장. 이 호출 자체는 심층 조사를 시작하지 않는다.

요청: `{ "candidateIds": ["cand_1","cand_4"], "version": 5, "requestId": "…" }`

응답 200: `{ "version": 6, "selection": { "candidateIds": [...] }, "stage": "selected" }`

오류: 4개 이상·중복·존재하지 않는 ID → 400. 후보가 없는 단계 → `wrong_stage`.

2단계 구현에서 정한 점: 선택 저장은 0~3개를 받는다(0개는 선택 해제이며 stage가 `candidates_ready`로 돌아간다). 1~3개 조건은 `/deep-research` 시작 시 검사한다(0개면 400). 화면이 체크할 때마다 바로 저장해 새로고침 후에도 선택이 남게 하려는 목적이다.

### POST /api/projects/{id}/deep-research
(PRD 표에 없는 추가 엔드포인트) 선택 확정 뒤 심층 조사 시작. 사용자의 명시적 "다음 단계" 클릭에서만 호출된다.

요청: `{ "requestId": "…", "version": 6 }` → 202 `{ "runId": "run_…", "status": "queued" }`

### POST /api/projects/{id}/answers
질문 답변. 답변은 질문 ID와 버전을 확인해 한 번만 반영한다.

요청:

```json
{ "questionId": "q_…", "version": 7, "answer": { "optionIds": ["general"], "freeText": null }, "requestId": "…" }
```

응답 200: `{ "accepted": true, "version": 8, "stage": "deep_research", "runId": "run_…", "status": "running" }`

- 이미 답했거나 교체된 질문 → `409 stale_question`(오래된 화면의 답변 거절).
- 같은 requestId 재전송(버튼 두 번 클릭) → 저장한 응답 반환, 엔진 재호출 없음.
- 답변 후 같은 `runId`를 `running`으로 되돌리고 저장된 엔진 세션 ID로 다음 CLI 호출을 한다.
- `optionIds`와 `freeText` 중 하나 이상 필요. `allowFreeText: false`인 질문에 자유 입력이면 400.

### POST /api/projects/{id}/approve
요청: `{ "stage": "storyboard", "version": 12, "requestId": "…" }` 또는 `{ "stage": "final", "version": 31, "requestId": "…" }`

응답 202/200:
- `storyboard`: 승인한 스토리보드 버전을 저장하고 이미지 제작 작업 `{ "runId": "run_…", "stage": "producing" }`을 시작한다. 스토리보드 단계가 아니면 `wrong_stage`.
- `final`: 현재 카드 결과 버전을 승인으로 저장 `{ "finalApproval": { "approved": true, "resultVersion": 31 } }`. 사실/출처 점검과 글자·가독성 점검이 모든 카드에서 `pass`가 아니면 `409 wrong_stage`와 실패 카드 목록.
- 스토리보드 변경 요청은 `/revisions`에서 `cardIds`를 전체로 보내거나, 별도 `storyboard` 대상(`target: "storyboard"`)으로 처리한다.

### POST /api/projects/{id}/revisions
특정 카드 수정(스토리보드 단계에서는 기획 변경 요청).

요청: `{ "target": "cards", "cardIds": ["card_3"], "instruction": "제목을 더 짧게", "version": 31, "requestId": "…" }` (`target`은 `cards` 또는 `storyboard`, 생략 시 `cards`)

응답 202: `{ "runId": "run_…", "status": "queued", "stage": "revising" }`

- 수정 대상이 아닌 카드의 승인 버전은 바뀌지 않는다. 대상 카드는 새 버전(`v3`)으로 저장하고 이전 버전 파일을 보관한다.
- 요청 즉시 `finalApproval`을 해제하고 version을 올린다.
- `cardIds`가 비었거나 없는 카드면 400.

### POST /api/runs/{id}/cancel
응답 200: `{ "runId": "run_…", "status": "cancelled", "cleanup": { "processesTerminated": 1, "partialFilesRemoved": true } }`

- 실행 중인 `claude`/`agy` 하위 프로세스 트리까지 종료하고 확인한 뒤에 `cancelled`로 표시한다. 종료를 확인하지 못하면 `status: "cancelling"`과 경고를 반환한다.
- 이미 끝난 작업은 `409 wrong_stage`.

### GET /api/projects/{id}/exports
최종 승인된 버전만 제공. 승인 전 `409 not_approved`.

응답 200:

```json
{ "resultVersion": 31, "verifiedAt": "…",
  "files": [
    { "kind": "card_png", "cardId": "card_1", "name": "card_01.png", "url": "…", "width": 1080, "height": 1350, "bytes": 412300, "verified": true },
    { "kind": "zip", "name": "cardnews_prj_….zip", "url": "…", "bytes": 3200000, "verified": true },
    { "kind": "provenance", "name": "PROVENANCE.md", "url": "…" } ] }
```

- `verified`는 파일 존재, PNG 시그니처, 실제 크기 1080×1350을 서버가 검사했다는 뜻.
- ZIP에는 카드 PNG와 `PROVENANCE.md`(출처 URL·작가·라이선스·필요 표기, 생성 이미지의 도구·날짜·프롬프트, 생성 이미지임을 밝히는 문구)를 포함한다. PDF는 제외(D12).

### 보조 엔드포인트

- `GET /api/projects` — 프로젝트 목록(복구 진입용). 각 항목에 `projectId, topic, stage, updatedAt`.
- `GET /api/projects/{id}/files/{path}` — 프로젝트 디렉터리 안 파일만 제공. 경로 탈출(`..`)과 절대 경로는 거절.
- `POST /api/runs/{id}/resume` — `interrupted` 작업을 저장된 세션으로 이어서 진행. 요청 `{ "mode": "continue|restart", "requestId": "…" }`.
- `GET /api/health` — `{ "ok": true, "engine": "claude-cli", "checks": { "claude": "unknown|ok|auth_required|missing", "search": "…", "agy": "…" } }`. 미검증 항목은 `unknown`으로 표시하고 `ok`로 추측하지 않는다.

## 4. 저장 항목 (SQLite 개요)

- `projects(id, topic, stage, version, engine, mock, window_days, window_basis, created_at, updated_at)`
- `runs(id, project_id, kind, status, engine, engine_session_id, step, message, error_code, error_message, pid_info, created_at, updated_at)`
- `questions(id, project_id, run_id, version, question, options_json, multi_select, allow_free_text, recommendation_json, status, answer_json, answered_at)`
- `candidates`, `selections`, `research_notes`, `storyboards`, `cards`, `card_versions(card_id, version, files, prompt, tool, created_at, license_json)`
- `requests(project_id, request_id, endpoint, input_hash, response_json)` — 멱등성
- 엔진 세션 ID는 `(project_id, engine)` 쌍으로만 재사용한다. 엔진이 바뀌면 저장된 프로젝트 요약으로 새 세션을 연다. `--continue`는 쓰지 않는다.
- API 키·인증 파일 내용은 저장하거나 로그에 남기지 않는다.

## 5. 실행 구조

- 서버는 요청 안에서 엔진을 오래 기다리지 않는다. `run`을 만들고 즉시 반환하며, 별도 작업 프로세스(워커)가 `claude -p … --output-format json` 등을 인자 배열로 실행한다. 작업별 디렉터리 `data/projects/{id}/runs/{runId}/`를 쓴다.
- 서버 시작 시 `running`인 작업은 `interrupted`로 바꾸고 복구 선택을 노출한다.
- Claude CLI 호출은 인자 배열로 `claude -p --restricted --tools WebSearch,WebFetch … --model sonnet --effort medium --output-format stream-json --verbose --json-schema <규격>`(이어갈 때 `--resume <세션ID>` 추가)를 쓰고 프롬프트는 표준입력으로 보낸다(D9). 처음 계획한 `--output-format json` 대신 `stream-json`을 쓰는 이유와 전체 연결 방식은 9장에 있다. 모델·effort는 설정 한 곳에서 읽고, `GET /api/health`에 실제 사용 값을 보여 준다.
- 엔진 인터페이스(내부): `start(run)`, `answer(run, question, answer)`, `status(run)`, `cancel(run)`.
- 이미지: agy에 자연어로 요청하고, 결과 파일의 존재·PNG 형식·크기를 검사한다. 실패하면 카드에 `failed`와 재시도 버튼을 표시한다.

## 6. 화면 흐름

단일 페이지(`/`), 상단 단계 표시 + 본문. 모든 화면은 `GET /api/projects/{id}`를 2초마다 폴링하고(`waiting_for_user`·`review` 등 대기 단계에서는 5초), 현재 `stage`로 렌더링한다. URL은 `/#/p/{projectId}`라 새로고침해도 같은 프로젝트로 돌아온다. 화면 오른쪽 위에 `engine`, `mock` 배지를 표시한다.

### 화면 목록과 전이

```
[S0 시작]──주제 입력──▶[S1 조사 중]──▶[S2 후보 선택]──1~3개 선택, "다음"──▶[S3 심층 조사]
                           │ 부족                              ▲
                           └─▶(질문: 30일로 넓힐까요?)──예──────┘(S1 재실행)
[S3]──독자 불명확──▶[Q 질문 대기]──답변──▶[S3]
[S3]──▶[S4 스토리보드 승인]──승인──▶[S5 제작 중]──▶[S6 검수]
[S4]──변경 요청──▶[S3/스토리보드 재작성]
[S6]──카드 수정 요청──▶[S5 해당 카드만]──▶[S6]
[S6]──최종 승인──▶[S7 내려받기]
```

| 화면 | 보여 줄 것 | 사용자 동작 | 호출 API |
|---|---|---|---|
| S0 시작 | 주제 입력, 기간 선택(7일 기본, 30일), 과거 프로젝트 목록 | 주제 입력 → "조사 시작" | POST /projects, POST /research |
| S1 조사 중 | 조사 기준 시각·기간, 단계 메시지, 취소 버튼 | 취소 | GET /projects/{id}, POST /runs/{id}/cancel |
| S2 후보 선택 | 후보 카드 7~12개: 제목·요약·게시일·사건일(미확인 표시)·원문 링크·선택 가치·불확실성, 묶인 기사 수, 선택 개수(1~3) 표시. 후보가 부족하면 이유와 "30일로 넓히기" | 체크, "다음 단계" | POST /selection, POST /deep-research |
| Q 질문 | 질문·선택지(차이 한 줄)·추천 표시(미선택 상태 유지)·자유 입력 | 선택 또는 입력 후 "답변 보내기"(전송 중 비활성) | POST /answers |
| S3 심층 조사 | 진행 메시지, 조사 기록(사실/출처/한계/상충), 제안(독자·유용한 이유·형식·후킹 2~3개) | 필요 시 질문에 답변, 취소 | GET, POST /answers |
| S4 스토리보드 | 카드 5~8장 표: 역할·제목·핵심 문장·근거 출처·그림 계획 | "승인" 또는 변경 요청 입력 | POST /approve(storyboard), POST /revisions(storyboard) |
| S5 제작 중 | 카드별 상태(대기/생성 중/완료/실패), 실패 카드의 사유와 "재시도", 취소 | 카드별 재시도, 취소 | GET, POST /revisions(해당 카드), POST /runs/{id}/cancel |
| S6 검수 | 전체 보기+개별 보기, 휴대폰 폭 미리보기, 사실/출처 점검·글자 점검 결과(별도 표시), 버전 이력 | 카드 선택 후 수정 요청, "최종 승인" | POST /revisions, POST /approve(final) |
| S7 내려받기 | 카드별 PNG, 전체 ZIP, 출처·이미지 기록, 검증 결과 | 다운로드 | GET /exports |

### 공통 동작

- **오류 화면**: 오류 코드별 이유와 다음 행동(재시도, 로그인 안내, 새로고침)을 표시한다. 로그인이 필요하면 "터미널에서 직접 로그인 후 재시도"를 안내하고 자격 증명 입력란은 만들지 않는다.
- **중복 클릭 방지**: 변경 요청은 클릭 시 requestId를 만들어 버튼을 잠그고, 응답 전 재클릭해도 같은 ID로 재전송한다.
- **오래된 화면**: `409 stale_version/stale_question`이면 "다른 곳에서 바뀌었습니다. 새로고침합니다"를 보여 주고 최신 상태를 다시 불러온다.
- **재시작 복구**: `interrupted` 작업은 "이어서 진행 / 처음부터 재시도" 선택을 보여 준다.
- **모의 데이터**: `mock: true`이면 후보·질문·카드 위에 "모의 데이터" 배지를 붙이고 조사 기준 시각 옆에 "실제 검색 결과 아님"을 표시한다. 모의 데이터를 실제 최신 검색 결과처럼 보여 주지 않는다.
- **접근성**: 휴대폰 폭(≈390px)에서 후보 목록과 카드 미리보기가 가로 스크롤 없이 읽혀야 한다.

## 7. 구현 단계 대응 (아래 8장에 2단계 구현 상태)

1. (이 문서) 계약과 화면 흐름 확정.
2. 모의 엔진으로 S0~S3(후보 선택·질문 대기·답변 저장·새로고침 복구·중복 방지) 구현. `mock: true`.
3. Claude CLI 엔진을 같은 인터페이스에 연결해 실제 조사와 `--resume` 이어가기 확인.
4. agy 이미지 1장 생성·검증.
5. S4~S7, Pillow 글자 렌더링, 부분 수정, 내보내기.
6. ACCEPTANCE.md로 확인하고 미검증 항목은 미확인으로 기록.

## 8. 2단계 구현 연결 방식 (모의 엔진, S0~S3)

### 구현된 것과 아직 없는 것

| 구분 | 상태 |
|---|---|
| 구현됨 | `POST /api/projects`, `GET /api/projects`, `GET /api/projects/{id}`, `POST /research`, `POST /selection`, `POST /deep-research`, `POST /answers`, `POST /api/runs/{id}/cancel`, `POST /api/runs/{id}/resume`, `GET /api/health`, 화면(`/`) |
| 아직 없음(2단계 당시. 이후 모두 구현됨) | `POST /approve`, `POST /revisions`, `GET /exports`, `GET /files/{path}` |

### 파일 구성

- `run.py` — 서버 실행(127.0.0.1만 바인딩, 8765 사용 중이면 8766~8804 중 빈 포트 안내, `--port`로 지정 가능).
- `app/main.py` — 엔드포인트, 멱등성(`requests` 표), 버전 검사, 오류 형식.
- `app/db.py` — SQLite(`data/app.db`). 모든 쓰기는 한 잠금 아래 트랜잭션으로 처리.
- `app/mock_engine.py`, `app/mock_data.py` — 모의 엔진과 모의 데이터. 엔진 인터페이스는 `start / answer / resume / cancel / status`이며 3단계에서 Claude CLI 엔진이 같은 자리에 들어온다.
- `app/static/` — 화면(`index.html`, `app.js`, `style.css`). 프레임워크 없이 만들었다.

### 화면 ↔ API 연결

화면은 `GET /api/projects/{id}`가 돌려주는 상태만 보고 그린다(브라우저에 따로 저장하는 진행 상태 없음). 주소는 `/#/p/{projectId}`라 새로고침하면 같은 프로젝트로 돌아온다. 진행 중(`queued/running`)에는 2초, 그 밖에는 5초마다 다시 읽는다. 상태가 바뀐 경우에만 다시 그린다.

| 화면 동작 | 호출 | 비고 |
|---|---|---|
| "조사 시작" | `POST /api/projects` → `POST /api/projects/{id}/research` | 응답의 `version`을 이어서 사용 |
| 후보 체크/해제 | `POST /selection` (체크할 때마다) | 요청은 한 줄로 순서대로 보내고, 저장 중에는 폴링을 쉰다 |
| "30일로 넓히기" | `POST /research` `{days: 30}` | `candidatesNote`가 있을 때만 표시 |
| "다음 단계: 심층 조사" | `POST /deep-research` | 선택 1~3개일 때만 활성화 |
| "답변 보내기" | `POST /answers` | 선택 또는 직접 입력이 있을 때만 활성화 |
| "취소" | `POST /api/runs/{id}/cancel` | |
| "이어서 진행 / 처음부터 다시" | `POST /api/runs/{id}/resume` | `currentRun.status == "interrupted"`일 때 |
| "다시 시도" | 실패한 작업 종류에 따라 `/research` 또는 `/deep-research` | `currentRun.status == "failed"`일 때 |

변경 요청의 `requestId`는 동작과 내용으로 키를 만들어 처음 한 번만 새로 만든다. 응답을 기다리는 동안 버튼이 잠기고, 네트워크 오류로 다시 보낼 때는 같은 `requestId`를 쓴다. 오래된 화면(`stale_version`, `stale_question`)은 최신 상태를 다시 불러오고 안내 문구를 보여 준다.

### 1~7장과 달라지거나 구체화한 점

- **`stage` 추가**: 심층 조사와 질문 답변이 끝나면 `proposal_ready`(조사 기록·제안 확인)가 된다. 스토리보드 단계(`storyboard_ready`)는 아직 없다.
- **실패 시 `stage`**: 작업이 실패하면 `stage`는 작업을 시작하기 전 단계로 돌아가고, `currentRun.status`가 `failed`이며 `currentRun.error`에 코드·메시지·다음 행동이 담긴다(2장의 `failed` 단계 대신).
- **취소 시 `stage`**: 조사 작업은 후보가 있으면 `candidates_ready`/`selected`, 없으면 `created`로, 심층 조사는 `selected`로 돌아간다. 대기 중이던 질문은 `cancelled`가 되어 이후 답변은 `stale_question`이다.
- **`/answers`의 `version`**: 질문의 `version`(질문이 만들어질 때의 프로젝트 버전)을 보낸다. 질문 ID·상태(`open`)·버전이 모두 맞아야 반영된다.
- **`/research`**: 진행 중인 조사가 있으면 새 작업을 만들지 않고 같은 `runId`를 `202`로 돌려준다. 조사 기준 시각은 이 요청을 받은 시점으로 갱신된다. 조사가 끝나 후보가 바뀌면 이전 선택은 지워진다.
- **서버 시작 시**: `queued/running` 작업은 `interrupted`가 된다. `waiting_for_user`인 작업과 열려 있는 질문은 그대로 남는다.
- **`/runs/{id}/cancel`**: 모의 엔진에는 하위 프로세스가 없어 `processesTerminated`는 0이다. 실제 엔진 연결 후 실제 값으로 채운다.
- **Host 검사**: `Host` 헤더가 `127.0.0.1` 또는 `localhost`가 아니면 거절한다.

### 모의 시나리오 (주제에 이 낱말이 들어가면 작동)

| 주제에 포함 | 동작 |
|---|---|
| `부족` | 7일 조사에서 후보가 3개만 나오고 "30일로 넓힐까요?" 안내가 뜬다. 30일로 넓히면 11개 |
| `실패` | 첫 조사만 `search_unavailable`로 실패한다. "다시 시도"하면 성공 |

모의 후보의 원문 링크는 모두 `https://example.com/mock/N`이며 실제 기사가 아니다.

### 확인한 것 (2026-10-23, 이 PC)

- 자동 테스트 23개 통과(`tests/test_flow.py`): 전체 흐름, 중복 요청 재생·충돌, 답변 두 번 반영 안 됨, 오래된 질문 거절, 프로젝트 간 질문 분리, 서버 재시작 후 질문 유지, 중단 인식과 재개, 취소, 선택 규칙, 선택 전 제작 거부, 잘못된 입력 오류 형식, 외부 Host 거절.
- 브라우저(Claude 내장 브라우저)에서 직접 확인: 주제 입력 → 후보 8개 표시(기준 시각·기간, 모의 배지) → 2개 선택 → 새로고침 후 선택 유지 → 심층 조사 → 질문 표시 → 새로고침 후 질문 유지 → 답변 버튼 더블 클릭 시 `POST /answers` 1회 → 제안 화면. 폭 375px에서 가로 스크롤 없음. 콘솔 오류 없음.
- 확인하지 않은 것: 실제 Claude CLI·검색·agy(3단계 이후), 서버를 실제로 껐다 켠 상태의 브라우저 화면(API 테스트로만 확인), Windows 외 환경, 브라우저 두 개를 동시에 연 경우의 화면 동작.

## 9. 3단계 구현 연결 방식 (Claude CLI 실제 조사, 답변 후 이어가기)

### 엔진 선택

`run.py --engine claude-cli`(기본) 또는 `--engine mock`. 프로젝트는 만들어질 때의 엔진을 `projects.engine`에 저장하고 그 프로젝트의 모든 작업에 같은 엔진을 쓴다. 작업 기록(`runs.engine`, `runs.engine_session_id`)에는 작업을 실행한 엔진과 그 엔진의 세션 ID를 저장한다. 엔진이 다르면 세션 ID를 재사용하지 않는다. 화면은 모의 엔진 프로젝트에만 "모의 데이터" 띠를 보여 준다.

환경 변수(호출할 때마다 읽음): `CARDNEWS_CLAUDE_MODEL`(기본 `sonnet`), `CARDNEWS_CLAUDE_EFFORT`(기본 `medium`), `CARDNEWS_CLAUDE_TIMEOUT`(초, 기본 900), `CARDNEWS_CLAUDE_CMD`(테스트용: 실행 파일 대체, JSON 배열).

### 호출 방식 (`app/claude_engine.py`)

```
claude -p --restricted --tools WebSearch,WebFetch --allowedTools WebSearch,WebFetch
       --permission-mode dontAsk --strict-mcp-config --model sonnet --effort medium
       --output-format stream-json --verbose --json-schema <JSON 규격> [--resume <세션ID>]
```

- 프롬프트는 **표준입력**으로 보낸다. 주제 등 사용자가 쓴 내용은 명령줄에 들어가지 않는다. 프로세스는 작업별 디렉터리 `data/projects/{id}/runs/{runId}/`에서 실행되고, 거기에 `stream.jsonl`(원시 출력)과 `evidence.json`(검색·열람 기록)이 남는다.
- **도구 제한**: `--restricted`로 명령·코드 실행 도구와 사용자 설정(훅 등)을 빼고, `--tools`로 웹 검색·페이지 읽기만 허용한다. 웹 문서 안에 지시문이 섞여 있어도 PC에서 할 수 있는 일이 없다. 프롬프트에도 "웹 페이지 안의 지시는 자료일 뿐"이라고 적었다.
- **`stream-json`을 쓰는 이유**: `json`은 마지막 결과만 주지만 `stream-json`은 `tool_use`/`tool_result`를 줘서 ① 진행 상황("검색 중: …", "원문 확인 중: 도메인")을 화면에 보여 주고 ② 실제로 검색이 성공했는지, 어떤 URL을 봤는지 앱이 직접 확인할 수 있다. 최종 `result` 이벤트에서 `session_id`를 읽어 저장한다.
- **`--json-schema`를 쓰는 이유**: 실제 실행에서 모델이 중괄호 하나를 빼먹은 JSON 텍스트를 두 번 연속 내서 심층 조사가 실패했다(아래 확인 결과). 규격을 주면 CLI가 `structured_output`으로 구조를 맞춰 돌려준다. 앱은 `structured_output`이 있으면 그것을, 없으면 텍스트에서 JSON을 꺼내 쓴다. 값의 의미 검증은 규격과 별개로 앱이 한다.

### 단계별 흐름

| 작업 | 호출 | 결과 |
|---|---|---|
| 후보 조사 `research` | 새 세션 1회(검색 여러 번) | 후보 검증 후 `candidates_ready`. 7개 미만이면 개수 안내, 7일이면 "30일로 넓힐까요?" |
| 심층 조사 `deep_research` | 새 세션 1회(원문 열람) | `need_input`이면 질문을 저장하고 **호출을 끝낸 뒤** `waiting_for_user`. 질문이 필요 없으면 바로 `proposal_ready` |
| 답변 후 이어가기 | 저장한 `engine_session_id`로 `--resume` 1회 | 답변을 반영한 최종 결과 → `proposal_ready`. 질문은 작업당 최대 2번 |
| 형식 오류 | 같은 세션을 `--resume`해 형식만 고쳐 쓰게 1회 | 그래도 틀리면 `engine_failed`. 완료로 처리하지 않는다 |
| 이어갈 세션이 없거나 실패 | 저장된 요약(주제·선택 후보·지금까지의 질문·답변)으로 새 세션 | 로그인 오류는 이 대체를 하지 않고 그대로 실패 처리 |

질문 대기 중에는 프로세스가 없다. 서버가 재시작돼도 질문·세션 ID·지금까지의 조사 기록이 DB에 남아 있어 답변하면 이어진다. 서로 다른 프로젝트·작업은 각자의 세션 ID만 쓴다(`--continue` 안 씀).

### 모델 출력에 대한 앱의 검증 (`app/claude_parse.py`)

- **검색이 있었는지**: 후보 조사에서 성공한 `WebSearch`가 한 번도 없으면 후보를 쓰지 않고 `search_unavailable`로 실패한다(검색 없이 나온 그럴듯한 후보를 쓰지 않기 위해).
- **출처 URL**: 후보의 출처 URL은 이번 작업의 검색 결과나 열어 본 페이지에 실제로 나온 주소(www·`utm_*`·끝 슬래시 차이는 무시)만 인정한다. 아니면 그 출처를 버리고, 출처가 없어진 후보는 제외한다.
- **조사 기간**: 게시일이 확인됐는데 기간 밖이면 제외. 형식이 틀리거나 기준일보다 미래인 날짜는 미확인으로 바꾼다. 같은 제목은 하나로 합친다. 최대 12개.
- **사실 상태**: 조사 기록의 사실은 `confirmed`(직접 확인) / `company_claim`(업체 주장) / `announced`(공개 예정) / `unverified`(미확인) 중 하나이고, 근거 URL이 확인되지 않으면 `unverified`로 내린다. 원문을 한 번도 열지 못했으면 `confirmed`를 모두 `unverified`로 내리고 한계에 그 사실을 적는다.
- **'최신'**: `freshnessBasis.titleMayUseLatest`는 앱이 계산한다(선택한 후보 모두 게시일이 확인되고 조사 기간 안일 때만 true). false이면 후킹 문구 중 '최신'이 든 것을 지운다(D15).
- **질문**: 선택지 2~5개와 `label`이 있어야 하고, 추천 선택지 ID가 선택지에 없으면 추천을 버린다.

### 오류 코드 연결

| 상황 | `error.code` | 화면 안내 |
|---|---|---|
| 성공한 검색이 없음 / 검색 도구 오류 | `search_unavailable` | 원인(도구가 돌려준 메시지 일부)과 "다시 시도". health의 `checks.search`는 실패한 경우에만 `search_unavailable` |
| CLI가 로그인 필요 신호를 냄 | `auth_required` | "터미널에서 claude를 실행해 로그인한 뒤 다시 시도". 앱은 로그인 정보를 다루지 않는다. `checks.claude`가 `auth_required` |
| CLI 없음, 실행 실패, 결과 없이 종료, 시간 초과, 형식 오류 | `engine_failed` | 원인과 "다시 시도" |

`GET /api/health`의 `checks.claude`는 `missing`(실행 파일 없음) / `unknown`(아직 호출 전) / `ok`(호출이 성공한 적 있음) / `auth_required`이고, `checks.search`는 `unknown` / `ok` / `search_unavailable`이다. `settings`에 모델·effort가 나온다.

### 취소·종료

`POST /api/runs/{id}/cancel`은 DB 상태를 `cancelled`로 바꾼 뒤 실행 중인 `claude` 프로세스를 하위 프로세스까지 종료하고(`taskkill /T /F`), `cleanup.processesTerminated`에 종료한 수를 돌려준다. 시간 초과도 같은 방법으로 정리한다. 서버를 정상 종료하면(Ctrl+C) 남은 하위 프로세스를 정리한다. 서버가 **강제 종료**돼도(Windows) 하위 프로세스가 남지 않도록 서버 프로세스를 `KILL_ON_JOB_CLOSE` Job Object에 넣는다(`app/procguard.py`, 시작 시 한 번). 적용 여부는 `GET /api/health`의 `childProcessGuard`(`job-object` / `failed: …` / `unsupported-os`)로 보인다.

### 확인한 것 (2026-10-23, 이 PC, 실제 Claude CLI 2.1.306)

**자동 테스트 47개 통과** (`tests/`): 모의 엔진 23개, 검증 규칙 단위 테스트 10개, 가짜 `claude`(`tests/fake_claude.py`)로 엔진을 검사하는 14개. 가짜 CLI 테스트가 확인한 것: 명령줄(`--restricted`, 도구 제한, 모델·effort, `stream-json`, `--json-schema`)과 주제가 명령줄에 없고 표준입력에만 있음, 질문 후 `--resume`이 같은 세션 ID로 호출됨, 답변 버튼 중복에도 CLI 한 번만 호출, 질문 없이 끝나는 경우, 형식 고쳐 쓰기 1회, 세션을 못 이어갈 때 새 세션 대체, 검색 미사용·검색 오류·로그인 필요·시간 초과·실행 파일 없음·형식 오류 각각의 실패 코드, 취소와 시간 초과 때 자식 프로세스가 실제로 사라짐, 출처 위조·기간 밖 후보 제외, `structured_output`이 깨진 텍스트보다 우선.

**실제 모델 호출로 확인한 것** (서버를 실제로 띄우고 실제 `claude`를 호출):

| 항목 | 결과 |
|---|---|
| 검색 도구 사용 가능 여부 (D3) | 사용 가능. `--restricted --tools WebSearch,WebFetch`로 `WebSearch`가 성공하고 결과 URL이 돌아옴 |
| 로그인 | 이 PC의 Claude CLI는 로그인된 상태로 호출 성공(`checks.claude: ok`) |
| 후보 조사 (3회 실행) | 후보 10개(약 40초), 12개(약 100초), 8개(약 80초). 검색 4~10회, 원문 열람 2~3건. 화면에 "검색 중: …", "원문 확인 중: 도메인"과 경과 시간이 표시됨 |
| 출처 검증 | 첫 실행(10개 후보)의 모든 출처 URL 16개가 원시 `stream.jsonl`의 검색·열람 결과에 실제로 있음을 별도 스크립트로 다시 확인 |
| 심층 조사 | 원문을 열어 사실을 `확인됨`/`업체 주장`/`공개 예정`/`미확인`으로 나누고 한계·상충 내용을 적음. 열지 못한 페이지(403 등)는 한계에 그대로 기록됨 |
| 질문 → 답변 → 이어가기 | 후보를 서로 다른 독자층(개발 도구 + 정책 뉴스)으로 골랐을 때 질문이 실제로 나옴(선택지 4개, 추천과 이유). 추천과 다른 선택지로 답하자 답변 접수 → **같은 세션 ID로 두 번째 CLI 호출**(작업의 `init` 이벤트 2개, 고유 세션 ID 1개) → 약 27초 뒤 답변이 반영된 제안(독자: 교육자·정책 분야) |
| 중복·오래된 답변 | 같은 `requestId`로 다시 보내면 같은 응답(재생), 같은 질문에 새 `requestId`로 답하면 `409 stale_question` |
| 프로젝트 간 세션 | 실제 실행한 프로젝트들의 세션 ID가 모두 다름 |
| 로그인 실패 표시 | 실제 CLI를 빈 설정 폴더(`CLAUDE_CONFIG_DIR`)로 실행해 로그인 없는 상태를 만들었더니 `auth_required`와 안내가 화면에 표시됨(CLI의 실제 오류 문구로 감지) |
| 검색 실패 표시 | 검색을 쓰지 않는 가짜 CLI로 `search_unavailable` 오류와 "다시 시도" 버튼이 화면에 표시됨 (실제 검색 장애는 만들 수 없어 가짜 CLI로만 확인) |

**실제 실행 중 발견해 고친 문제**: 첫 심층 조사가 `engine_failed`("JSON 객체를 찾지 못했습니다")로 실패했다. 원시 출력을 보니 모델이 `notes` 객체를 닫지 않고 `proposal`을 이어 써서 중괄호가 하나 모자랐고, 형식 고쳐 쓰기도 같은 오류를 반복했다. 이 실패는 완료로 처리되지 않고 오류와 "다시 시도"로 표시됐다. 원인 대응으로 `--json-schema`를 도입했고 같은 프로젝트에서 "다시 시도"로 복구되는 것까지 확인했다. 그 뒤 실제 심층 조사 3회(CLI 호출 4번, 답변 이어가기 1번 포함)는 모두 형식 오류 없이 끝났다.

### 확인하지 못한 것 · 알려진 한계

- **실제 검색 장애**는 만들지 못했다. 검색 실패 화면은 가짜 CLI로만 확인했다.
- **실제 `claude`로 취소·시간 초과**는 시험하지 않았다. 프로세스 종료는 가짜 CLI 프로세스로만 확인했다. 서버를 강제로 종료(프로세스 킬)하면 하위 프로세스가 남는 문제가 있었으나 Windows Job Object로 고쳤고(`app/procguard.py`) 가짜·실제 `claude` 모두 사라지는 것을 확인했다(VALIDATION.md). 정상 종료(Ctrl+C) 시의 정리 코드는 있으나 시험하지 않았다.
- **브라우저에서 직접 조작한 실제 흐름**은 주제 입력과 조사 시작·진행 표시, 후보 선택(체크박스)·심층 조사 시작, 실패 후 "다시 시도" 버튼까지다. 질문 답변 이후와 나머지 실제 실행은 같은 API를 HTTP 스크립트로 호출해 확인했고, 결과 화면은 브라우저에서 읽어 확인했다.
- **서버 재시작 중 실제 작업 복구**(실제 CLI)와 실제 실행 두 개의 동시 진행은 시험하지 않았다.
- **질문은 모델이 판단**한다. 후보가 비슷한 독자층이면 질문 없이 진행하고(성공한 실제 심층 조사 3회 중 처음 2회) 이유를 `formatRecommendation.reason`에 쓴다. 독자층이 다른 후보를 골랐을 때(3회째) 질문이 나왔다. 항상 묻도록 강제하지 않았다.
- **후킹 문구 검증은 '최신' 규칙만** 앱이 강제한다. "확인된 사실 범위 안에서" 쓰라는 규칙은 프롬프트로만 요구하며, 문구가 `확인됨`이 아닌 사실(예: 보도 인용)에 기대는지는 자동으로 검사하지 않는다. 실제 결과에도 `미확인` 사실을 인용한 문구가 한 건 있었다.
- 후보 출처는 뉴스 모음·언론 기사가 많고 발표 기관의 원문은 일부다. 앱은 출처가 실제로 검색·열람됐는지만 확인하며 기사 내용의 사실 여부는 보증하지 않는다.
- 비용·사용량은 기록하지 않았다(CLI 결과에는 `total_cost_usd`가 있으나 저장하지 않음). 실행당 몇 분의 모델 호출이 일어난다.
- `data/projects/*/runs/*/stream.jsonl`에는 검색·열람 결과 본문과 모델 출력이 그대로 남는다. 로그인 정보는 앱이 다루지 않아 들어 있지 않다.

## 10. 4단계 구현: 스토리보드와 설명용 이미지 (agy, 실패 시 Claude CLI 도안)

### 흐름과 단계(stage)

```
proposal_ready ──POST /storyboard──▶ storyboarding ──▶ storyboard_ready ──POST /approve(storyboard)──▶ producing ──▶ review
                                         ▲                    │ 변경 요청(/revisions) · 다른 형식(/storyboard)  │
                                         └────────────────────┘                          카드 다시 만들기(/cards/retry)─▶ producing
```

승인(`/approve`) 전에는 이미지 작업이 만들어지지 않는다(API로도 불가: `storyboard_ready`가 아니면 `409 wrong_stage`). 작업 종류(`runs.kind`)는 `storyboard`(Claude CLI, 도구 없음)와 `images`(agy가 기본 켜짐이라 먼저 시도하고, 끄면 건너뜀. 이어서 Claude SVG + 브라우저 캡처, 안 되면 Claude 도형 도안 + Pillow)가 추가됐다.

| 요청 | 입력 | 동작 |
|---|---|---|
| `POST /api/projects/{id}/storyboard` | `format`(list·story), `hookIndex`(선택), `instruction`(선택), `version`, `requestId` | `proposal_ready`·`storyboard_ready`에서 스토리보드 작업 시작. 진행 중이면 같은 `runId` 반환. 새 버전으로 저장되고 이전 버전은 보관 |
| `POST /api/projects/{id}/revisions` | `target: "storyboard"`, `instruction`, `version`, `requestId` | 지금 스토리보드의 형식을 유지한 채 변경 요청을 반영해 새 버전을 만든다. `target: "cards"`는 카드 한 장 수정(아래 15장) |
| `POST /api/projects/{id}/approve` | `stage: "storyboard"`, `version`, `requestId` | `storyboard_ready`에서만 가능. 카드 행을 `pending`으로 만들고 이미지 작업 시작(`producing`). `stage: "final"`은 최종 승인(아래 15장) |
| `POST /api/projects/{id}/cards/retry` | `cardIds`(생략하면 실패·대기 카드 전부), `version`, `requestId` | `review`에서 카드를 다시 만든다. 이미 만든 카드를 고르면 **새 버전**으로 만들고 이전 버전 파일은 보관한다 |
| `GET /api/projects/{id}/files/{path}` | | 프로젝트의 `cards/**` 안의 `.png`·`.json`만 내려 준다. `..`·다른 폴더·`agy_work`는 404 |

`GET /api/projects/{id}`에 `storyboard`(버전·형식·표지 후킹·변경 요청·승인 여부·공통 그림체·카드 목록)와 `cards`(카드별 상태 `pending|generating|ready|failed`, 오류, 현재 버전, `imageUrl`, 만든 방식 `tool`, 크기·용량, `checks`, `notes`, `provenance`, 버전 목록)가 채워진다.

### 스토리보드 규칙과 검증 (`claude_parse.validate_storyboard`)

- 5~8장. 카드: `title`(30자 이내), `keyline`(핵심 문장, **한 문장** 70자 이내), `body`(핵심 문장을 풀어 쓰는 1~2문장 160자 이내), `imageRole`(이미지 역할), `imagePlan`(그림 계획), `evidence`(출처). 한 장에 메시지 하나는 길이·문장 수 검사로 강제하고(위반하면 모델에게 고쳐 쓰게 한다), 메시지의 내용이 정말 하나인지는 프롬프트에 의존한다.
- 출처는 선택한 후보·조사 기록에 있던 URL만 인정하고 나머지는 지운다. 본문 카드에 출처가 하나도 남지 않으면 형식 오류, 표지·마지막 카드는 선택한 소식의 출처로 채운다.
- 자료(조사 기록·후보 요약·주제)에 없는 숫자(두 자리 이상)는 카드의 `unsupportedNumbers`에 담아 화면에 "근거를 확인하세요" 경고로 보여 준다(자동 삭제는 하지 않음).
- 역할은 위치로 정한다(첫 장 표지, 마지막 장 마무리). `style.palette`(#RRGGBB 4~6개), `artStyle`, `mood`가 있어야 하며 모든 카드가 이 값을 공유한다.
- 목록형: 표지 + 소식마다 1장 + 한계 + 마무리(5장 미만이면 '독자에게 의미' 추가). 이야기형: 표지·상황·불편·변화·활용·한계·마무리 7장.

### 이미지 만들기 (`app/images.py`)

0. **agy 스위치(기본 켜짐)**: agy는 사용 한도가 작아서 한동안 기본 꺼짐이었다가 사용자의 결정으로 **다시 기본 켜짐**이 됐다. 끄는 방법은 서버 실행 옵션 `run.py --no-agy` 또는 환경 변수 `CARDNEWS_USE_AGY=0`(`0`/`false`/`off`/`no`)이고, 설정이 없거나 다른 값이면 켜진다. `run.py --agy`는 환경 변수로 꺼 둔 것을 한 번 다시 켠다(`--agy`와 `--no-agy`는 함께 쓸 수 없다). 값은 이미지 작업을 시작할 때마다 읽는다. 끈 경우에는 agy를 실행하지 않고(프로세스도, 대화 폴더 조회도 없음) 바로 3번의 Claude SVG(안 되면 4번 도형 도안)로 만든다. 이때 카드 버전의 `provenance.attempts`에 `{"provider": "agy", "outcome": "disabled", "reason": "agy 이미지 생성이 꺼져 있어 …(--no-agy 또는 CARDNEWS_USE_AGY=0으로 끈 설정)…"}`가 남고, 화면은 카드마다 "agy를 쓰지 않았습니다"를 표시하며 승인 화면에도 agy를 껐다는 안내가 나온다. 두 수단이 모두 실패했을 때의 카드 오류 메시지에는 꺼 둔 agy를 원인으로 쓰지 않는다. `GET /api/health`는 Claude 엔진에서 `agyEnabled`(켜짐 true)와 `checks.agy`(끄면 `disabled`, 켜면 `unknown`/`ok`/`image_failed`/`missing`/`auth_required`)를 돌려주고 모의 엔진 프로젝트에는 해당하지 않는다.
1. **(agy가 켜진 경우, 기본) agy 먼저**: `agy -p <프롬프트> --output-format json`을 인자 배열로 실행한다(작업 폴더 `cards/<카드>/v<n>/agy_work`). 프롬프트에는 이미지 역할·그림 계획·**공통 그림체와 팔레트(같은 값이 모든 카드에)**·"위쪽 30%는 배경만"·"글자·숫자·기호·로고·실제 사람·실제 제품 화면 금지"·"명령 실행 대신 이미지 도구만 사용"이 들어간다. 시간 제한 420초(`CARDNEWS_AGY_TIMEOUT`).
2. **agy 응답을 믿지 않는다**: 실제로 관찰한 동작 — JSON의 `status`가 `SUCCESS`여도 이미지가 없을 수 있다. 이미지는 하위 에이전트(`image-generator`)의 대화 폴더(`~/.gemini/antigravity-cli/brain/<id>/…`)에 만들어지고(작업 폴더로의 복사는 명령 실행 권한이 없어 거부됨), 이미지 도구는 4:5를 지원하지 않아 3:4 등으로 만든다. 그래서 작업 폴더·대화 폴더(하위 에이전트 포함)·응답에 적힌 경로에서 **이미지 파일을 직접 찾아 열어 보고**, 없으면 대화 기록(`transcript.jsonl`)의 이미지 도구 오류를 원인으로 쓴다(예: `500 Internal Server Error (10번 시도 모두 실패)`). 오류 기록이 없으면 거부된 요청과 agy 응답을 원인에 적는다.
3. **Claude SVG + 브라우저 캡처(기본 수단, `claude-svg`)**: agy가 꺼져 있거나 실패하면 Claude CLI(도구 없음, `--json-schema`)가 카드의 이미지 역할·그림 계획·공통 그림체·팔레트로 **SVG 요소 조각**을 쓴다. 앱은 이 조각을 그대로 믿지 않고 XML로 파싱해 검증한다(`app/svg_render.py`): 허용 요소는 `g path circle ellipse rect polygon polyline line defs linearGradient radialGradient stop`뿐이고, 허용 속성만 남기며(`style`·`href`·`on*`·글자 요소 `text`·`image`·`script`·`foreignObject`·`use`·주석·DOCTYPE 금지), 색은 `#RRGGBB`(스토리보드 팔레트의 가장 가까운 색으로 강제 변환)·`none`·같은 조각 안의 그라데이션 `url(#id)`만, 숫자·`d`·`points`·`transform`은 형식과 크기(절댓값 10000 이하)를 검사한다. 조각은 60,000자·요소 200개 이하, 그리는 요소는 5개 이상이어야 한다. 검증에 실패하면 모델에게 이유를 알려 한 번 고쳐 쓰게 하고(카드당 최대 2번 호출), 그래도 실패하면 이 카드는 4번으로 대체한다. 검증을 통과한 조각은 우리가 그린 배경 그라데이션 위의 `<g clip-path>`(y ≥ 30% 영역만) 안에 넣어 **위쪽 30% 여백은 항상 배경만 남게** 한다. 이 SVG 문서(`art.svg`로 저장)를 **Edge 또는 Chrome 헤드리스**(`--headless=new --screenshot`, 창 1080×1350, 임시 프로필 `--user-data-dir`로 사용자의 브라우저와 분리)로 캡처해 PNG를 얻는다. 브라우저는 `CARDNEWS_BROWSER`(경로) → Edge → Chrome 순서로 찾고(시험용 `CARDNEWS_BROWSER_CMD`), 캡처에는 90초 제한(`CARDNEWS_BROWSER_TIMEOUT`)과 취소 시 프로세스 종료가 있으며, 한 가지 색뿐인 빈 이미지는 실패로 본다. **브라우저를 찾지 못하면 Claude를 부르지 않고** 바로 4번으로 간다(호출 낭비 방지). 브라우저가 없거나 캡처에 실패하면 같은 실행의 나머지 카드는 SVG를 다시 시도하지 않고 4번을 쓴다.
4. **도형 도안 + Pillow(마지막 대체 수단, `claude-scene`)**: SVG를 쓸 수 없거나 검증을 통과하지 못하면 같은 카드를 Claude CLI(도구 없음, `--json-schema`)가 **도형 도안**(배경 그라데이션 + 사각형·둥근 사각형·타원·다각형·선, 좌표 0~1)으로 설계하고 Pillow(`app/scene_renderer.py`)가 그린다. 도안의 색은 스토리보드 팔레트로 강제로 맞추고(그림체 통일), 위쪽 30%는 배경만 남기도록 도형을 잘라 낸다. 글자는 그리지 않는다. 이 방식은 "Claude가 그림을 직접 생성한다"가 아니라 **Claude가 설계한 단순한 벡터풍 일러스트**임을 `만든 방식`에 그대로 표시한다.
(agy가 켜진 경우) **agy 실패 후 건너뛰기**: 같은 실행에서 agy가 한 번 실패하면 나머지 카드에서는 agy를 기다리지 않고 바로 대체 수단을 쓴다(agy 실패 하나에 최대 3분이 걸렸다). 다시 시도하면 agy를 다시 시도한다.
5. **저장·검사**: 어떤 방식이든 PNG 1080×1350으로 저장한다(비율이 다르면 덮어 맞춰 자르되 위쪽 여백에서 자른다. 원본 크기와 처리 내용은 `provenance`에 기록). 저장 뒤 파일을 다시 읽어 `checks`를 만든다 — `file`(존재·비어 있지 않음), `format`(PNG 서명), `size`(1080×1350), `margin`(위쪽 28%의 가로 밝기 편차 12 이하면 `pass`, 아니면 `warn`), `textInImage`(**항상 `unchecked`**: 이미지 안 글자는 자동으로 검사하지 못한다). 파일·형식·크기 검사가 실패하면 그 수단은 실패로 처리하고 다음 수단으로 간다.
6. **기록**: 카드 버전마다 `provenance.json`(만든 방식, 프롬프트, 시도 내역과 실패 원인, 원본 크기, 생성 시각, "생성 이미지입니다. 실제 사건이나 제품 화면이 아닙니다.", "저작권이 없다고 단정하지 않음")과 DB(`card_versions`)에 남긴다. SVG 방식은 `art.svg`, 도형 도안 방식은 `scene.json`도 남긴다(`provenance.tool`은 `claude-cli SVG + 브라우저` 또는 `claude-cli 도안 + Pillow`, SVG 방식은 `provenance.browser`에 쓴 브라우저 이름).
7. **실패와 재시도**: 카드마다 독립적이다. agy(켜진 경우)·SVG·도형 도안이 모두 실패하면 그 카드만 `failed`(원인 포함)이고 나머지는 계속 만든다. 작업이 끝나면 `review`, 작업을 취소·중단하면 `만드는 중`이던 카드는 `pending`으로 돌아가며 `review`에서 다시 만들 수 있다. 승인한 스토리보드는 바뀌지 않는다.

### 확인한 것 (2026-10-23, 이 PC)

**자동 테스트 65개 통과**(앞의 50개 + 새 15개: 모의 엔진 전체 흐름 6, 가짜 claude·가짜 agy 7, 파일 검사 2). 확인한 것: 형식별 카드 수와 승인 전 이미지 없음, 승인 뒤 카드별 PNG(1080×1350)·크기·서명·검사, 같은 팔레트 색만 쓰는 모의 이미지, 승인·재시도의 중복·오래된 버전 거절, 변경 요청의 새 버전과 이전 버전 보관, 재시도가 새 버전을 만들고 다른 카드는 그대로, 파일 엔드포인트의 경로 탈출 거절, 스토리보드 호출이 도구 없이 실행되고 지어낸 출처가 걸러짐, 가짜 agy 성공(3:4 원본이 1080×1350으로)·500 오류·깨진 PNG·agy 없음·시간이 오래 걸릴 때의 취소(프로세스 종료, 카드 `pending` 복귀), 두 수단이 모두 실패할 때 카드 실패와 재시도, 모든 카드 프롬프트에 같은 팔레트와 그림체가 들어감.

**실제 호출로 확인한 것** (임시 폴더의 서버, 실제 Claude CLI·agy):

| 항목 | 결과 |
|---|---|
| 스토리보드(실제 Claude, 이야기형) | 조사 → 질문 → 제안 뒤 7장 스토리보드가 약 25~45초에 만들어졌다. 장마다 핵심 문장·본문·이미지 역할·그림 계획·출처가 있고 모든 카드가 같은 팔레트·그림체를 공유했다. 첫 결과에서 마지막 카드가 마무리가 아니라 다른 소식을 소개해 프롬프트에 "마무리 카드는 새 소식을 소개하지 않는다"를 추가했고, 다시 만든 결과(v2)는 정리·다음 행동 카드가 됐다 |
| 승인 전 | 승인 전에는 이미지 작업·카드·폴더가 없었다 |
| agy 이미지 생성 | **성공을 확인하지 못했다.** 시험한 4번 모두 이미지가 만들어지지 않았다. 3번은 이미지 도구가 `500 Internal Server Error`를 돌려줬다(도구 호출 3번 실패 2회, 10번 실패 1회이며 마지막 시험은 183초 걸림). 1번(앱 실행 중 첫 카드)은 agy가 이미지 도구 대신 명령 실행을 시도해 거부된 채 끝났고, 프롬프트에 "명령 실행 금지"를 추가한 뒤 다시 시험하자 이미지 도구까지 갔지만 500이었다. 어느 경우도 agy 응답 상태는 `SUCCESS`였다 |
| (이전 방식) 대체: Claude 도형 도안 + Pillow | 카드 7장 모두 만들어졌다(78초). 7개 파일이 모두 PNG 서명·1080×1350·위쪽 여백 `pass`였고 카드 화면에서 모두 불러와졌다. 카드 1·5를 직접 열어 보니 같은 팔레트·그림체의 평평한 도형 일러스트였고 글자는 없었다 |
| 화면 | 브라우저에서 이미지 검수 화면이 7장 이미지·만든 방식·검사 결과·"대체 수단을 쓴 이유"를 보여 주고 가로 스크롤이 없었다 |
| 기존 데이터 | 내 `data/` 폴더를 복사해 새 코드로 열었더니 기존 프로젝트 2개가 그대로 열렸고(스키마가 자동으로 확장됨) 원본 폴더는 바뀌지 않았다 |

### 확인하지 못한 것 · 알려진 한계

- **agy가 실제로 이미지를 만드는 경우는 한 번도 보지 못했다.** 성공했을 때 이미지가 놓이는 위치는 가짜 agy로 가정한 것이다(실제 대화 폴더 구조를 근거로 작업 폴더·대화 폴더·응답 경로를 모두 뒤진다). 실제 agy 성공 시의 3:4 → 4:5 자르기와 품질은 미확인이다.
- **이미지 안에 글자가 없는지는 자동으로 확인하지 못한다.** 프롬프트로 금지하고 도안 방식은 도형만 그리지만, agy 이미지에 글자가 섞여도 앱은 알아채지 못한다(`textInImage: unchecked`).
- (도형 도안 방식) 그림은 단순한 도형 일러스트다. 사진·정교한 그림이 아니고, 그림의 의미(예: 허락을 구하는 로봇)가 얼마나 잘 전달되는지는 사람이 봐야 한다. 장면 구성이 어색한 카드는 "이미지 다시 만들기"로 새 버전을 만들 수 있다.
- 실제 실행은 이야기형 1건만 했다. **목록형, 변경 요청, 카드 재시도, 실제 agy 시간 초과·취소는 가짜 도구로만 확인했다.** 스토리보드를 다시 만들 때(v2) 형식 고쳐 쓰기가 한 번 쓰였다(실제 Claude 출력이 한 번 규칙을 어김).
- 스토리보드의 "한 장에 메시지 하나"와 "근거 없는 과장 금지"는 길이·문장 수·출처 URL·숫자 검사와 프롬프트로 지킨다. 문장이 사실 표현을 넘는지(예: "업무를 끊는다" 같은 해석)는 자동으로 잡지 못한다.
- (4단계 당시) 제목·본문 글자 렌더링, 글자 잘림·가독성 검사, 최종 승인, PNG/ZIP 내려받기는 아직 없었다. 이후 5단계에서 모두 구현됨.
- agy가 실패하면 첫 카드에서 최대 3분 기다린다(`CARDNEWS_AGY_TIMEOUT`로 줄일 수 있다).


### 11. SVG + 브라우저 캡처 (agy 다음 수단, 2026-10-23)

이미지 수단의 순서는 **agy(기본 켜짐, `--no-agy`로 끔) → Claude SVG + 브라우저 캡처 → Claude 도형 도안 + Pillow**다. 사용자 지시에 따라 Claude 쪽 기본 방식을 SVG로 바꾸고, **브라우저(Edge/Chrome)를 쓸 수 없거나 SVG가 검증을 통과하지 못하면 도형 도안으로 대체**한다(카드마다 독립). 이 장의 시험은 실제 agy를 호출하지 않았다(그 시점에는 agy가 꺼져 있었고, 안전장치로 agy 실행 파일 자리에는 가짜를 뒀으며 호출 기록이 없음을 확인했다). 이후 agy를 다시 기본 켜짐으로 되돌렸고 그 변경도 가짜 agy로만 시험했다.

**왜 SVG인가(같은 입력으로 비교, 카드 2장)**: 도형 도안은 각진 도형 조합이라 의미가 약했다(말풍선이 기기 위에 겹쳐 보이고, 로봇 팔·정지 손바닥 표지판이 알약 모양으로 그려짐). SVG는 곡선·그라데이션으로 로봇·표지판·열쇠·기기가 알아보게 그려졌다. 비용은 카드당 약 14~36초(도형 도안은 약 9초)다. 표본이 2장뿐이라 항상 더 낫다고는 할 수 없다.

**확인한 것**:

- **자동 테스트 93개 통과**(이전 72개 + 21개). 새로 확인한 것: SVG 검증(허용 요소·속성, `script`·`text`·`image`·`use`·`style`·`href`·`on*`·이름 색·`rgb()`·외부 url·잘못된 변환·너무 큰 숫자·과도한 길이/요소 수·XML 오류·주석·DOCTYPE·id 충돌·정의되지 않은 그라데이션 참조 거절, 팔레트 밖 색이 팔레트 색으로 바뀜), 문서 구조(배경과 `y=405` 클립), **실제 Edge**로 SVG를 1080×1350 PNG로 캡처하고 위쪽 여백에 놓인 어두운 도형이 잘려 나가는 것, 브라우저 탐색(환경 변수 우선, 없는 경로는 다른 걸 몰래 쓰지 않음), 가짜 Claude·가짜 브라우저로 기본 경로(agy 호출 0, 브라우저 인자 `--headless=new`·`--window-size=1080,1350`·`--user-data-dir`, 모든 카드에 같은 팔레트), 위험·잘못된 SVG 5종(script·이름 색·text·깨진 XML·Claude 호출 실패)의 도형 도안 대체와 고쳐 쓰기 1번, **브라우저 없음 → Claude를 부르지 않고 대체**, 빈 캡처·브라우저 종료·시간 초과(프로세스 종료 확인) → 대체하고 나머지 카드는 SVG를 다시 시도하지 않음, 크기가 다른 캡처의 정규화, **취소 시 브라우저 프로세스 종료와 카드 `pending` 복귀**.
- **실제 호출(실제 Claude CLI + 실제 Edge, 임시 폴더)**: 7장 스토리보드(모두 새 그림 계획)를 승인해 **7장이 141초에 모두 SVG 방식으로 만들어졌다**(대체 없음). 7개 모두 PNG·1080×1350·위쪽 여백 `pass`, 카드마다 15~60개 범위의 그리는 요소(22~43개), 모든 카드가 같은 팔레트·그림체, 글자 없음. 카드 화면에 "만든 방식 Claude SVG + 브라우저 캡처 (msedge)"와 "agy를 쓰지 않았습니다"가 표시됐다. 실행이 끝난 뒤 남은 Edge·claude·agy 프로세스는 없었다.

**확인하지 못한 것 · 한계**:

- 실제 실행에서는 SVG 검증 실패나 브라우저 실패가 한 번도 나오지 않아, **실제 모델이 내는 규칙 위반과 대체 동작은 가짜 도구로만 확인했다**.
- 모델이 y < 405 쪽으로 그린 부분은 클립으로 잘려 나가므로, 그런 도형(예: 카드 4의 해 모양 빛무리)은 가장자리가 수평으로 잘려 보일 수 있다. 위쪽 여백이 비는 것은 보장하지만 잘린 모양이 어색한지는 사람이 봐야 한다.
- 이미지 안에 글자가 없다는 것은 SVG 단계에서 `text` 요소를 막아 SVG 방식에서는 구조적으로 보장되지만(래스터 이미지 글자 검사가 아님), agy 이미지에는 여전히 자동 검사가 없다.
- Edge/Chrome이 없는 PC에서는 항상 도형 도안으로 만든다(`GET /api/health`의 `checks.browser: "missing"`). Windows 11의 Edge 외 환경(Chrome만 있는 PC, Linux/macOS)은 시험하지 않았다.
- 브라우저 캡처는 카드 하나에 몇 초가 걸리고 카드는 하나씩 순서대로 만든다. 여러 카드를 동시에 만드는 경우는 없다.


### 12. agy 다시 기본 켜짐 (2026-10-23)

사용자 결정("agy 다시 기본 설정 켜져 있는 걸로 두자. 이미지 만들기는 직접 시험해 볼게")으로 agy를 **기본 켜짐**으로 되돌렸다. 끄기: `run.py --no-agy` 또는 `CARDNEWS_USE_AGY=0`(위 10장 0번). 이미지 수단의 순서와 대체 규칙은 그대로다: agy → Claude SVG → Claude 도형 도안.

- **자동 테스트 94개 통과**. agy가 기본 켜짐이 되었으므로 어떤 시험도 실제 agy에 닿지 않게 **모든 시험의 기본 agy 명령을 "즉시 실패하는 가짜"로 바꿨고**(`tests/test_flow.py` 맨 위), agy를 쓰는 시험은 각자 `tests/fake_agy.py`로 다시 지정한다. 새로 확인한 것: 설정이 없으면 agy를 먼저 시도함(가짜 agy 7번 호출), `0`/`false`/`off`/`no`(공백·대소문자 무시)만 끔으로 읽고 그 밖의 값과 설정 없음은 켬, `--agy`와 `--no-agy`가 도움말에 있음, 끈 경우 agy 호출 0번과 `disabled` 기록·안내 문구(`--no-agy`와 `--agy`를 모두 알려 줌), 켠 경우의 상태(`agyEnabled: true`).
- **이번 변경에서 실제 agy를 호출하지 않았다.** 서버 상태 조회만 했다(아래).
- 이 변경 중 실제 Edge 시험에서 임시 브라우저 프로필 삭제가 간헐적으로 실패하는 것을 발견해(Windows에서 브라우저 보조 프로세스가 막 끝난 직후 파일이 잠김) 최대 5초 동안 다시 지우도록 고쳤다(`svg_render._remove_profile`). 이후 같은 시험을 3번 반복해 모두 통과했다.
- **주의**: agy를 켜 둔 채 이미지를 만들면 agy 사용 한도를 쓴다. 이전 시험에서 agy 이미지 도구는 4번 모두 이미지를 만들지 못했고(3번 500 오류, 1번 이미지 도구를 쓰지 않고 끝남) 그때마다 약 8천~10만 토큰이 들었다. 이 PC에서 agy가 실제로 이미지를 만드는 것은 아직 확인하지 못했다.
- **사용자 요청으로 실제 agy를 다시 시도했다(5번째, 2026-10-23)**: 임시 폴더에 카드 1장짜리 승인 대기 스토리보드를 만들고(사용 한도를 아끼려고 1장만), agy 기본 켜짐·실제 Claude·실제 Edge로 승인했다. agy는 첫 카드에서 177초 동안 이미지 도구를 9번 시도했지만 모두 `500 Internal Server Error`였고, 앱은 그 원인을 카드에 기록한 뒤 Claude SVG + Edge로 대신 만들었다(카드 1장, 189초, 1080×1350 PNG, 파일·형식·크기·위쪽 여백 `pass`). agy 대화 기록 기준 이 시도의 토큰은 입력 약 17.0만 + 출력 약 1.5만(캐시 읽기 4.1만 별도), `generate_image` 9번 전부 실패다. 지금까지 5번의 시도 누적은 입력 약 47.3만 + 출력 약 4.1만이고 이미지 도구 호출 25번이 모두 실패했다. **agy가 실제로 이미지를 만드는 경우는 여전히 확인하지 못했다.** 500의 원인(서비스 장애, 한도, 이 계정에서 이미지 생성 불가)은 구분하지 못했다.

### 13. 사용자 서버에서 agy를 "찾지 못했습니다"가 났던 문제와 6번째 시도 (2026-10-23)

- **발견**: 사용자가 직접 서버를 켜서 만든 프로젝트(`prj_1e1bf8e55d`, 목록형 스토리보드 v3, 7장)에서 이미지를 만들 때 agy가 "agy(Antigravity CLI)를 이 PC에서 찾지 못했습니다"로 실패했다. 실제로는 `%LOCALAPPDATA%\agy\bin\agy.exe`가 설치되어 있었다. 서버를 켠 터미널의 PATH에 agy가 없어 PATH만 보던 탐색이 실패한 것이다. 즉 **그 시도에서 agy는 한 번도 실행되지 않았다.**
- **수정**: 탐색 순서를 `CARDNEWS_AGY`(경로 지정) → PATH → 기본 설치 위치(`%LOCALAPPDATA%\agy\bin\agy.exe`)로 바꿨다(`images.find_agy`). `CARDNEWS_AGY`를 지정했는데 파일이 없으면 다른 것을 몰래 쓰지 않고 없는 것으로 본다. 시험(`AgyDiscoveryTests`)은 agy를 실행하지 않는다.
- **같이 고친 것**: 카드를 골라(`cardIds`) 다시 만들어도, 앞서 취소해서 `pending`으로 남은 다른 카드까지 함께 만들어졌다. 이제 작업이 `params.cardIds`를 가지고 고른 카드만 만든다. `cardIds` 없이 "만들지 못한 카드 다시 만들기"를 누르면 대기·실패 카드를 모두 만든다(`test_retry_of_chosen_cards_does_not_build_other_pending_cards`). 이 수정 전에 카드 3만 다시 만들려던 시도가 대기 중이던 6장을 한꺼번에 만들었다.
- **6번째 agy 시도(사용자 요청, 사용자 프로젝트)**: agy가 실행됐고 이미지 도구가 7번 모두 `500 Internal Server Error`였다(163초). 나머지 카드는 agy를 건너뛰고 Claude SVG + Edge로 만들었다. 프로젝트의 카드 7장이 모두 `ready`(1080×1350, 검사 `pass`)다. agy 기록 기준 토큰은 입력 약 20.4만 + 출력 약 1.2만(캐시 읽기 14.2만 별도)이고, 6번 시도 누적은 입력 약 67.6만 + 출력 약 5.3만, `generate_image` 32번이 모두 실패했다. **agy가 이미지를 실제로 만든 적은 아직 없다.**

- **agy 그림 위치(사용자가 직접 확인한 사실)**: agy는 응답이 `SUCCESS`여도 그림을 작업 폴더가 아니라 `~/.gemini/antigravity-cli/brain/` 아래 가장 최근 폴더에 JPG로 저장한다. 앱은 작업 폴더 → 대화 폴더(하위 에이전트 포함) → 이번 실행 뒤 새로 바뀐 brain 하위 폴더(최신순 5개) → 응답에 적힌 경로 순으로, **이번 실행 시작 이후에 생긴** png/jpg/webp만 찾아 가져온다(옛 그림은 고르지 않음). 가져온 파일은 1080×1350 PNG로 맞춘다. 시험은 agy를 실행하지 않는다(`AgyBrainFolderTests`).

### 14. 글자 올리기와 모바일 미리보기 (2026-10-23)

그림(`v<n>/image.png`)에는 글자가 없고, 제목·본문은 Pillow로 따로 올려 `v<n>/final.png`(1080×1350)로 저장한다. 그림 파일과 승인한 스토리보드는 바뀌지 않는다. 글꼴은 Windows 맑은 고딕(`malgun.ttf`/`malgunbd.ttf`)을 쓰고, 없으면 나눔·Noto CJK를 찾는다. 모두 없거나 한글이 안 되면 글자를 깨뜨려 그리지 않고 `typeset_unavailable`로 알린다(`CARDNEWS_FONT`, `CARDNEWS_FONT_BOLD`로 지정).

| 요청 | 설명 |
|---|---|
| `GET /api/projects/{id}/cards/{cardId}/typeset` | 저장한 배치(없으면 스토리보드의 title·body와 기본값), 그림 주소, 저장 여부 |
| `POST …/typeset/preview` `{layout}` | 저장하지 않고 `preview.png`만 만든다. 프로젝트 상태·버전은 바뀌지 않는다 |
| `PUT …/typeset` `{layout}` | `final.png`를 만들고 파일을 다시 열어 PNG·크기를 확인한 뒤 `layout.json`에 배치와 검사 결과를 저장한다. 같은 요청을 다시 보내도 같은 결과 |

`layout`: `title`(≤120자), `body`(≤400자), `titleSize`(40~120, null=자동 맞춤), `bodySize`(28~64, null=자동), `color`(auto/light/dark), `scrim`(본문 바탕 띠 농도 0~90), `bodyAnchor`(bottom/center). 이미지가 준비된 카드(`ready`)이고 단계가 `review`일 때만 가능하다(아니면 409 `wrong_stage`/`image_not_ready`).

응답의 `checks`는 항목별 `pass/warn/fail`이다: `font`, `titleFit`(제목 잘림), `bodyFit`(본문 잘림·제목 자리 침범), `overlap`(제목이 위쪽 빈 영역 30%를 넘어 그림과 겹침), `titleContrast`(≥3.0), `bodyContrast`(≥4.5, 글자가 칠해지는 자리의 배경 하위 5% 기준), `phone`(폭 390px에서 제목 ≥20px, 본문 ≥13px). 자동 맞춤은 영역에 들어가는 가장 큰 크기(제목 56~88, 본문 36~46)이고, 사용자가 정한 크기가 영역을 넘으면 자르지 않고 `fail`로 알린다. 저장은 `fail`이 있어도 되지만 화면에 "고칠 곳 있음"으로 남는다.

카드 상태(`GET /api/projects/{id}`)에 `finalUrl`, `typeset{ok, savedAt, checks}`가 추가됐다. 그림을 다시 만들면(새 버전) 예전 합성본은 새 그림의 것으로 보지 않아 `finalUrl`이 null이고, 이전 버전 파일은 보관된다.

한계: 글자 위치는 제목=위쪽 빈 영역, 본문=아래쪽 또는 가운데쪽 바탕 띠 두 가지뿐이다(자유 배치 없음). 이미지 속 그림과 본문 띠가 겹치는 것은 띠 농도로 가리는 방식이며 그림 내용을 인식해 피하지는 않는다.

- **그림 구도(2026-10-23 변경)**: 그림이 아래쪽에 쏠려 본문 글자와 겹치던 문제로, 중심 피사체를 **가운데(세로 30~70%)**에 모으고 위쪽 30%(제목)와 아래쪽 약 29%(본문)를 배경만 남기게 바꿨다. SVG는 y 405~958만 남기고 잘리며(`svg_render.BOTTOM_Y`), 도형 도안도 같은 범위로 자른다(`scene_renderer.MARGIN_BOTTOM`). agy·Claude 프롬프트에도 같은 규칙을 넣었다. 이미 만든 스토리보드의 `imagePlan`에 "아래 70%"라고 적혀 있어도 SVG·도안 프롬프트의 이 규칙이 우선한다.

### 15. 카드 하나만 고치기 · 최종 승인 · 내보내기 · 내보낸 파일 확인 (2026-10-23)

**카드 수정 요청** `POST /api/projects/{id}/revisions` `{target:"cards", cardIds:[한 장], instruction(1~500자), version, requestId}` — `review` 단계, 이미지가 `ready`인 카드만. 한 번에 한 장(`cardIds`가 1개가 아니면 400). 작업(`kind: card_revision`)이 `producing`으로 돌아가며 Claude CLI(`--tools ""`, sonnet/medium)가 요청을 해석한다.
- `kind:"text"`(제목·본문 요청): 새 title/body를 받아 **기존 그림 그대로** 글자만 다시 올려 `final.png`를 갱신한다. 요청한 문장 수("한/두/세 문장")를 검증하고, 원문에 있던 숫자가 빠졌거나 원문·출처에 없는 숫자가 생기면 `notes`로 알린다.
- `kind:"image"`(그림 요청): 그 카드의 그림 계획(`overrides.json`)만 바꿔 **그 카드만** 새 그림 버전으로 다시 만들고, 저장해 둔 글자 배치를 새 그림 위에 다시 올린다. 이전 버전 파일은 보관한다.
- 다른 카드와 스토리보드는 바뀌지 않는다. 기록은 `cards/<카드>/revisions.jsonl`(요청, 종류, 고치기 전·후)과 카드 상태의 `revisions`에 남는다.
- 실패하면 `review`로 돌아가고 카드는 그대로다.

**최종 승인** `POST /approve {stage:"final", version, requestId}` — 모든 카드가 `ready`이고 글자를 올렸으며(합성본이 현재 그림 버전의 것) 글자 검사에 `fail`이 없어야 한다. 아니면 `409 final_not_ready`와 `detail.blockers`(카드별 사유). 승인은 지금 합성본들의 sha256 서명을 저장한다. **카드의 글자·그림이 바뀌면 서명이 달라져 승인이 자동으로 풀리고**(`finalApproval.stale: true`) 다시 승인해야 한다. 상태: `finalApproval{approved, resultVersion, approvedAt, stale, ready, blockers}`.

**내보내기** `POST /exports {version, requestId}` — 최종 승인 상태에서만(아니면 `409 not_approved`). `data/projects/<id>/exports/ex_<시각>/`에 쓴다: `cards/card_01.png…`(합성본 복사), `cardnews.zip`, `manifest.json`, `sources.json/.md`(조사 기준·기간, 선택한 소식과 발행일, 카드별 근거 URL), `image_usage.json/.md`(만든 방식·시도 기록·프롬프트·파일 sha256·생성 이미지 표시·저작권 문구), `verification.json`. `GET /exports`는 최근 내보내기 목록, `GET /exports/{exportId}/{name}`은 허용된 파일만 내려준다(그 밖의 이름·`..`는 404).

**내보낸 파일 기준 확인** — 내보낸 폴더의 manifest와 파일만 읽어 항목별 `pass / fail / unchecked`를 낸다. **`unchecked`는 통과가 아니며** `userAction`(내가 확인할 일)을 함께 준다. `allPassed`는 실패와 직접 확인 항목이 0일 때만 true.
| 분류 | 앱이 확인하는 것(pass/fail) | 확인하지 못해 `unchecked`로 남기는 것 |
|---|---|---|
| 누락 | 카드 수·번호, PNG가 열리는지·1080×1350·sha256, 출처/이미지 사용 기록 파일과 카드별 항목, ZIP 안 파일·손상·내용 | PDF(범위 밖) |
| 글자 잘림 | 기록된 글자 검사(잘림·그림과 겹침·대비·휴대폰 크기)에 실패가 없는지, 저장된 배치로 같은 PNG를 다시 만들어 일치하는지 | 글자 인식(OCR)으로 읽어 보지 못함, 실제 휴대폰에서의 가독성 |
| 출처 | 카드마다 출처 URL이 있는지, 이번 조사에서 연 자료의 주소인지, 자료에서 못 찾은 숫자 표시 | 출처 페이지를 열어 카드 문장과 대조하는 것, 사용자가 고친 문장이 출처 범위를 넘지 않는지, 자료에서 못 찾은 숫자 |
| 날짜 | 조사 기준·기간 기록, 선택한 소식의 발행일이 기간 안인지, '최신' 사용 허용 여부, 조사 당일 내보냈는지 | 발행일이 확인되지 않은 소식, 카드 글 속 날짜, 원문 페이지의 실제 발행일, 조사 뒤 지난 시간 |

한계: 앱은 웹 페이지를 다시 열어 사실을 대조하지 않는다(그건 사용자 몫). 글자 잘림은 계산한 배치와 재현 일치로만 확인한다.

- **글자 배치 보강(2026-10-23)**: (1) 숫자와 바로 뒤의 짧은 한글 단위("1억 쌍", "5% 증가")는 줄 끝에서 갈라지지 않는다. (2) 제목에 `:` 또는 `—`가 있으면 그 자리에서 두 줄로 끊는다(앞뒤가 각각 한 줄에 들어가는 가장 큰 글자 크기, 최소 64px). 없으면 줄 길이가 고르게 되도록 끊는다. (3) 새 검사 `bandOverlap`: 본문 바탕 띠 아래에 그림이 12px 넘게 깔려 있으면 `warn`(저장·승인은 막지 않음). 글자 배치 코드가 바뀌었으므로 이전 코드로 저장·내보낸 합성본은 "내보낸 파일 확인"에서 같은 PNG를 다시 만들 수 없어 `reproduce`가 `unchecked`가 될 수 있다(글자를 다시 저장하고 새로 내보내면 해결).

- **이전 그림으로 되돌리기(2026-10-23)**: `POST /api/projects/{id}/cards/{cardId}/restore` `{imageVersion, version, requestId}` — `review` 단계, 이미지가 `ready`인 카드, 보관된 다른 버전만. 그림을 새로 만들지 않으므로 엔진(Claude·agy)을 부르지 않는다. 카드의 현재 버전만 바꾸고, 저장해 둔 글자 배치를 그 그림 위에 다시 올리며(배치가 없으면 글자 없는 상태), 합성본이 바뀌어 최종 승인은 풀린다. 다른 카드는 바뀌지 않는다. 수정 기록에 `kind: "restore"`가 남는다. 카드 상태의 `versions[]`에 `imageUrl`, `current`가 추가됐다. 되돌린 뒤 새로 만들면 현재 번호가 아니라 **가장 큰 번호 다음**을 새 버전으로 쓴다(번호가 겹치지 않음).

- **그림 수정 요청은 기존 SVG를 고친다(2026-10-23)**: 수정 요청(kind: image)은 이전에는 요청을 글 계획으로 바꿔 그림을 처음부터 다시 그려서 그대로 두고 일부만 고쳐 줘가 지켜지지 않았다. 이제 현재 그림이 SVG(rt.svg)로 만든 것이면 그 SVG 조각과 사용자의 요청 원문을 모델에게 보여 주고 요청한 부분만 고친 전체 조각을 받는다(SVG_EDIT_PROMPT, 기존과 같은 검증·팔레트 규칙). 이 방식은 수정 요청 작업에서만 쓰고(overrides.json의 edit, 만든 뒤 지움) 그냥 다시 만들기는 처음부터 그린다. 수정 방식으로 만들 때는 agy를 쓰지 않는다. agy·도형 도안으로 만든 그림은 SVG가 없어 기존처럼 새로 그린다. 실제 Claude로 문서 더미 카드에 '형식·개수·색·배치는 두고 모서리만 정리' 요청을 했을 때 구도·개수·색이 유지되고 그림자만 조금 바뀌었다.

- **내보내기 출처 기록의 후보 단계 메모(2026-10-23)**: `sources.json/.md`의 선택한 소식에 있던 "불확실한 점"은 후보 조사 때(원문을 읽기 전)의 메모라서, 심층 조사에서 원문(GitHub 등)을 읽은 뒤에도 "원문을 직접 확인하지 못했다"로 남아 카드 근거와 모순됐다. 이제 `candidateStageNote`로 이름을 바꾸고 "후보 조사 단계의 메모(심층 조사 전의 상태, 이후 달라졌을 수 있음)"로 표시하며, 심층 조사에서 `confirmed`로 읽은 원문 주소는 `readOriginals`와 "심층 조사에서 직접 읽어 확인한 원문" 절에 따로 적는다. 이미 내보낸 폴더는 바뀌지 않으니 새로 내보내야 반영된다.

- **조사 뒤 지난 시간은 실제 시간으로(2026-10-24)**: 내보낸 파일 확인의 `날짜/age`가 달력 날짜 차이로 판단해서, 밤 9시에 조사하고 자정을 넘겨 새벽 0시에 내보내면 3시간밖에 안 지났는데도 "조사한 지 1일"로 직접 확인 필요가 됐다. 이제 조사 기준 시각부터 내보낸 시각까지 실제 시간을 재서 24시간 이내(`FRESH_HOURS`)면 통과, 넘으면 "N시간(또는 N일)이 지났다"로 직접 확인 필요로 둔다.
