"""엔진이 공통으로 쓰는 작업 상태 갱신. 작업의 진짜 상태는 항상 DB에 있고, 엔진 스레드는 쓰기 전에
작업이 아직 진행 중인지 확인해서 취소·중단된 작업이면 Stop으로 멈춘다."""
import json

from . import db

ACTIVE = ("queued", "running")


class Stop(Exception):
    """작업이 이미 취소·중단돼서 더 쓰지 않고 멈춘다."""


def require_running(c, run_id: str):
    run = c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if run is None or run["status"] not in ACTIVE:
        raise Stop()
    return run


def ensure_active(run_id: str) -> None:
    with db.read() as c:
        row = c.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
    if row is None or row["status"] not in ACTIVE:
        raise Stop()


def progress(run_id: str, step: str, message: str) -> None:
    with db.tx() as c:
        require_running(c, run_id)
        c.execute("UPDATE runs SET status='running', step=?, message=?, updated_at=? WHERE id=?",
                  (step, message[:300], db.now_iso(), run_id))


def stage_before(c, run) -> str:
    """작업이 실패·취소됐을 때 돌아갈 단계."""
    pid = run["project_id"]
    if run["kind"] == "storyboard":
        has = c.execute("SELECT storyboard_version FROM projects WHERE id=?", (pid,)).fetchone()[0]
        return "storyboard_ready" if has else "proposal_ready"
    if run["kind"] in ("images", "card_revision"):
        return "review"
    if run["kind"] == "research":
        if not c.execute("SELECT 1 FROM candidates WHERE project_id=?", (pid,)).fetchone():
            return "created"
        sel = json.loads(c.execute("SELECT selection_json FROM projects WHERE id=?", (pid,)).fetchone()[0])
        return "selected" if sel else "candidates_ready"
    return "selected"


def fail(run_id: str, code: str, message: str, next_action: str) -> None:
    with db.tx() as c:
        run = c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if run is None or run["status"] not in ACTIVE:
            return
        c.execute("UPDATE runs SET status='failed', error_code=?, error_message=?, error_next=?, updated_at=? WHERE id=?",
                  (code, message[:500], next_action[:300], db.now_iso(), run_id))
        c.execute("UPDATE projects SET stage=?, version=version+1, updated_at=? WHERE id=?",
                  (stage_before(c, run), db.now_iso(), run["project_id"]))
        reset_generating_cards(c, run)


def reset_generating_cards(c, run) -> None:
    """이미지 작업이 실패·취소·중단되면 '만드는 중'으로 남은 카드를 다시 만들 수 있는 상태로 되돌린다."""
    if run["kind"] in ("images", "card_revision"):
        c.execute("UPDATE cards SET status='pending' WHERE project_id=? AND status='generating'", (run["project_id"],))


def set_session(run_id: str, session_id: str) -> None:
    with db.tx() as c:
        c.execute("UPDATE runs SET engine_session_id=? WHERE id=?", (session_id, run_id))


def write_candidates(run_id: str, candidates: list[dict], note: str | None, message: str) -> None:
    with db.tx() as c:
        run = require_running(c, run_id)
        pid = run["project_id"]
        c.execute("DELETE FROM candidates WHERE project_id=?", (pid,))
        for n, cand in enumerate(candidates):
            c.execute("INSERT INTO candidates(id, project_id, ord, data_json) VALUES (?,?,?,?)",
                      (cand["id"], pid, n, json.dumps(cand, ensure_ascii=False)))
        c.execute("UPDATE projects SET stage='candidates_ready', selection_json='[]', selection_version=NULL, "
                  "candidates_note=?, version=version+1, updated_at=? WHERE id=?", (note, db.now_iso(), pid))
        c.execute("UPDATE runs SET status='succeeded', step='done', message=?, updated_at=? WHERE id=?",
                  (message, db.now_iso(), run_id))


