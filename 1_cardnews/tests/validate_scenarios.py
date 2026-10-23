"""입력·조작 시나리오 검증 도우미. 실행 중인 서버(HTTP)만 만진다. 자동 테스트(unittest)가 아니라 VALIDATION.md를 만들 때 쓰는 스크립트다.

서버는 따로 띄운다(앱 결과 폴더가 아니라 임시 폴더, 가짜 claude 사용):
  CARDNEWS_DATA_DIR=<임시폴더>\\data  CARDNEWS_CLAUDE_CMD=[python, tests\\fake_claude.py]
  FAKE_CLAUDE_LOG=<임시폴더>\\calls.jsonl  FAKE_STATE_DIR=<임시폴더>\\state  python run.py --port 8792

사용:  python tests/validate_scenarios.py <임시폴더> <포트> <단계>
  단계: a(시나리오 1~4 + 브라우저용 프로젝트 준비) | ui_stale_supersede(브라우저 시험 중 질문 교체)
        b_prepare(서버 재시작 전 상태 만들기) | b_verify(재시작 뒤 확인)
결과는 <임시폴더>\\results_<단계>.json 에 남는다.
"""
import json
import os
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid

TMP, PORT, PHASE = sys.argv[1], sys.argv[2], sys.argv[3]
BASE = f"http://127.0.0.1:{PORT}"
LOG = os.path.join(TMP, "calls.jsonl")
STATE_FILE = os.path.join(TMP, "validation_state.json")
DB = os.path.join(TMP, "data", "app.db")
RESULTS = []


def call(method, path, body=None):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method, headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8")), {k.lower(): v for k, v in r.headers.items()}
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8")), {k.lower(): v for k, v in e.headers.items()}


def rid():
    return uuid.uuid4().hex


def calls():
    if not os.path.exists(LOG):
        return []
    return [json.loads(x) for x in open(LOG, encoding="utf-8").read().splitlines() if x.strip()]


def db_count(sql, *args):
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        return c.execute(sql, args).fetchone()[0]
    finally:
        c.close()


def state(pid):
    return call("GET", f"/api/projects/{pid}")[1]


def wait(pid, pred, limit=90, label=""):
    t0 = time.time()
    while time.time() - t0 < limit:
        s = state(pid)
        if pred(s):
            return s
        time.sleep(0.5)
    raise SystemExit(f"[{label}] timeout; stage={state(pid)['stage']} run={state(pid)['currentRun']}")


def check(scn, label, ok, detail=""):
    RESULTS.append({"scenario": scn, "check": label, "ok": bool(ok), "detail": str(detail)[:300]})
    print(("PASS " if ok else "FAIL ") + f"[{scn}] {label}" + (f" | {detail}" if detail else ""), flush=True)


def new_project(topic):
    _, p, _ = call("POST", "/api/projects", {"topic": topic, "requestId": rid()})
    return p["projectId"], p["version"]


def to_candidates(topic):
    pid, v = new_project(topic)
    call("POST", f"/api/projects/{pid}/research", {"version": v, "requestId": rid()})
    return pid, wait(pid, lambda s: s["stage"] == "candidates_ready", label="research")


def select(pid, ids):
    s = state(pid)
    return call("POST", f"/api/projects/{pid}/selection", {"candidateIds": ids, "version": s["version"], "requestId": rid()})[0]


def start_deep(pid):
    s = state(pid)
    return call("POST", f"/api/projects/{pid}/deep-research", {"version": s["version"], "requestId": rid()})


def to_question(topic):
    pid, _ = to_candidates(topic)
    select(pid, ["cand_1", "cand_2"])
    start_deep(pid)
    return pid, wait(pid, lambda s: s["stage"] == "waiting_for_user", label="deep")


def answer(pid, q, option="general", request_id=None):
    return call("POST", f"/api/projects/{pid}/answers", {"questionId": q["questionId"], "version": q["version"],
                "answer": {"optionIds": [option], "freeText": None}, "requestId": request_id or rid()})


def snapshot(pid):
    s = state(pid)
    r = s["currentRun"] or {}
    return {"version": s["version"], "stage": s["stage"], "runId": r.get("runId"), "runStatus": r.get("status"),
            "questionId": (s["question"] or {}).get("questionId"), "proposal": s["proposal"]}


def save_state(**kv):
    d = json.load(open(STATE_FILE)) if os.path.exists(STATE_FILE) else {}
    d.update(kv)
    json.dump(d, open(STATE_FILE, "w"), ensure_ascii=False)


def load_state():
    return json.load(open(STATE_FILE))


