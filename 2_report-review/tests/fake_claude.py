"""시험용 가짜 `claude`. 프롬프트를 읽고 정해 둔 JSON을 stream-json 형식으로 돌려준다. 실제 모델·네트워크를 쓰지 않는다.
환경 변수 FAKE_MODE로 상황을 바꾼다: ok(기본) / no_search / unknown_url / quote_bad / insufficient / verify_partial / login / hang"""
import json
import os
import sys
import time

argv = sys.argv[1:]
prompt = sys.stdin.read()
mode = os.environ.get("FAKE_MODE", "ok")
log = os.environ.get("FAKE_LOG")
if log:
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"argv": argv, "tools": argv[argv.index("--tools") + 1] if "--tools" in argv else None, "prompt": prompt[:6000]}, ensure_ascii=False) + "\n")
session = "sess_" + str(os.getpid())


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def finish(obj, is_error=False, text=None):
    if not is_error:
        with open("fake_last.json", "w", encoding="utf-8") as f:  # 형식 고치기 요청에서 같은 답을 되풀이할 수 있게 마지막 답을 남긴다
            json.dump(obj, f, ensure_ascii=False)
    emit({"type": "result", "subtype": "error" if is_error else "success", "is_error": is_error, "session_id": session,
          "result": text if text is not None else json.dumps(obj, ensure_ascii=False), **({} if is_error else {"structured_output": obj})})
    sys.exit(1 if is_error else 0)


emit({"type": "system", "subtype": "init", "session_id": session})
if mode == "login":
    finish(None, True, "Not logged in · Please run /login")
if mode == "hang":
    time.sleep(60)


def search(urls, ok=True):
    """검색 도구 호출 기록(tool_use / tool_result)을 흉내 낸다."""
    emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "WebSearch", "input": {"query": "통계 검색"}}]}})
    emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "is_error": not ok,
                                                  "content": ("\n".join(urls) if ok else "search backend error")}]}})


if prompt.startswith("방금 답변에 문제가 있다"):  # 형식 고치기: 모델이 같은 답을 되풀이하는 상황(시험용)
    finish(json.load(open("fake_last.json", encoding="utf-8")))
if "너는 보고서·과제 글을 검수하는 편집자다" in prompt:  # 분석
    quote = "청년 실업은 계속 늘고 있다" if mode != "quote_bad" else "글에 없는 문장입니다"
    finish({"findings": [
        {"paragraphId": "P2", "category": "근거 부족", "quote": quote, "problem": "수치나 출처 없이 단정했다.", "why": "독자가 믿을 근거가 없다.",
         "directions": [{"label": "통계로 뒷받침", "detail": "공식 통계를 찾아 인용한다.", "needsSearch": True},
                        {"label": "표현을 낮춤", "detail": "단정을 피하고 가능성으로 쓴다.", "needsSearch": False}], "recommended": "a"},
        {"paragraphId": "P3", "category": "비약", "quote": "그러므로 정부가 책임져야 한다", "problem": "원인에서 책임으로 바로 건너뛴다.", "why": "논리가 약하다.",
         "directions": [{"label": "중간 단계 추가", "detail": "원인과 책임을 잇는 이유를 쓴다.", "needsSearch": False},
                        {"label": "결론 완화", "detail": "책임 대신 제안으로 바꾼다.", "needsSearch": False}], "recommended": "a"}]})
elif "너는 보고서·과제 글의 한 문단을 고치는 편집자다" in prompt:  # 수정
    pid = prompt.split("- 고칠 문단: [")[1].split("]")[0]
    original = prompt.split("- 고칠 문단: [" + pid + "]\n")[1].split("\n- 지적:")[0]
    uses_search = "WebSearch" in argv[argv.index("--tools") + 1] if "--tools" in argv else False
    if "사용자가 직접 제공한 자료" in prompt:  # 사용자가 붙여 넣은 자료를 근거로 고친다
        material = prompt.split("<<<\n")[1].split("\n>>>")[0]
        n = int(prompt.split("N=")[1].split("개")[0])
        quote = material[:14] if mode != "bad_quote" else "자료에 없는 문장입니다"
        finish({"status": "revised", "newText": original.replace("청년 실업은 계속 늘고 있다", f"청년 실업률이 높다는 제공 자료의 설명이 있다[{n + 1}]"),
                "sources": [{"url": "user-material", "title": "x", "quote": quote}], "gap": "", "held": []})
    if uses_search:
        if mode == "no_search":
            search([], ok=False)
        else:
            search(["https://kostat.example.org/stat/youth"])
        if mode == "insufficient":
            finish({"status": "insufficient", "newText": "", "sources": [], "gap": "공식 통계를 찾지 못했다.", "held": [{"claim": "청년 실업이 늘고 있다는 공식 수치", "needed": "통계청 경제활동인구조사의 청년층(15~29세) 실업률 최근 5년 추이"}]})
        url = "https://kostat.example.org/stat/youth" if mode != "unknown_url" else "https://made-up.example.net/x"
        n = int(prompt.split("N=")[1].split("개")[0])  # 지금까지 쓴 참고 자료 수: 새 인용은 [n+1]부터 이어 쓴다
        held = [{"claim": "청년 실업이 늘고 있다는 공식 수치", "needed": "통계청 경제활동인구조사의 청년층(15~29세) 실업률 최근 5년 추이"}] if mode == "partial_hold" else []  # 일부만 근거가 있어 나머지는 보류하는 경우
        finish({"status": "revised", "newText": original.replace("청년 실업은 계속 늘고 있다", f"청년 실업률은 2023년 6.1%였다[{n + 1}]"),
                "sources": [{"url": url, "title": "청년 고용 통계", "quote": "청년 실업률 6.1%"}], "gap": "", "held": held})
    finish({"status": "revised", "newText": original.rstrip() + " 이는 앞선 원인이 책임으로 이어지는 이유를 보여 준다.", "sources": [], "gap": ""})
elif "너는 고친 글을 다시 검수하는 편집자다" in prompt:  # 재검수
    heads = [l for l in prompt.splitlines() if l.startswith("### ")]
    ids = [l.split()[1] for l in heads]
    verdict = "partial" if mode == "verify_partial" else "resolved"
    items = []
    for h, i in zip(heads, ids):
        issues = []
        if "[근거 부족]" in h and mode != "no_issues":  # 첫 라운드의 근거 부족 수정만 새 문제를 만든다(이어서 고치면 사라진다)
            issues = [{"text": "인용한 통계가 한 해 수치뿐이라 추세를 말하기 어렵다.", "severity": "high", "paragraphId": "P2",
                       "directions": [{"label": "추세 자료 추가", "detail": "여러 해 수치를 더한다.", "needsSearch": True},
                                      {"label": "표현 범위 좁힘", "detail": "한 해의 수치라고 한정한다.", "needsSearch": False}], "recommended": "b"},
                      {"text": "수치 뒤 문장 길이가 조금 길다.", "severity": "low", "paragraphId": "P2"}]
        elif "[비약]" in h and mode == "medium_issue":
            issues = [{"text": "연결 문장이 추상적이다.", "severity": "medium", "paragraphId": "P3",
                       "directions": [{"label": "예시 추가", "detail": "예를 든다.", "needsSearch": False}, {"label": "자료로 뒷받침", "detail": "자료를 찾는다.", "needsSearch": True}],
                       "recommended": "a"}]
        items.append({"findingId": i, "verdict": verdict, "reason": "고친 문단이 근거를 제시해 문제를 해소했다.", "newIssues": issues})
    finish({"items": items})
else:
    finish({"findings": []})
