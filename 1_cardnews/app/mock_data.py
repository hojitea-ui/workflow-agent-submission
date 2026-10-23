"""모의 데이터. 실제 검색 결과가 아니며 화면에 모의 데이터로 표시된다.

예약된 주제어(모의 시나리오):
- 주제에 "부족"이 들어가면 7일 조사에서 후보가 3개만 나온다(30일로 넓히기 시나리오).
- 주제에 "실패"가 들어가면 첫 조사만 검색 실패로 끝나고 재시도하면 성공한다.
"""
from datetime import date, timedelta

# (제목, 분류, 게시 경과일, 사건 경과일, 요약, 선택 가치, 불확실성, 묶인 기사 수)
_TEMPLATES = [
    ("[모의] 새 언어 모델 공개 발표", "model", 1, 1,
     "새 언어 모델이 공개됐다는 모의 후보입니다.", "독자 관심이 높은 모델 소식입니다.", "성능 수치는 업체 주장이며 독립 검증 전입니다.", 3),
    ("[모의] 문서 작성 도우미 기능 업데이트", "product", 2, 2,
     "문서 작성 제품에 새 기능이 추가됐다는 모의 후보입니다.", "일반 사용자가 바로 써 볼 수 있습니다.", "일부 지역에서만 제공될 수 있습니다.", 2),
    ("[모의] 추론 성능 비교 논문 공개", "research", 3, 5,
     "여러 모델의 추론 성능을 비교한 논문이라는 모의 후보입니다.", "공식 논문이라 근거가 단단합니다.", "평가 방식이 달라 결과 해석에 주의가 필요합니다.", 1),
    ("[모의] 코드 에디터용 개발 도구 정식 출시", "devtool", 3, 3,
     "개발 도구가 정식 출시됐다는 모의 후보입니다.", "개발자 독자에게 유용합니다.", "기업용 요금제 조건은 확인되지 않았습니다.", 2),
    ("[모의] 이미지 생성 모델 이용 조건 변경", "product", 4, 4,
     "이미지 생성 모델의 이용 조건이 바뀌었다는 모의 후보입니다.", "저작권과 이용 범위를 알려 줄 수 있습니다.", "약관 해석이 갈릴 수 있습니다.", 1),
    ("[모의] 음성 비서 신기능 공개 예정 예고", "product", 5, None,
     "신기능이 공개 예정으로 예고됐다는 모의 후보입니다.", "곧 달라질 변화를 미리 알릴 수 있습니다.", "공개 예정일 뿐 출시된 기능이 아닙니다.", 2),
    ("[모의] 오픈소스 에이전트 프레임워크 새 버전", "devtool", 6, 6,
     "오픈소스 프레임워크의 새 버전이 나왔다는 모의 후보입니다.", "개발자 커뮤니티 반응을 보여 줍니다.", "변경 사항 일부가 문서에 아직 없습니다.", 1),
    ("[모의] 교육용 AI 활용 가이드라인 발표", "other", None, None,
     "교육 기관이 활용 가이드라인을 발표했다는 모의 후보입니다.", "교육자 독자에게 맞습니다.", "게시일을 확인하지 못했습니다.", 1),
    ("[모의] 로봇 학습 데이터셋 공개", "research", 12, 14,
     "대규모 학습 데이터셋이 공개됐다는 모의 후보입니다.", "연구 흐름을 짚어 줍니다.", "라이선스 조건 확인이 필요합니다.", 1),
    ("[모의] 번역 모델 지원 언어 확대", "model", 19, 19,
     "번역 모델의 지원 언어가 늘었다는 모의 후보입니다.", "한국어 사용자에게 의미가 있습니다.", "품질 평가는 공개되지 않았습니다.", 2),
    ("[모의] 보안 점검 도구 공개", "devtool", 26, 26,
     "보안 점검 도구가 공개됐다는 모의 후보입니다.", "개발 도구 다양성을 채웁니다.", "독립 검증 결과가 없습니다.", 1),
]
_WITHIN_7_DAYS = 8  # 위 목록에서 앞의 8개는 최근 7일 안(게시일 미확인 1개 포함)


