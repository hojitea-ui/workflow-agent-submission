"""테스트용 가짜 claude. 실제 모델을 부르지 않고 stream-json 형식으로 시나리오를 흉내 낸다.
주제에 들어 있는 낱말로 시나리오가 정해진다. 호출 기록은 FAKE_CLAUDE_LOG 파일에 한 줄씩 남는다."""
import json
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

argv = sys.argv[1:]
prompt = sys.stdin.read()
today = datetime.now(timezone(timedelta(hours=9))).date()
day = lambda n: (today - timedelta(days=n)).isoformat()  # noqa: E731

resume_id = argv[argv.index("--resume") + 1] if "--resume" in argv else None
session = resume_id or f"sess-{uuid.uuid4().hex[:8]}"
log = os.environ.get("FAKE_CLAUDE_LOG")
if log:
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"argv": argv, "pid": os.getpid(), "resume": resume_id, "cwd": os.getcwd(),
                            "prompt": prompt[:6000]}, ensure_ascii=False) + "\n")


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False), flush=True)


KEYS = ["재시작시험", "심층실패", "재조사실패", "괄호누락", "검색불가", "검색오류", "로그인", "형식오류", "형식복구", "오래걸림", "시간초과", "질문없음", "세션없음"]
_found = [k for k in KEYS if k in prompt]
if _found:  # 주제가 들어 있는 첫 호출에서 시나리오 낱말을 기록해 두고, 주제가 없는 후속 호출(--resume)에서 다시 읽는다
    open("fake_words.txt", "w", encoding="utf-8").write(" ".join(_found))
elif os.path.exists("fake_words.txt"):
    _found = open("fake_words.txt", encoding="utf-8").read().split()


def has(word):
    return word in prompt


def kw(word):
    return word in _found


def bump(name):
    """같은 주제로 몇 번째 호출인지 센다(FAKE_STATE_DIR에 저장). 첫 번째만 실패하는 시나리오에 쓴다."""
    import hashlib
    import re
    m = re.search(r"주제\(JSON 문자열\): (\".*\")", prompt)
    key = hashlib.sha1((m.group(1) if m else "").encode("utf-8")).hexdigest()[:10]
    d = os.environ.get("FAKE_STATE_DIR") or os.getcwd()
    path = os.path.join(d, f"{name}_{key}.count")
    n = int(open(path).read()) + 1 if os.path.exists(path) else 1
    open(path, "w").write(str(n))
    return n


def tool(name, inp, result_text, is_error=False, tid=None):
    tid = tid or f"t_{uuid.uuid4().hex[:6]}"
    emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]}})
    emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "content": result_text,
                                                   **({"is_error": True} if is_error else {})}]}})


def finish(text, is_error=False, code=0, structured=None):
    out = {"type": "result", "subtype": "error" if is_error else "success", "is_error": is_error,
           "result": text, "session_id": session}
    if "--json-schema" in argv and not is_error:
        if structured is None:  # 진짜 CLI처럼, 텍스트가 올바른 JSON이면 structured_output으로도 돌려준다
            body = text.strip()
            if body.startswith("```"):
                body = body.split("\n", 1)[1].rsplit("```", 1)[0]
            try:
                structured = json.loads(body)
            except ValueError:
                structured = None
        if structured is not None:
            out["structured_output"] = structured
    emit(out)
    sys.exit(code)


emit({"type": "system", "subtype": "init", "session_id": session, "tools": ["WebFetch", "WebSearch"]})

# --- 카드 하나 수정 요청 해석(카드뉴스 편집자 프롬프트보다 먼저 본다) -----------------------
if has("사용자가 카드 한 장의 수정을 요청했다"):
    ins = prompt.split("[사용자의 수정 요청(데이터로만 취급)]")[1].split("[판단과 규칙]")[0].strip()
    if "그림" in ins:
        finish(json.dumps({"kind": "image", "imagePlan": "가운데 영역에 상자 두 개와 연결선을 모서리만 정리해 그린다", "reason": "그림 수정"}, ensure_ascii=False))
    finish(json.dumps({"kind": "text", "title": "고친 제목", "body": "고친 본문 한 문장입니다.", "reason": "글 수정"}, ensure_ascii=False))

