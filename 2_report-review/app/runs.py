"""작업(runs) 상태 갱신. 작업의 진짜 상태는 항상 DB에 있고, 엔진 스레드는 쓰기 전에 작업이 아직 진행 중인지 확인해서
취소·중단된 작업이면 Stop으로 멈춘다."""
import json

from . import db

ACTIVE = ("queued", "running")


class Stop(Exception):
    """작업이 이미 취소·중단돼서 더 쓰지 않고 멈춘다."""


def new_run(c, project_id: str, kind: str, message: str) -> str:
    run_id = db.new_id("run")
    now = db.now_iso()
    c.execute("INSERT INTO runs(id, project_id, kind, status, step, message, created_at, updated_at) VALUES (?,?,?, 'queued','queued',?,?,?)",
              (run_id, project_id, kind, message, now, now))
    return run_id


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
        c.execute("UPDATE runs SET status='running', step=?, message=?, updated_at=? WHERE id=?", (step, message[:300], db.now_iso(), run_id))


def stage_before(kind: str, has_findings: bool) -> str:
    """작업이 실패·취소됐을 때 돌아갈 단계."""
    return "findings_ready" if kind == "revise" else "created"


def fail(run_id: str, code: str, message: str, next_action: str) -> None:
    with db.tx() as c:
        run = c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if run is None or run["status"] not in ACTIVE:
            return
        c.execute("UPDATE runs SET status='failed', error_code=?, error_message=?, error_next=?, updated_at=? WHERE id=?",
                  (code, message[:500], next_action[:300], db.now_iso(), run_id))
        c.execute("UPDATE projects SET stage=?, version=version+1, updated_at=? WHERE id=?",
                  (stage_before(run["kind"], True), db.now_iso(), run["project_id"]))


def write_findings(run_id: str, findings: list[dict], paragraphs: list[dict]) -> None:
    """분석 결과를 저장한다. 분석 때의 문단을 같이 저장해서, 나중에 문단 나누는 규칙이 바뀌어도 지적의 문단 번호가 어긋나지 않게 한다."""
    with db.tx() as c:
        run = require_running(c, run_id)
        c.execute("UPDATE projects SET stage='findings_ready', findings_json=?, choices_json='{}', result_json=NULL, paragraphs_json=?, round=1, "
                  "history_json='[]', sources_json='[]', version=version+1, updated_at=? WHERE id=?",
                  (json.dumps(findings, ensure_ascii=False), json.dumps(paragraphs, ensure_ascii=False), db.now_iso(), run["project_id"]))
        c.execute("UPDATE runs SET status='succeeded', step='done', message=?, updated_at=? WHERE id=?",
                  (f"지적 {len(findings)}개를 찾았습니다.", db.now_iso(), run_id))


def write_result(run_id: str, result: dict) -> None:
    with db.tx() as c:
        run = require_running(c, run_id)
        c.execute("UPDATE projects SET stage='done', result_json=?, sources_json=?, version=version+1, updated_at=? WHERE id=?",
                  (json.dumps(result, ensure_ascii=False), json.dumps(result["sources"], ensure_ascii=False), db.now_iso(), run["project_id"]))
        c.execute("UPDATE runs SET status='succeeded', step='done', message='수정과 재검수를 마쳤습니다.', updated_at=? WHERE id=?", (db.now_iso(), run_id))