def build_candidates(topic: str, days: int, today: date) -> tuple[list[dict], str | None]:
    count = _WITHIN_7_DAYS if days == 7 else len(_TEMPLATES)
    note = None
    if "부족" in topic and days == 7:
        count = 3
        note = "조사 기간 7일 안에서 후보가 3개뿐입니다. 30일로 넓힐까요? (모의 시나리오)"
    candidates = []
    for n, t in enumerate(_TEMPLATES[:count], start=1):
        title, category, age, event_age, summary, why, uncertainty, grouped = t
        candidates.append({
            "id": f"cand_{n}",
            "title": title,
            "summary": f"'{topic}' 주제용 모의 후보. {summary}",
            "publishedAt": (today - timedelta(days=age)).isoformat() if age is not None else None,
            "eventDate": (today - timedelta(days=event_age)).isoformat() if event_age is not None else None,
            "dateStatus": "confirmed" if age is not None else "unconfirmed",
            "category": category,
            "sources": [{"title": f"모의 출처 {n}", "url": f"https://example.com/mock/{n}", "publisher": "example.com (모의)"}],
            "whySelect": why,
            "uncertainty": uncertainty,
            "groupedCount": grouped,
        })
    return candidates, note


AUDIENCE_OPTIONS = [
    {"id": "general", "label": "일반 사용자", "description": "쉬운 말과 일상 활용 중심으로 풀어 씁니다."},
    {"id": "developer", "label": "개발자", "description": "도구·버전·기술 변화 중심으로 씁니다."},
    {"id": "educator", "label": "교육자", "description": "수업 활용과 주의점 중심으로 씁니다."},
]
AUDIENCE_RECOMMENDATION = {
    "optionId": "general",
    "reason": "선택한 후보가 제품·모델 소식 위주라 폭넓은 독자에게 맞고 설명 부담이 적습니다. (모의 추천)",
}


def build_notes_and_proposal(topic: str, selected: list[dict], answer: dict, window_days: int, basis_date: date):
    labels = {o["id"]: o["label"] for o in AUDIENCE_OPTIONS}
    free = (answer.get("freeText") or "").strip()
    ids = answer.get("optionIds") or []
    audience = free or "、".join(labels.get(i, i) for i in ids)
    chosen_list_format = "developer" in ids and not free
    n = len(selected)
    window_start = basis_date - timedelta(days=window_days)
    all_dated = all(
        c["dateStatus"] == "confirmed" and window_start <= date.fromisoformat(c["publishedAt"]) <= basis_date
        for c in selected
    )
    notes = {
        "facts": [{"text": f"{c['title']}: {c['summary']} (모의, 원문 미확인)",
                   "sourceUrl": c["sources"][0]["url"], "status": "unverified"} for c in selected],
        "sources": [s for c in selected for s in c["sources"]],
        "limits": ["모의 데이터라 원문을 읽지 않았습니다. 실제 조사는 Claude CLI 엔진으로 실행하세요."]
                  + [f"{c['title']}: {c['uncertainty']}" for c in selected],
        "conflicts": [],
    }
    proposal = {
        "audience": audience,
        "whyUseful": "선택한 소식이 독자의 일상이나 작업에 어떤 변화를 주는지 한눈에 보여 줄 수 있습니다. (모의)",
        "format": "list" if chosen_list_format else "story",
        "formatComparison": [
            {"format": "list", "fit": "소식을 빠르게 훑고 싶은 독자에게 맞음", "cards": n + 2,
             "evidenceDifficulty": "발표마다 출처 1개 이상 필요"},
            {"format": "story", "fit": "AI를 처음 접해 변화의 의미가 궁금한 독자에게 맞음", "cards": 6,
             "evidenceDifficulty": "한계 카드까지 근거를 이어야 해서 다소 높음"},
        ],
        "formatRecommendation": {
            "format": "list" if chosen_list_format else "story",
            "reason": ("개발자 독자는 항목을 빠르게 훑는 편이 효율적입니다. (모의 추천)" if chosen_list_format
                       else "처음 접하는 독자에게는 상황에서 한계까지 이어지는 이야기 흐름이 이해하기 쉽습니다. (모의 추천)"),
        },
        "freshnessBasis": {
            "researchBasis": basis_date.isoformat(),
            "publishedAt": [c["publishedAt"] for c in selected],
            "titleMayUseLatest": all_dated,
        },
        "hooks": [
            f"[모의] {selected[0]['title'].replace('[모의] ', '')}, 무엇이 달라졌나",
            "[모의] 이번 주 소식, 확인된 것만 골라 봤어요",
            "[모의] 공개된 것과 예고된 것을 구분해서 알려 드립니다",
        ],
    }
    return notes, proposal