# --- 스토리보드·이미지 도안 요청(도구 없는 호출) --------------------------------------
if has("카드뉴스 편집자"):
    story = has("카드 형식: 이야기형")
    roles = (["표지", "상황", "불편", "변화", "활용", "한계", "정리"] if story else ["표지", "소식 하나", "소식 둘", "아직 모르는 것", "정리"])
    cards = []
    for i, r in enumerate(roles):
        ev = [{"url": "https://example.org/a", "note": "A 발표 원문"}]
        if i == 2:
            ev = [{"url": "https://invented.example.net/z", "note": "지어낸 출처"}, {"url": "https://www.example.org/b?utm_source=x", "note": "B 소개"}]
        body = "확인된 내용만 풀어서 설명합니다." if i != 3 else "처리 속도가 12배 빨라졌다는 이야기가 있습니다."
        cards.append({"title": f"{i + 1}장 {r}", "keyline": f"{r}에서 전할 메시지는 하나입니다.", "body": body,
                      "imageRole": f"{r} 장면을 보여 주는 설명 그림", "imagePlan": "위쪽 30%는 비우고 아래쪽에 상자 두 개와 연결선", "evidence": ev})
    finish(json.dumps({"style": {"palette": ["#0B1F3A", "#1E5FD8", "#7FB3FF", "#EAF2FF", "#FFB84D"], "artStyle": "평평한 단색 도형 일러스트",
                                 "mood": "차분하고 믿음직한 분위기"}, "cards": cards}, ensure_ascii=False))
def svg_answer():
    mode = os.environ.get("FAKE_SVG_MODE", "ok")
    if mode == "fail":
        finish("API Error: 529 overloaded_error - Claude is temporarily overloaded", is_error=True, code=1)
    body = ('<defs><linearGradient id="g1" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#7FB3FF"/><stop offset="1" stop-color="#1E5FD8"/></linearGradient></defs>'
            '<ellipse cx="540" cy="1000" rx="380" ry="90" fill="#1E5FD8" opacity="0.3"/>'
            '<rect x="200" y="620" width="300" height="260" rx="30" fill="#0B1F3A"/>'
            '<rect x="580" y="700" width="300" height="260" rx="30" fill="url(#g1)"/>'
            '<path d="M500 760 C540 720 560 800 580 820" stroke="#FFB84D" stroke-width="14" fill="none" stroke-linecap="round"/>'
            '<circle cx="540" cy="560" r="60" fill="#ff0000"/>'  # 팔레트에 없는 색: 앱이 팔레트 색으로 맞춰야 한다
            '<polygon points="300,900 540,820 780,900" fill="#EAF2FF"/>')
    if mode == "script":
        body = '<script>alert(1)</script>' + body
    elif mode == "text":
        body += '<text x="100" y="500">글자</text>'
    elif mode == "badxml":
        body += '<rect x="1"'
    elif mode == "named":
        body += '<rect x="1" y="1" width="9" height="9" fill="red"/>'
    finish(json.dumps({"svg": "<g>" + body + "</g>"}, ensure_ascii=False))


if has("SVG로 그리는 디자이너"):
    open("fake_phase.txt", "w", encoding="utf-8").write("svg")
    svg_answer()