def finish():
    path = os.path.join(TMP, f"results_{PHASE}.json")
    json.dump(RESULTS, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    bad = [r for r in RESULTS if not r["ok"]]
    print(f"\n== {PHASE}: {len(RESULTS) - len(bad)} PASS / {len(bad)} FAIL -> {path}")
    sys.exit(1 if bad else 0)


# --------------------------------------------------------------------------- 단계 a
def phase_a():
    # 시나리오 1: 질문 → 새로고침 → 같은 질문, 답하기 전에는 제작(다음 작업) 시작 안 됨
    pid, s = to_question("독자질문 시험 S1")
    q = s["question"]
    n_calls, n_runs = len(calls()), db_count("select count(*) from runs where project_id=?", pid)
    check("S1", "질문이 나왔다(선택지 2개 이상, 추천 있음)", len(q["options"]) >= 2 and q["recommendation"], [o["label"] for o in q["options"]])
    same = all(state(pid)["question"] == q for _ in range(3))  # 새로고침 = 같은 GET을 다시 호출
    check("S1", "새로고침 3번 모두 같은 질문 ID·버전·선택지", same, q["questionId"])
    time.sleep(6)
    s2 = state(pid)
    check("S1", "6초 뒤에도 답변 대기(stage·작업 상태 그대로)", s2["stage"] == "waiting_for_user" and s2["currentRun"]["status"] == "waiting_for_user")
    check("S1", "답하기 전에는 새 작업이 없고 Claude 호출도 늘지 않음", db_count("select count(*) from runs where project_id=?", pid) == n_runs and len(calls()) == n_calls,
          f"runs={n_runs}, calls={n_calls}")
    check("S1", "제안(최종 결과)이 아직 없음", s2["proposal"] is None)
    check("S1", "대기 중 다음 단계 요청은 거절(wrong_stage)", start_deep(pid)[0] == 409)
    save_state(s1_pid=pid)

    # 시나리오 2: 같은 답변 두 번(동시 + 이후 한 번 더) → 작업은 한 번만
    pid, s = to_question("이중제출 시험 S2")
    q = s["question"]
    n_calls = len(calls())
    runs_before = db_count("select count(*) from runs where project_id=?", pid)
    req = rid()
    out = []
    ts = [threading.Thread(target=lambda: out.append(answer(pid, q, "general", req))) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    out.append(answer(pid, q, "general", req))
    check("S2", "세 번 보낸 요청이 모두 200이고 같은 응답", all(o[0] == 200 for o in out) and len({json.dumps(o[1], sort_keys=True) for o in out}) == 1, [o[0] for o in out])
    check("S2", "재생 응답 표시(Idempotent-Replay)가 최소 1개", sum(1 for o in out if o[2].get("idempotent-replay") == "true") >= 1,
          [o[2].get("idempotent-replay") for o in out])
    final = wait(pid, lambda x: x["stage"] == "proposal_ready", label="S2 finish")
    new_calls = calls()[n_calls:]
    check("S2", "답변 이후 Claude 호출은 정확히 1번(--resume)", len(new_calls) == 1 and new_calls[0]["resume"], f"calls={len(new_calls)}")
    check("S2", "새 작업 행이 만들어지지 않음(같은 작업을 이어감)", db_count("select count(*) from runs where project_id=?", pid) == runs_before)
    check("S2", "결과(제안)가 한 번 만들어짐", final["proposal"] is not None)

    # 시나리오 3a: 질문이 교체된 뒤 이전 질문 화면에서 답변
    pid, s = to_question("지난질문 시험 S3")
    q1 = s["question"]
    run1 = s["currentRun"]["runId"]
    call("POST", f"/api/runs/{run1}/cancel")
    start_deep(pid)
    s = wait(pid, lambda x: x["stage"] == "waiting_for_user" and x["question"] and x["question"]["questionId"] != q1["questionId"], label="S3 new question")
    q2 = s["question"]
    before, n_calls = snapshot(pid), len(calls())
    st, body, _ = answer(pid, q1, "developer")
    after = snapshot(pid)
    check("S3", "지난 질문(교체됨)에 답하면 409 stale_question", st == 409 and body["error"]["code"] == "stale_question", f"{st} {body['error']['code']}")
    check("S3", "안내 문구가 지난 질문임을 알림", "이미 답했거나 바뀐 질문" in body["error"]["message"], body["error"]["message"])
    check("S3", "현재 작업·질문·버전이 그대로(덮어쓰지 않음)", before == after, f"{before['questionId']} == {after['questionId']}")
    check("S3", "Claude가 추가로 호출되지 않음", len(calls()) == n_calls)
    ok2 = answer(pid, q2, "general")
    check("S3", "현재 질문에는 정상적으로 답할 수 있고 작업이 이어짐", ok2[0] == 200)
    done = wait(pid, lambda x: x["stage"] == "proposal_ready", label="S3 finish")
    # 시나리오 3b: 이미 끝난 질문에 뒤늦게(다른 requestId) 답변
    snap = snapshot(pid)
    st, body, _ = answer(pid, q2, "developer")
    check("S3", "이미 답한 질문에 뒤늦게 답하면 409 stale_question", st == 409 and body["error"]["code"] == "stale_question")
    check("S3", "완료된 결과가 그대로(덮어쓰지 않음)", snapshot(pid) == snap and done["proposal"]["audience"])

    # 시나리오 4a: 심층 조사 실패 → 원인 표시 → 재시도, 앞 단계 결과 유지
    pid, _ = to_candidates("심층실패 시험 S4a")
    select(pid, ["cand_1", "cand_2"])
    cands_before = [c["id"] for c in state(pid)["candidates"]]
    start_deep(pid)
    s = wait(pid, lambda x: x["currentRun"] and x["currentRun"]["status"] == "failed", label="S4a fail")
    err = s["currentRun"]["error"]
    check("S4a", "실패 원인이 화면용 오류로 표시됨(코드·메시지·다음 행동)", err["code"] == "engine_failed" and "529" in err["message"] and err["nextAction"], err["message"])
    check("S4a", "확정한 선택(후보 2개)과 후보 목록이 그대로", s["selection"]["candidateIds"] == ["cand_1", "cand_2"] and [c["id"] for c in s["candidates"]] == cands_before)
    check("S4a", "stage는 시작 전 단계(selected)로 돌아감, 가짜 결과 없음", s["stage"] == "selected" and s["proposal"] is None and s["question"] is None)
    check("S4a", "스토리보드·카드는 아직 없음(미구현이라 보존 여부 시험 불가)", s["storyboard"] is None and s["cards"] == [])
    st, r, _ = start_deep(pid)
    check("S4a", "재시도 요청이 새 작업으로 접수됨", st == 202 and r["runId"] != s["currentRun"]["runId"], r["runId"])
    s = wait(pid, lambda x: x["stage"] == "waiting_for_user", label="S4a retry")
    check("S4a", "재시도가 성공해 질문 단계까지 감, 선택·후보는 그대로", s["selection"]["candidateIds"] == ["cand_1", "cand_2"] and [c["id"] for c in s["candidates"]] == cands_before)
    answer(pid, s["question"], "general")
    s = wait(pid, lambda x: x["stage"] == "proposal_ready", label="S4a finish")
    check("S4a", "재시도 후 끝까지 완료", s["proposal"] is not None and s["currentRun"]["status"] == "succeeded")

    # 시나리오 4b: 다시 조사 실패 → 이미 성공한 후보 결과 유지 → 재시도
    pid, s0 = to_candidates("재조사실패 시험 S4b")
    ids0 = [c["id"] for c in s0["candidates"]]
    select(pid, ["cand_1"])
    s = state(pid)
    call("POST", f"/api/projects/{pid}/research", {"version": s["version"], "requestId": rid()})
    s = wait(pid, lambda x: x["currentRun"]["status"] == "failed", label="S4b fail")
    check("S4b", "다시 조사 실패 원인 표시(search_unavailable, 다음 행동)", s["currentRun"]["error"]["code"] == "search_unavailable" and s["currentRun"]["error"]["nextAction"],
          s["currentRun"]["error"]["message"][:100])
    check("S4b", "이미 성공한 후보 목록과 내 선택이 그대로", [c["id"] for c in s["candidates"]] == ids0 and s["selection"]["candidateIds"] == ["cand_1"] and s["stage"] == "selected")
    st = call("POST", f"/api/projects/{pid}/research", {"version": s["version"], "requestId": rid()})[0]
    s = wait(pid, lambda x: x["stage"] == "candidates_ready", label="S4b retry")
    check("S4b", "재시도 성공(새 후보로 교체되고 선택은 비워짐 — 설계대로)", st == 202 and s["currentRun"]["status"] == "succeeded" and s["selection"]["candidateIds"] == [])

    # 브라우저 시험용 프로젝트 3개(질문 대기 상태)
    p_reload, _ = to_question("브라우저새로고침 시험")
    p_dbl, _ = to_question("브라우저더블클릭 시험")
    p_stale, sq = to_question("브라우저지난질문 시험")
    save_state(ui_reload=p_reload, ui_dbl=p_dbl, ui_stale=p_stale, ui_stale_q1=sq["question"]["questionId"], ui_stale_run=sq["currentRun"]["runId"])
    print("browser projects:", p_reload, p_dbl, p_stale)
    finish()


def phase_ui_stale_supersede():
    st = load_state()
    pid = st["ui_stale"]
    call("POST", f"/api/runs/{st['ui_stale_run']}/cancel")
    start_deep(pid)
    s = wait(pid, lambda x: x["stage"] == "waiting_for_user" and x["question"] and x["question"]["questionId"] != st["ui_stale_q1"], label="ui stale")
    save_state(ui_stale_q2=s["question"]["questionId"], ui_stale_snapshot=snapshot(pid))
    print("replaced question:", st["ui_stale_q1"], "->", s["question"]["questionId"], "| calls", len(calls()))
    finish()


# --------------------------------------------------------------------------- 단계 b
def phase_b_prepare():
    pq, s = to_question("서버재시작 질문 S5a")
    pr, _ = to_question("서버재시작 결과 S5b")
    answer(pr, state(pr)["question"], "developer")
    done = wait(pr, lambda x: x["stage"] == "proposal_ready", label="S5b")
    # 오래 걸리는 작업을 시작해 두고(서버를 끌 때 진행 중이 되도록) 가짜 claude가 실제로 떠 있는지 확인
    pi, v = new_project("재시작시험 S5c")
    n = len(calls())
    call("POST", f"/api/projects/{pi}/research", {"version": v, "requestId": rid()})
    for _ in range(60):
        if len(calls()) > n:
            break
        time.sleep(0.2)
    si = state(pi)
    save_state(s5a=pq, s5a_q=s["question"], s5b=pr, s5b_snapshot={"proposal": done["proposal"], "notes": done["researchNotes"], "version": done["version"]},
               s5c=pi, s5c_run=si["currentRun"]["runId"], s5c_pid=calls()[-1]["pid"])
    print("prepared: waiting", pq, "| done", pr, "| running", pi, si["currentRun"]["status"], "| fake claude pid", calls()[-1]["pid"])
    finish()


def phase_b_verify():
    st = load_state()
    names = {p["projectId"]: p for p in call("GET", "/api/projects")[1]["projects"]}
    check("S5", "서버를 다시 켠 뒤 프로젝트 목록에 세 프로젝트가 모두 있음", all(k in names for k in (st["s5a"], st["s5b"], st["s5c"])))
    a = state(st["s5a"])
    check("S5", "질문 대기 프로젝트: 같은 질문 ID·버전·선택지·추천이 돌아옴", a["question"] == st["s5a_q"] and a["stage"] == "waiting_for_user" and a["currentRun"]["status"] == "waiting_for_user")
    check("S5", "질문 대기 프로젝트: 이전 조사 기록(사실)도 돌아옴", bool(a["researchNotes"] and a["researchNotes"]["facts"]) and len(a["candidates"]) > 0)
    b = state(st["s5b"])
    snap = st["s5b_snapshot"]
    check("S5", "완료 프로젝트: 제안·조사 기록·버전이 재시작 전과 완전히 같음", b["proposal"] == snap["proposal"] and b["researchNotes"] == snap["notes"] and b["version"] == snap["version"])
    c = state(st["s5c"])
    check("S5", "진행 중이던 작업이 '중단됨(interrupted)'으로 인식됨", c["currentRun"]["status"] == "interrupted", f"{c['stage']} / {c['currentRun']['status']}")
    check("S5", "중단된 작업의 stage·선택 정보가 남아 있음(조사 중 상태 유지)", c["stage"] == "researching")
    n = len(calls())
    r = call("POST", f"/api/runs/{st['s5c_run']}/resume", {"mode": "continue", "requestId": rid()})
    check("S5", "중단된 작업을 다시 시도(이어서 진행)할 수 있음", r[0] == 202, r[1])
    c = wait(st["s5c"], lambda x: x["stage"] == "candidates_ready", label="S5c resume")
    check("S5", "다시 시도한 작업이 끝까지 완료되고 같은 작업 ID를 씀", c["currentRun"]["status"] == "succeeded" and c["currentRun"]["runId"] == st["s5c_run"] and len(calls()) > n)
    check("S5", "끝난 작업을 다시 이어가려 하면 거절", call("POST", f"/api/runs/{st['s5c_run']}/resume", {"mode": "restart", "requestId": rid()})[0] == 409)
    ans = answer(st["s5a"], a["question"], "general")
    check("S5", "재시작 뒤에도 질문에 답하면 같은 제작이 이어져 완료됨", ans[0] == 200 and wait(st["s5a"], lambda x: x["stage"] == "proposal_ready", label="S5a finish")["proposal"] is not None)
    finish()


{"a": phase_a, "ui_stale_supersede": phase_ui_stale_supersede, "b_prepare": phase_b_prepare, "b_verify": phase_b_verify}[PHASE]()