MOCK_STYLE = {"palette": ["#0B1F3A", "#1E5FD8", "#7FB3FF", "#EAF2FF", "#FFB84D"],
              "artStyle": "단색 도형으로 그린 평평한 설명 일러스트(모의)", "mood": "차분하고 신뢰감 있는 분위기(모의)"}


def build_storyboard(topic: str, selected: list[dict], fmt: str, hook: str) -> dict:
    """모의 스토리보드(claude_parse.validate_storyboard를 통과하는 형태). 선택한 후보의 모의 요약만 쓴다."""
    def ev(c):
        return [{"url": c["sources"][0]["url"], "note": c["title"][:60]}]

    def card(title, keyline, body, role, plan, cand=None):
        return {"title": title, "keyline": keyline, "body": body, "imageRole": role, "imagePlan": plan,
                "evidence": ev(cand or selected[0])}

    plan = "위쪽 30%와 아래쪽 29%는 비우고 가운데에 중심 도형 하나(모의)"
    cards = [card(hook[:28], "[모의] 이번 소식에서 확인된 것만 골라 봤습니다.", "[모의] 모의 데이터로 만든 표지입니다. 실제 조사 결과가 아닙니다.", "표지 분위기를 보여 주는 설명 그림", plan)]
    if fmt == "list":
        for c in selected:
            cards.append(card(c["title"].replace("[모의] ", "")[:28], f"[모의] {c['whySelect']}", f"[모의] {c['summary'][:100]}", "소식 한 가지를 상징하는 설명 그림", plan, c))
        cards.append(card("아직 확인 안 된 것", "[모의] 모든 내용이 확인된 것은 아닙니다.", "[모의] " + selected[0]["uncertainty"], "한계를 보여 주는 설명 그림", plan))
        if len(cards) < 4:
            cards.insert(-1, card("독자에게 의미", "[모의] 독자에게 달라지는 점을 정리합니다.", "[모의] 모의 설명입니다.", "의미를 보여 주는 설명 그림", plan))
    else:
        steps = [("상황", "[모의] 지금 상황을 먼저 짚어 봅니다."), ("불편", "[모의] 기존 방식에는 불편이 있었습니다."),
                 ("변화", "[모의] 이번 발표가 바꾸는 점이 있습니다."), ("활용", "[모의] 독자가 써 볼 수 있는 방법입니다."),
                 ("한계", "[모의] 아직 확인되지 않은 부분도 있습니다.")]
        for t, k in steps:
            cards.append(card(t, k, f"[모의] {selected[0]['summary'][:100]}", f"{t} 단계를 보여 주는 설명 그림", plan))
    cards.append(card("정리와 다음 행동", "[모의] 확인된 내용만 기억하세요.", "[모의] 원문 링크에서 직접 확인해 보세요.", "마무리를 보여 주는 설명 그림", plan))
    return {"style": MOCK_STYLE, "cards": cards}