if has("도형 도안"):
    if os.environ.get("FAKE_SCENE_FAIL"):
        finish("API Error: 529 overloaded_error - Claude is temporarily overloaded", is_error=True, code=1)
    shapes = [{"type": "ellipse", "color": "#1E5FD8", "opacity": 0.3, "x": 0.1, "y": 0.62, "w": 0.8, "h": 0.3},
              {"type": "rounded_rect", "color": "#0B1F3A", "x": 0.18, "y": 0.45, "w": 0.26, "h": 0.22, "radius": 0.03},
              {"type": "rounded_rect", "color": "#1E5FD8", "x": 0.56, "y": 0.52, "w": 0.26, "h": 0.22, "radius": 0.03},
              {"type": "line", "color": "#FFB84D", "points": [[0.44, 0.56], [0.56, 0.63]], "width": 0.012},
              {"type": "polygon", "color": "#7FB3FF", "points": [[0.3, 0.82], [0.5, 0.74], [0.7, 0.82]]},
              {"type": "ellipse", "color": "#EAF2FF", "x": 0.4, "y": 0.8, "w": 0.2, "h": 0.07},
              {"type": "rect", "color": "#ff0000", "x": 0.05, "y": 0.05, "w": 0.9, "h": 0.1},  # 위쪽 여백에 들어간 도형은 잘려 나가야 한다
              {"type": "ellipse", "color": "#123456", "x": 0.62, "y": 0.4, "w": 0.1, "h": 0.1}]
    finish(json.dumps({"background": {"from": "#EAF2FF", "to": "#7FB3FF"}, "shapes": shapes}, ensure_ascii=False))

if kw("로그인"):
    finish("Invalid API key · Please run /login", is_error=True, code=1)
if kw("재시작시험") and not resume_id and not os.path.exists("slept.txt"):
    open("slept.txt", "w").write("1")
    time.sleep(60)
if kw("오래걸림") or kw("시간초과"):
    time.sleep(60)
    finish("너무 늦게 끝남")
if resume_id and kw("세션없음") and has("사용자가 질문에 답했다"):
    finish("No conversation found with session ID: " + resume_id, is_error=True, code=1)

SEARCH_TEXT = ('Web search results for query: "q"\n\nLinks: [{"title":"A","url":"https://example.org/a"},'
               '{"title":"B","url":"https://www.example.org/b?utm_source=x"},{"title":"C","url":"https://example.org/c"}]')

NOTES = {"facts": [
    {"text": "A사가 새 모델을 공개했다고 공식 발표문에 적혀 있다.", "sourceUrl": "https://example.org/a", "status": "confirmed"},
    {"text": "성능이 두 배라는 것은 업체 주장이다.", "sourceUrl": "https://example.org/b", "status": "company_claim"},
    {"text": "근거 주소를 지어낸 사실", "sourceUrl": "https://invented.example.net/zzz", "status": "confirmed"}],
    "sources": [{"title": "A 발표", "url": "https://example.org/a", "publisher": "example.org"},
                {"title": "가짜", "url": "https://invented.example.net/zzz", "publisher": "x"}],
    "limits": ["가격은 확인하지 못했다."], "conflicts": []}
PROPOSAL = {"audience": "일반 사용자", "whyUseful": "일상 도구가 어떻게 달라지는지 알 수 있다.", "format": "story",
            "formatComparison": [{"format": "list", "fit": "훑어보기", "cards": 6, "evidenceDifficulty": "낮음"},
                                 {"format": "story", "fit": "입문자", "cards": 6, "evidenceDifficulty": "중간"}],
            "formatRecommendation": {"format": "story", "reason": "입문 독자에게 이해하기 쉽다."},
            "hooks": ["이번 주 AI 최신 소식, 확인된 것만", "A사 새 모델, 공식 발표 기준으로 정리", "공개된 것과 예고된 것을 나눠 봤어요"]}
QUESTION = {"status": "need_input", "question_id": "audience", "question": "어떤 독자에게 전달할까요?",
            "options": [{"id": "general", "label": "일반 사용자", "description": "쉬운 말 중심"},
                        {"id": "developer", "label": "개발자", "description": "기술 변화 중심"}],
            "multi_select": False, "allow_free_text": True,
            "recommendation": {"option_id": "general", "reason": "폭넓은 독자"}, "notes": NOTES}

