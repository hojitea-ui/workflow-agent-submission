# 출처 — AI 최신소식

- 조사 기준 시각: 2026-10-23T21:17:04+09:00
- 조사 기간: 2026-10-16 ~ 2026-10-23 (7일)

## 선택한 소식

- **DeepSeek, 추론 프레임워크 DeepInfer 오픈소스 공개** — 발행일 2026-10-20 (confirmed)
  - DeepSeek open-sources DeepInfer, its V4.1-Flash serving stack https://aiweekly.co/alerts/deepseek-open-sources-deepinfer-its-v41-flash-serving-stack
  - AI News for October 20, 2026 https://aiweekly.co/ai-news-today/edition/2026-10-20
  - 후보 조사 단계의 메모(심층 조사 전의 상태, 이후 달라졌을 수 있음): GitHub 원문을 직접 확인하지 못했다. 성능 수치는 확인되지 않았다.

## 심층 조사에서 직접 읽어 확인한 원문

- https://github.com/deepseek-ai/DeepInfer

## 카드별 근거

### 1. 최신: DeepInfer, Apache 2.0 공개
- https://github.com/deepseek-ai/DeepInfer — 2026-10-20(보도 기준) Apache 2.0 라이선스로 GitHub에 공개됐다.
- https://aiweekly.co/alerts/deepseek-open-sources-deepinfer-its-v41-flash-serving-stack — DeepInfer 오픈소스 공개를 다룬 보도.

### 2. DeepInfer, 지원 범위
- https://github.com/deepseek-ai/DeepInfer — 지원 모델, 지원 하드웨어, 요구 환경, 배치 불변 추론·결정론적 샘플링의 NVIDIA 전용 제한이 README에 명시돼 있다.

### 3. 독자에게 의미: 빠진 기능
- https://github.com/deepseek-ai/DeepInfer — KV 캐시 시스템, Prefill/Decode 분리, 복제본 간 로드 밸런싱이 이번 릴리스에 포함되지 않았음이 확인된다.

### 4. 아직 확인되지 않은 것
- https://aiweekly.co/alerts/deepseek-open-sources-deepinfer-its-v41-flash-serving-stack — 155T 토큰, 96%, 초당 300토큰은 보도된 수치이며 DeepSeek 자체 수치로 독립 검증이 없다.
- https://github.com/deepseek-ai/DeepInfer — README에는 Pareto 그래프 이미지만 있고 본문에 구체 수치가 없다.

### 5. 다음 행동: 원문부터 확인
- https://github.com/deepseek-ai/DeepInfer — 공개 범위, 지원 모델·하드웨어, 미포함 기능의 원문.

## 한계

- 성능 수치(155T 토큰, 96%, 초당 300토큰)는 DeepSeek 자체 수치이며 독립 검증이 없어 카드에 단정해 쓰면 안 된다.
- KV 캐시·Prefill/Decode 분리·로드 밸런싱이 빠져 있어 이것만으로 완전한 서비스 스택은 아니다.
- vLLM 기반 여부, Ascend 사전학습 계획, deepseek-recipe는 2차 보도로만 확인했다.
- GitHub 별·포크 수는 기사마다 달라 사용하지 않는다.

