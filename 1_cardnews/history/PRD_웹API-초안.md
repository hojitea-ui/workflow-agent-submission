# PRD 웹 API 계약 초안 (옮겨 둔 부분)

`../PRD.md`에서 옮긴 초안 표입니다. 현재 명세는 `../API_SPEC.md`입니다.

## 웹 API 계약 초안
아래는 필수 의미를 담은 초안이다. 구현 언어에 맞춰 상세 타입과 오류 응답을 API_SPEC.md에 확정한다.

| 요청 | 입력 | 결과 |
|---|---|---|
| POST /api/projects | topic, optional period | projectId |
| POST /api/projects/{id}/research | requestId | runId, status |
| GET /api/projects/{id} | 없음 | 후보·선택·상태·질문·산출물 |
| POST /api/projects/{id}/selection | candidateIds, version | 저장된 선택 |
| POST /api/projects/{id}/answers | questionId, version, answer, requestId | 수락 여부·현재 상태 |
| POST /api/projects/{id}/approve | stage(storyboard/final), version, requestId | 제작 작업 ID 또는 최종 승인 상태 |
| POST /api/projects/{id}/revisions | cardIds, instruction, version, requestId | 수정 작업 ID |
| POST /api/runs/{id}/cancel | 없음 | 취소 상태 |
| GET /api/projects/{id}/exports | 없음 | 검증된 다운로드 목록 |