def open_question(run_id: str, q: dict, notes: dict | None = None) -> None:
    """q: question, options[{id,label,description}], multiSelect, allowFreeText, recommendation|None"""
    with db.tx() as c:
        run = require_running(c, run_id)
        pid = run["project_id"]
        c.execute("UPDATE questions SET status='superseded' WHERE run_id=? AND status='open'", (run_id,))
        version = c.execute("SELECT version FROM projects WHERE id=?", (pid,)).fetchone()[0] + 1
        c.execute(
            "INSERT INTO questions(id, project_id, run_id, version, question, options_json, multi_select, "
            "allow_free_text, recommendation_json, status) VALUES (?,?,?,?,?,?,?,?,?,'open')",
            (db.new_id("q"), pid, run_id, version, q["question"], json.dumps(q["options"], ensure_ascii=False),
             int(bool(q["multiSelect"])), int(bool(q["allowFreeText"])),
             json.dumps(q["recommendation"], ensure_ascii=False) if q.get("recommendation") else None))
        c.execute("UPDATE runs SET status='waiting_for_user', step='audience', message='답변을 기다리는 중', updated_at=? WHERE id=?",
                  (db.now_iso(), run_id))
        if notes is not None:
            c.execute("UPDATE projects SET notes_json=? WHERE id=?", (json.dumps(notes, ensure_ascii=False), pid))
        c.execute("UPDATE projects SET stage='waiting_for_user', version=version+1, updated_at=? WHERE id=?",
                  (db.now_iso(), pid))


def write_result(run_id: str, notes: dict, proposal: dict, message: str) -> None:
    with db.tx() as c:
        run = require_running(c, run_id)
        c.execute("UPDATE projects SET stage='proposal_ready', notes_json=?, proposal_json=?, version=version+1, updated_at=? WHERE id=?",
                  (json.dumps(notes, ensure_ascii=False), json.dumps(proposal, ensure_ascii=False), db.now_iso(), run["project_id"]))
        c.execute("UPDATE runs SET status='succeeded', step='done', message=?, updated_at=? WHERE id=?",
                  (message, db.now_iso(), run_id))


def load_context(run_id: str) -> dict:
    """엔진이 프롬프트를 만들 때 쓰는 프로젝트 요약: 주제, 조사 기간, 선택한 후보, 지금까지의 질문·답변."""
    with db.read() as c:
        run = c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        p = c.execute("SELECT * FROM projects WHERE id=?", (run["project_id"],)).fetchone()
        ids = json.loads(p["selection_json"])
        selected = [d for d in (json.loads(r["data_json"]) for r in
                    c.execute("SELECT data_json FROM candidates WHERE project_id=? ORDER BY ord", (p["id"],)))
                    if d["id"] in ids]
        qas = []
        for q in c.execute("SELECT * FROM questions WHERE run_id=? AND status='answered' ORDER BY rowid", (run_id,)):
            options = {o["id"]: o["label"] for o in json.loads(q["options_json"])}
            ans = json.loads(q["answer_json"])
            qas.append({"questionId": q["id"], "question": q["question"],
                        "chosen": [options.get(i, i) for i in ans.get("optionIds", [])],
                        "freeText": ans.get("freeText")})
        asked = c.execute("SELECT COUNT(*) FROM questions WHERE run_id=?", (run_id,)).fetchone()[0]
    return {"run": dict(run), "project": dict(p), "selected": selected, "qas": qas, "questionsAsked": asked,
            "params": json.loads(run["params_json"] or "{}")}


def write_storyboard(run_id: str, sb: dict, message: str) -> None:
    """검증된 스토리보드를 새 버전으로 저장하고 승인 대기 단계로 넘긴다(이전 버전은 보관)."""
    with db.tx() as c:
        run = require_running(c, run_id)
        pid = run["project_id"]
        params = json.loads(run["params_json"] or "{}")
        version = (c.execute("SELECT MAX(version) FROM storyboards WHERE project_id=?", (pid,)).fetchone()[0] or 0) + 1
        c.execute("INSERT INTO storyboards(project_id, version, format, hook, instruction, style_json, cards_json, run_id, created_at) "
                  "VALUES (?,?,?,?,?,?,?,?,?)",
                  (pid, version, sb["format"], params.get("hook"), params.get("instruction"),
                   json.dumps(sb["style"], ensure_ascii=False), json.dumps(sb["cards"], ensure_ascii=False), run_id, db.now_iso()))
        c.execute("UPDATE projects SET stage='storyboard_ready', storyboard_version=?, storyboard_approved=0, version=version+1, updated_at=? WHERE id=?",
                  (version, db.now_iso(), pid))
        c.execute("UPDATE runs SET status='succeeded', step='done', message=?, updated_at=? WHERE id=?", (message, db.now_iso(), run_id))


def finish_images(run_id: str, message: str) -> None:
    with db.tx() as c:
        run = require_running(c, run_id)
        c.execute("UPDATE projects SET stage='review', version=version+1, updated_at=? WHERE id=?", (db.now_iso(), run["project_id"]))
        c.execute("UPDATE runs SET status='succeeded', step='done', message=?, updated_at=? WHERE id=?", (message, db.now_iso(), run_id))