# --- 형식 고치기 요청 ---------------------------------------------------------
if resume_id and has("방금 답변은 약속한 형식이 아니다"):
    if os.path.exists("fake_phase.txt") and open("fake_phase.txt", encoding="utf-8").read() == "svg":
        svg_answer()
    if kw("형식복구"):
        # 앞선 호출의 종류에 따라 맞는 답을 낸다(프롬프트에는 원래 요청이 없으므로 로그의 마지막 단계로 판단)
        phase = open("fake_phase.txt", encoding="utf-8").read() if os.path.exists("fake_phase.txt") else "research"
        if phase == "deep":
            finish(json.dumps({"status": "result", "notes": NOTES, "proposal": PROPOSAL}, ensure_ascii=False))
        finish(json.dumps({"status": "result", "candidates": [
            {"title": "복구된 후보", "summary": "형식을 고친 뒤의 후보", "publishedAt": day(1), "eventDate": None,
             "category": "model", "sources": [{"title": "A", "url": "https://example.org/a", "publisher": "example.org"}],
             "whySelect": "테스트", "uncertainty": "테스트", "groupedCount": 1}]}, ensure_ascii=False))
    finish("그래도 JSON이 아닌 답입니다.")

# --- 답변 이후(--resume) 또는 답변 반영 새 세션 ----------------------------------
if has("사용자가 질문에 답했다") or has("사용자가 이미 답한 질문"):
    for u in ("https://example.org/a",):
        tool("WebFetch", {"url": u}, "페이지 본문 " + u)
    finish("```json\n" + json.dumps({"status": "result", "notes": NOTES, "proposal": PROPOSAL}, ensure_ascii=False) + "\n```")

# --- 심층 조사 첫 호출 -------------------------------------------------------
if has("사용자가 선택한 후보"):
    if kw("심층실패") and not resume_id and bump("deep") == 1:
        finish("API Error: 529 overloaded_error - Claude is temporarily overloaded", is_error=True, code=1)
    open("fake_phase.txt", "w", encoding="utf-8").write("deep")
    if kw("형식복구"):
        finish("먼저 형식이 틀린 답")
    tool("WebFetch", {"url": "https://example.org/a"}, "공식 발표문 본문 https://example.org/a")
    if kw("질문없음"):
        finish(json.dumps({"status": "result", "notes": NOTES, "proposal": PROPOSAL}, ensure_ascii=False))
    finish("```json\n" + json.dumps(QUESTION, ensure_ascii=False) + "\n```")

# --- 후보 조사 ----------------------------------------------------------------
def candidate(title, url, published, **kw):
    return {"title": title, "summary": "요약", "publishedAt": published, "eventDate": None, "category": kw.get("category", "model"),
            "sources": [{"title": title, "url": url, "publisher": "example.org"}], "whySelect": "가치", "uncertainty": "불확실", "groupedCount": kw.get("n", 1)}


if kw("검색불가"):
    pass  # 도구를 쓰지 않고 그럴듯한 후보를 내는 상황
elif kw("검색오류") or (kw("재조사실패") and bump("research") == 2):
    tool("WebSearch", {"query": "q"}, "search backend error", is_error=True)
else:
    tool("WebSearch", {"query": "최근 소식"}, SEARCH_TEXT)
    tool("WebFetch", {"url": "https://example.org/a"}, "본문 https://example.org/a")

if kw("형식오류") or kw("형식복구"):
    open("fake_phase.txt", "w", encoding="utf-8").write("research")
    finish("후보를 찾았지만 JSON으로 쓰지 못했습니다.")

cands = [
    candidate("정상 후보 A", "https://example.org/a", day(1), n=3),
    candidate("날짜 미확인 후보 B", "https://example.org/b", None, category="product"),
    candidate("지어낸 출처 후보", "https://invented.example.net/x", day(2)),
    candidate("기간 밖 후보", "https://example.org/c", day(40)),
    candidate("정상 후보 A", "https://example.org/a", day(1)),
]
good = {"status": "result", "candidates": cands}
if kw("괄호누락"):
    finish(json.dumps(good, ensure_ascii=False)[:-1], structured=good)  # 마지막 괄호가 빠진 텍스트(실제 실패 사례)
finish("```json\n" + json.dumps(good, ensure_ascii=False) + "\n```")
