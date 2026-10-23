"""카드뉴스 웹 서버. 화면과 영속 상태를 관리하고 실제 작업은 엔진이 맡는다. 계약은 API_SPEC.md 참고."""
import hashlib
import json
import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Literal, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import cardfiles, db, images, procguard, svg_render, typeset
from . import export as export_mod
from . import runs as runs_mod
from .claude_engine import ClaudeEngine
from .mock_engine import MockEngine
from .runs import stage_before

STATIC_DIR = Path(__file__).resolve().parent / "static"
# 같은 인터페이스(start / answer / resume / cancel)를 가진 엔진들. 프로젝트는 만들어질 때의 엔진을 끝까지 쓴다.
ENGINES = {"mock": MockEngine(), "claude-cli": ClaudeEngine()}


def default_engine_name() -> str:
    name = os.environ.get("CARDNEWS_ENGINE", "claude-cli")
    return name if name in ENGINES else "claude-cli"


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, next_action: str = "", retryable: bool = False, detail=None):
        self.status, self.code, self.message = status, code, message
        self.next_action, self.retryable, self.detail = next_action, retryable, detail or {}


def error_body(code, message, next_action="", retryable=False, detail=None) -> dict:
    return {"error": {"code": code, "message": message, "retryable": retryable,
                      "nextAction": next_action, "detail": detail or {}}}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    procguard.protect_current_process()  # 서버가 강제로 끝나도 claude 하위 프로세스가 남지 않게 한다
    db.init()
    with db.tx() as c:  # 서버가 꺼지면서 끊긴 작업을 표시한다. 대기 중인 질문은 그대로 남는다.
        c.execute("UPDATE runs SET status='interrupted', updated_at=? WHERE status IN ('queued','running')",
                  (db.now_iso(),))
        c.execute("UPDATE cards SET status='pending' WHERE status='generating'")  # 끊긴 이미지 작업의 카드는 다시 만들 수 있게
    yield
    for eng in ENGINES.values():  # 서버를 끄면 실행 중인 하위 프로세스도 함께 정리한다
        eng.shutdown()


app = FastAPI(title="카드뉴스 에이전트", lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])


@app.exception_handler(ApiError)
async def _api_error(_req: Request, exc: ApiError):
    return JSONResponse(error_body(exc.code, exc.message, exc.next_action, exc.retryable, exc.detail), status_code=exc.status)


@app.exception_handler(RequestValidationError)
async def _validation_error(_req: Request, exc: RequestValidationError):
    fields = [".".join(str(p) for p in e["loc"][1:]) or "body" for e in exc.errors()]
    return JSONResponse(error_body("invalid_input", "요청 형식이 올바르지 않습니다: " + ", ".join(fields),
                                   "입력을 확인한 뒤 다시 시도해 주세요."), status_code=400)


@app.exception_handler(StarletteHTTPException)
async def _http_error(_req: Request, exc: StarletteHTTPException):
    code = "not_found" if exc.status_code == 404 else "invalid_input"
    return JSONResponse(error_body(code, "요청한 주소를 찾을 수 없거나 허용되지 않는 요청입니다."), status_code=exc.status_code)


# --- 요청 모델 ---------------------------------------------------------------
RequestId = Field(min_length=8, max_length=64)


class Period(BaseModel):
    days: int


class CreateProject(BaseModel):
    topic: str
    period: Optional[Period] = None
    requestId: str = RequestId


class ResearchReq(BaseModel):
    version: int
    days: Optional[int] = None
    requestId: str = RequestId


class SelectionReq(BaseModel):
    candidateIds: list[str]
    version: int
    requestId: str = RequestId


class DeepReq(BaseModel):
    version: int
    requestId: str = RequestId


class AnswerBody(BaseModel):
    optionIds: list[str] = []
    freeText: Optional[str] = None


class AnswerReq(BaseModel):
    questionId: str
    version: int
    answer: AnswerBody
    requestId: str = RequestId


class StoryboardReq(BaseModel):
    format: str
    hookIndex: Optional[int] = None
    instruction: Optional[str] = None
    version: int
    requestId: str = RequestId


class RevisionReq(BaseModel):
    target: str = "cards"
    cardIds: list[str] = []
    instruction: str
    version: int
    requestId: str = RequestId


class ApproveReq(BaseModel):
    stage: str
    version: int
    requestId: str = RequestId


class TextLayout(BaseModel):
    title: str = Field(max_length=120)
    body: str = Field(max_length=400)
    titleSize: Optional[int] = Field(default=None, ge=40, le=120)
    bodySize: Optional[int] = Field(default=None, ge=28, le=64)
    color: Literal["auto", "light", "dark"] = "auto"
    scrim: int = Field(default=70, ge=0, le=90)
    bodyAnchor: Literal["bottom", "center"] = "bottom"


class TypesetReq(BaseModel):
    layout: TextLayout


class RetryCardsReq(BaseModel):
    cardIds: Optional[list[str]] = None
    version: int
    requestId: str = RequestId


class ResumeReq(BaseModel):
    mode: str
    requestId: str = RequestId


# --- 공통 도우미 -------------------------------------------------------------
def run_idempotent(scope: str, endpoint: str, request_id: str, payload: dict, fn: Callable):
    """같은 requestId·같은 입력은 저장한 응답을 돌려주고, 다른 입력은 거절한다.
    fn(conn) -> (status, body, post). post는 저장이 끝난 뒤 한 번만 부르는 후속 동작(엔진 시작 등)이다."""
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    post = None
    with db.tx() as c:
        row = c.execute("SELECT * FROM requests WHERE project_id=? AND request_id=?", (scope, request_id)).fetchone()
        if row:
            if row["endpoint"] != endpoint or row["input_hash"] != digest:
                raise ApiError(409, "request_conflict", "같은 requestId로 다른 내용을 보냈습니다.",
                               "새 요청으로 다시 시도해 주세요.")
            stored = json.loads(row["response_json"])
            return JSONResponse(stored["body"], status_code=stored["status"], headers={"Idempotent-Replay": "true"})
        status, body, post = fn(c)
        c.execute("INSERT INTO requests(project_id, request_id, endpoint, input_hash, response_json) VALUES (?,?,?,?,?)",
                  (scope, request_id, endpoint, digest, json.dumps({"status": status, "body": body}, ensure_ascii=False)))
    if post:
        post()
    return JSONResponse(body, status_code=status)


def get_project(c, pid: str):
    p = c.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    if p is None:
        raise ApiError(404, "not_found", "프로젝트를 찾을 수 없습니다.", "처음 화면에서 프로젝트를 다시 고르세요.")
    return p


def check_version(p, version: int) -> None:
    if p["version"] != version:
        raise ApiError(409, "stale_version", "화면이 오래되었습니다. 다른 곳에서 상태가 바뀌었습니다.",
                       "새로고침 후 다시 시도해 주세요.", detail={"currentVersion": p["version"]})


def wrong_stage(p, allowed: str) -> ApiError:
    return ApiError(409, "wrong_stage", f"지금 단계({p['stage']})에서는 할 수 없는 요청입니다. 가능한 단계: {allowed}",
                    "화면을 새로고침해서 현재 단계를 확인하세요.")


def active_run(c, pid: str, kind: str):
    return c.execute("SELECT * FROM runs WHERE project_id=? AND kind=? AND status IN ('queued','running') "
                     "ORDER BY rowid DESC LIMIT 1", (pid, kind)).fetchone()


def new_run(c, p, kind: str, message: str, params: dict | None = None) -> str:
    run_id = db.new_id("run")
    now = db.now_iso()
    c.execute("INSERT INTO runs(id, project_id, kind, status, step, message, engine, params_json, created_at, updated_at) "
              "VALUES (?,?,?, 'queued', 'queued', ?, ?, ?, ?, ?)",
              (run_id, p["id"], kind, message, p["engine"], json.dumps(params, ensure_ascii=False) if params else None, now, now))
    return run_id


def basis_window(days: int) -> dict:
    now = db.now_iso()
    to = date.fromisoformat(now[:10])
    return {"days": days, "from": (to - timedelta(days=days)).isoformat(), "to": to.isoformat(), "basis": now}


def storyboard_state(c, p) -> dict | None:
    if not p["storyboard_version"]:
        return None
    sb = c.execute("SELECT * FROM storyboards WHERE project_id=? AND version=?", (p["id"], p["storyboard_version"])).fetchone()
    return {"version": sb["version"], "format": sb["format"], "hook": sb["hook"], "instruction": sb["instruction"],
            "approved": bool(p["storyboard_approved"]), "style": json.loads(sb["style_json"]),
            "cards": json.loads(sb["cards_json"]), "createdAt": sb["created_at"]}


def card_paths(pid: str, card_id: str) -> Path:
    return db.DATA_DIR / "projects" / pid / "cards" / card_id


def typeset_saved(pid: str, card_id: str, image_version: int) -> dict | None:
    """저장한 글자 배치. 지금 이미지 버전의 합성본 파일이 실제로 있을 때만 돌려준다."""
    d = card_paths(pid, card_id)
    meta = d / "layout.json"
    if not meta.is_file() or not (d / f"v{image_version}" / "final.png").is_file():
        return None
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if data.get("imageVersion") == image_version else None


def cards_state(c, p) -> list[dict]:
    out = []
    if not p["storyboard_version"]:
        return out
    titles = {x["id"]: x for x in json.loads(c.execute("SELECT cards_json FROM storyboards WHERE project_id=? AND version=?",
                                                       (p["id"], p["storyboard_version"])).fetchone()[0])}
    for r in c.execute("SELECT * FROM cards WHERE project_id=? ORDER BY ord", (p["id"],)):
        item = {"id": r["card_id"], "order": r["ord"], "title": titles.get(r["card_id"], {}).get("title"), "status": r["status"],
                "currentVersion": r["current_version"], "error": None, "imageUrl": None, "tool": None, "width": None, "height": None,
                "bytes": None, "checks": None, "notes": [], "provenance": None, "versions": [], "finalUrl": None, "typeset": None}
        if r["status"] == "failed":
            item["error"] = {"code": r["error_code"], "message": r["error_message"], "nextAction": r["error_next"]}
        vers = c.execute("SELECT * FROM card_versions WHERE project_id=? AND card_id=? ORDER BY version", (p["id"], r["card_id"])).fetchall()
        item["versions"] = [{"version": v["version"], "tool": v["tool"], "createdAt": v["created_at"],
                             "imageUrl": f"/api/projects/{p['id']}/files/{v['file_rel']}", "current": v["version"] == r["current_version"]} for v in vers]
        cur = next((v for v in vers if v["version"] == r["current_version"]), None)
        if cur:
            chk = json.loads(cur["checks_json"])
            item.update({"imageUrl": f"/api/projects/{p['id']}/files/{cur['file_rel']}", "tool": cur["tool"], "width": cur["width"],
                         "height": cur["height"], "bytes": cur["bytes"], "checks": chk["checks"], "notes": chk["notes"],
                         "provenance": json.loads(cur["provenance_json"])})
            saved = typeset_saved(p["id"], r["card_id"], cur["version"])
            if saved:
                item["finalUrl"] = f"/api/projects/{p['id']}/files/cards/{r['card_id']}/v{cur['version']}/final.png"
                item["typeset"] = {"ok": saved["ok"], "savedAt": saved["savedAt"], "checks": saved["checks"]}
        item["revisions"] = [{"at": x.get("at"), "kind": x.get("kind"), "instruction": x.get("instruction"), "reason": x.get("reason"),
                              "notes": x.get("notes", [])} for x in cardfiles.load_revisions(p["id"], r["card_id"])][-3:]
        out.append(item)
    return out


def final_state(c, p) -> dict:
    if not p["storyboard_approved"] or p["stage"] != "review":
        return {"approved": False, "resultVersion": None, "approvedAt": None, "stale": False, "ready": False, "blockers": []}
    a = cardfiles.approval_state(c, p)
    return {"approved": a["approved"], "resultVersion": p["version"] if a["approved"] else None, "approvedAt": a["approvedAt"],
            "stale": a["stale"], "ready": a["ready"], "blockers": a["blockers"]}


def build_state(c, pid: str) -> dict:
    p = get_project(c, pid)
    run = c.execute("SELECT * FROM runs WHERE project_id=? ORDER BY rowid DESC LIMIT 1", (pid,)).fetchone()
    q = c.execute("SELECT * FROM questions WHERE project_id=? AND status='open' ORDER BY rowid DESC LIMIT 1", (pid,)).fetchone()
    to = date.fromisoformat(p["window_basis"][:10])
    current_run = None
    if run:
        error = None
        if run["status"] == "failed":
            error = {"code": run["error_code"], "message": run["error_message"], "nextAction": run["error_next"]}
        current_run = {"runId": run["id"], "kind": run["kind"], "status": run["status"], "step": run["step"],
                       "message": run["message"], "startedAt": run["created_at"], "updatedAt": run["updated_at"], "error": error}
    question = None
    if q:
        question = {"questionId": q["id"], "version": q["version"], "question": q["question"],
                    "options": json.loads(q["options_json"]), "multiSelect": bool(q["multi_select"]),
                    "allowFreeText": bool(q["allow_free_text"]),
                    "recommendation": json.loads(q["recommendation_json"]) if q["recommendation_json"] else None,
                    "status": q["status"]}
    return {
        "projectId": p["id"], "topic": p["topic"], "version": p["version"], "stage": p["stage"],
        "engine": p["engine"], "mock": bool(p["mock"]),
        "researchWindow": {"days": p["window_days"], "from": (to - timedelta(days=p["window_days"])).isoformat(),
                           "to": to.isoformat(), "basis": p["window_basis"]},
        "currentRun": current_run,
        "candidatesNote": p["candidates_note"],
        "candidates": [json.loads(r["data_json"]) for r in
                       c.execute("SELECT data_json FROM candidates WHERE project_id=? ORDER BY ord", (pid,))],
        "selection": {"candidateIds": json.loads(p["selection_json"]), "savedAtVersion": p["selection_version"]},
        "question": question,
        "researchNotes": json.loads(p["notes_json"]) if p["notes_json"] else None,
        "proposal": json.loads(p["proposal_json"]) if p["proposal_json"] else None,
        "storyboard": storyboard_state(c, p), "cards": cards_state(c, p),
        "finalApproval": final_state(c, p),
        "exportsReady": bool(final_state(c, p)["approved"]),
        "exports": export_mod.latest_exports(pid, 3) if p["storyboard_approved"] else [],
    }


# --- 엔드포인트 --------------------------------------------------------------
@app.get("/api/health")
def health():
    # 아직 확인하지 않은 항목은 ok로 추측하지 않고 unknown으로 둔다. 로그인·검색은 실제 작업이 성공해야 ok가 된다.
    name = default_engine_name()
    eng = ENGINES[name]
    out = {"ok": True, "engine": name, "mock": name == "mock",
           "checks": {**eng.health(), "agy": images.agy_health()},
           "childProcessGuard": procguard.STATUS}  # job-object = 서버가 강제 종료돼도 하위 프로세스가 함께 끝남(Windows)
    if name == "claude-cli":
        out["settings"] = {k: v for k, v in eng.settings().items() if k in ("model", "effort")}
        out["agyEnabled"] = images.agy_enabled()  # 꺼져 있으면 이미지는 agy 없이 Claude로 만든다
        browser = svg_render.browser_name()
        out["checks"]["browser"] = "found" if browser else "missing"  # SVG를 이미지로 바꿀 Edge/Chrome. 없으면 도형 도안으로 대체
        out["browser"] = browser
    return out


@app.post("/api/projects")
def create_project(body: CreateProject):
    topic = body.topic.strip()
    days = body.period.days if body.period else 7
    if not 1 <= len(topic) <= 200:
        raise ApiError(400, "invalid_input", "주제는 1~200자로 입력해 주세요.", "주제를 입력한 뒤 다시 시도해 주세요.")
    if days not in (7, 30):
        raise ApiError(400, "invalid_input", "조사 기간은 7일 또는 30일만 가능합니다.", "기간을 다시 고르세요.")

    def fn(c):
        pid = db.new_id("prj")
        win = basis_window(days)
        now = db.now_iso()
        eng_name = default_engine_name()
        c.execute("INSERT INTO projects(id, topic, stage, version, engine, mock, window_days, window_basis, created_at, updated_at) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?)", (pid, topic, "created", 1, eng_name, int(eng_name == "mock"), days, win["basis"], now, now))
        return 201, {"projectId": pid, "version": 1, "stage": "created", "researchWindow": win}, None

    return run_idempotent("", "create", body.requestId, {"topic": topic, "days": days}, fn)


@app.get("/api/projects")
def list_projects():
    with db.read() as c:
        rows = c.execute("SELECT id, topic, stage, mock, updated_at FROM projects ORDER BY updated_at DESC, rowid DESC LIMIT 50").fetchall()
    return {"projects": [{"projectId": r["id"], "topic": r["topic"], "stage": r["stage"],
                          "mock": bool(r["mock"]), "updatedAt": r["updated_at"]} for r in rows]}


@app.get("/api/projects/{pid}")
def get_project_state(pid: str):
    with db.read() as c:
        return build_state(c, pid)


@app.post("/api/projects/{pid}/research")
def start_research(pid: str, body: ResearchReq):
    if body.days is not None and body.days not in (7, 30):
        raise ApiError(400, "invalid_input", "조사 기간은 7일 또는 30일만 가능합니다.", "기간을 다시 고르세요.")

    def fn(c):
        p = get_project(c, pid)
        running = active_run(c, pid, "research")
        if running:  # 진행 중이면 중복 제작하지 않고 같은 작업을 알려 준다
            return 202, {"runId": running["id"], "status": running["status"], "stage": p["stage"]}, None
        check_version(p, body.version)
        if p["stage"] not in ("created", "candidates_ready", "selected"):
            raise wrong_stage(p, "created, candidates_ready, selected")
        days = body.days or p["window_days"]
        win = basis_window(days)
        run_id = new_run(c, p, "research", "조사를 준비하는 중")
        c.execute("UPDATE projects SET stage='researching', window_days=?, window_basis=?, version=version+1, updated_at=? WHERE id=?",
                  (days, win["basis"], db.now_iso(), pid))
        return 202, {"runId": run_id, "status": "queued", "stage": "researching"}, lambda: ENGINES[p["engine"]].start(run_id)

    return run_idempotent(pid, "research", body.requestId, {"version": body.version, "days": body.days}, fn)


@app.post("/api/projects/{pid}/selection")
def save_selection(pid: str, body: SelectionReq):
    ids = body.candidateIds

    def fn(c):
        p = get_project(c, pid)
        check_version(p, body.version)
        if p["stage"] not in ("candidates_ready", "selected"):
            raise wrong_stage(p, "candidates_ready, selected")
        if len(ids) > 3 or len(set(ids)) != len(ids):
            raise ApiError(400, "invalid_input", "후보는 중복 없이 최대 3개까지 고를 수 있습니다.", "선택을 줄인 뒤 다시 시도해 주세요.")
        known = {r["id"] for r in c.execute("SELECT id FROM candidates WHERE project_id=?", (pid,))}
        if not set(ids) <= known:
            raise ApiError(400, "invalid_input", "존재하지 않는 후보가 포함돼 있습니다.", "화면을 새로고침해 주세요.")
        stage = "selected" if ids else "candidates_ready"
        c.execute("UPDATE projects SET selection_json=?, selection_version=version+1, stage=?, version=version+1, updated_at=? WHERE id=?",
                  (json.dumps(ids), stage, db.now_iso(), pid))
        version = p["version"] + 1
        return 200, {"version": version, "selection": {"candidateIds": ids}, "stage": stage}, None

    return run_idempotent(pid, "selection", body.requestId, {"candidateIds": ids, "version": body.version}, fn)


@app.post("/api/projects/{pid}/deep-research")
def start_deep_research(pid: str, body: DeepReq):
    def fn(c):
        p = get_project(c, pid)
        check_version(p, body.version)
        if p["stage"] != "selected":
            raise wrong_stage(p, "selected")
        ids = json.loads(p["selection_json"])
        if not 1 <= len(ids) <= 3:
            raise ApiError(400, "invalid_input", "후보를 1~3개 고른 뒤에 시작할 수 있습니다.", "후보를 고르세요.")
        run_id = new_run(c, p, "deep_research", "심층 조사를 준비하는 중")
        c.execute("UPDATE projects SET stage='deep_research', version=version+1, updated_at=? WHERE id=?", (db.now_iso(), pid))
        return 202, {"runId": run_id, "status": "queued", "stage": "deep_research"}, lambda: ENGINES[p["engine"]].start(run_id)

    return run_idempotent(pid, "deep-research", body.requestId, {"version": body.version}, fn)


@app.post("/api/projects/{pid}/answers")
def answer_question(pid: str, body: AnswerReq):
    ans = body.answer
    free = (ans.freeText or "").strip()

    def fn(c):
        p = get_project(c, pid)
        q = c.execute("SELECT * FROM questions WHERE id=? AND project_id=?", (body.questionId, pid)).fetchone()
        if q is None or q["status"] != "open" or q["version"] != body.version:
            raise ApiError(409, "stale_question", "이미 답했거나 바뀐 질문입니다.", "화면을 새로고침해서 현재 질문을 확인하세요.")
        run = c.execute("SELECT * FROM runs WHERE id=?", (q["run_id"],)).fetchone()
        if run is None or run["status"] != "waiting_for_user":
            raise ApiError(409, "stale_question", "이 질문은 더 이상 답을 기다리지 않습니다.", "화면을 새로고침해 주세요.")
        options = {o["id"] for o in json.loads(q["options_json"])}
        if not set(ans.optionIds) <= options or len(set(ans.optionIds)) != len(ans.optionIds):
            raise ApiError(400, "invalid_input", "선택지에 없는 답변입니다.", "선택지를 다시 고르세요.")
        if len(ans.optionIds) > 1 and not q["multi_select"]:
            raise ApiError(400, "invalid_input", "이 질문은 하나만 고를 수 있습니다.", "하나만 고르세요.")
        if free and not q["allow_free_text"]:
            raise ApiError(400, "invalid_input", "이 질문은 직접 입력을 받지 않습니다.", "선택지에서 고르세요.")
        if len(free) > 500:
            raise ApiError(400, "invalid_input", "직접 입력은 500자까지 가능합니다.", "내용을 줄여 주세요.")
        if not ans.optionIds and not free:
            raise ApiError(400, "invalid_input", "선택하거나 직접 입력해 주세요.", "답변을 입력한 뒤 다시 시도해 주세요.")
        now = db.now_iso()
        c.execute("UPDATE questions SET status='answered', answer_json=?, answered_at=? WHERE id=?",
                  (json.dumps({"optionIds": ans.optionIds, "freeText": free or None}, ensure_ascii=False), now, q["id"]))
        c.execute("UPDATE runs SET status='running', step='answered', message='답변을 반영하는 중', updated_at=? WHERE id=?", (now, run["id"]))
        c.execute("UPDATE projects SET stage='deep_research', version=version+1, updated_at=? WHERE id=?", (now, pid))
        body_out = {"accepted": True, "version": p["version"] + 1, "stage": "deep_research",
                    "runId": run["id"], "status": "running"}
        return 200, body_out, lambda: ENGINES[p["engine"]].answer(run["id"])

    payload = {"questionId": body.questionId, "version": body.version,
               "optionIds": ans.optionIds, "freeText": free or None}
    return run_idempotent(pid, "answers", body.requestId, payload, fn)


@app.post("/api/runs/{run_id}/cancel")
def cancel_run(run_id: str):
    with db.tx() as c:
        run = c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if run is None:
            raise ApiError(404, "not_found", "작업을 찾을 수 없습니다.")
        cleanup = {"processesTerminated": 0, "partialFilesRemoved": False}
        eng_name = run["engine"] or "mock"
        if run["status"] == "cancelled":
            return {"runId": run_id, "status": "cancelled", "cleanup": cleanup}
        if run["status"] not in ("queued", "running", "waiting_for_user", "interrupted"):
            raise ApiError(409, "wrong_stage", f"이미 끝난 작업입니다({run['status']}).", "화면을 새로고침해 주세요.")
        c.execute("UPDATE runs SET status='cancelled', message='사용자가 취소했습니다', updated_at=? WHERE id=?", (db.now_iso(), run_id))
        c.execute("UPDATE questions SET status='cancelled' WHERE run_id=? AND status='open'", (run_id,))
        c.execute("UPDATE projects SET stage=?, version=version+1, updated_at=? WHERE id=?",
                  (stage_before(c, run), db.now_iso(), run["project_id"]))
        runs_mod.reset_generating_cards(c, run)
    cleanup["processesTerminated"] = ENGINES[eng_name].cancel(run_id)  # 실행 중인 하위 프로세스(트리)까지 종료
    return {"runId": run_id, "status": "cancelled", "cleanup": cleanup}


@app.post("/api/runs/{run_id}/resume")
def resume_run(run_id: str, body: ResumeReq):
    if body.mode not in ("continue", "restart"):
        raise ApiError(400, "invalid_input", "mode는 continue 또는 restart여야 합니다.")
    with db.read() as c:
        row = c.execute("SELECT project_id FROM runs WHERE id=?", (run_id,)).fetchone()
    if row is None:
        raise ApiError(404, "not_found", "작업을 찾을 수 없습니다.")
    pid = row["project_id"]

    def fn(c):
        run = c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if run["status"] != "interrupted":
            raise ApiError(409, "wrong_stage", f"중단된 작업만 이어갈 수 있습니다({run['status']}).", "화면을 새로고침해 주세요.")
        answered = c.execute("SELECT 1 FROM questions WHERE run_id=? AND status='answered'", (run_id,)).fetchone()
        c.execute("UPDATE runs SET status='queued', step='queued', message='중단된 작업을 다시 시작합니다', updated_at=? WHERE id=?",
                  (db.now_iso(), run_id))
        stage = {"research": "researching", "storyboard": "storyboarding", "images": "producing"}.get(run["kind"], "deep_research")
        if body.mode == "restart" or not answered:
            c.execute("UPDATE questions SET status='superseded' WHERE run_id=? AND status='open'", (run_id,))
        c.execute("UPDATE projects SET stage=?, version=version+1, updated_at=? WHERE id=?", (stage, db.now_iso(), pid))
        return 202, {"runId": run_id, "status": "queued", "stage": stage}, lambda: ENGINES[run["engine"] or "mock"].resume(run_id, body.mode)

    return run_idempotent(pid, "resume", body.requestId, {"runId": run_id, "mode": body.mode}, fn)


# --- 스토리보드·이미지 -----------------------------------------------------------
def _start_storyboard(pid: str, endpoint: str, body, fmt: str | None, instruction: str | None, hook_index: int | None):
    def fn(c):
        p = get_project(c, pid)
        running = active_run(c, pid, "storyboard")
        if running:  # 진행 중이면 새 작업을 만들지 않는다
            return 202, {"runId": running["id"], "status": running["status"], "stage": p["stage"]}, None
        check_version(p, body.version)
        if p["stage"] not in ("proposal_ready", "storyboard_ready"):
            raise wrong_stage(p, "proposal_ready, storyboard_ready")
        proposal = json.loads(p["proposal_json"])
        use_fmt = fmt
        if use_fmt is None:  # 변경 요청이면 지금 스토리보드의 형식을 유지한다
            cur = c.execute("SELECT format FROM storyboards WHERE project_id=? AND version=?", (pid, p["storyboard_version"])).fetchone()
            use_fmt = cur["format"] if cur else proposal["format"]
        if use_fmt not in ("list", "story"):
            raise ApiError(400, "invalid_input", "형식은 list(목록형) 또는 story(이야기형)여야 합니다.", "형식을 고른 뒤 다시 시도해 주세요.")
        if hook_index is not None and not 0 <= hook_index < len(proposal["hooks"]):
            raise ApiError(400, "invalid_input", "후킹 문구 번호가 올바르지 않습니다.", "목록에서 다시 고르세요.")
        text = (instruction or "").strip()
        if len(text) > 500:
            raise ApiError(400, "invalid_input", "변경 요청은 500자까지 가능합니다.", "내용을 줄여 주세요.")
        hook = proposal["hooks"][hook_index] if hook_index is not None else None
        run_id = new_run(c, p, "storyboard", "스토리보드를 준비하는 중", {"format": use_fmt, "hook": hook, "instruction": text or None})
        c.execute("UPDATE projects SET stage='storyboarding', version=version+1, updated_at=? WHERE id=?", (db.now_iso(), pid))
        return 202, {"runId": run_id, "status": "queued", "stage": "storyboarding"}, lambda: ENGINES[p["engine"]].start(run_id)

    payload = {"format": fmt, "hookIndex": hook_index, "instruction": instruction, "version": body.version}
    return run_idempotent(pid, endpoint, body.requestId, payload, fn)


@app.post("/api/projects/{pid}/storyboard")
def make_storyboard(pid: str, body: StoryboardReq):
    return _start_storyboard(pid, "storyboard", body, body.format, body.instruction, body.hookIndex)


@app.post("/api/projects/{pid}/revisions")
def revisions(pid: str, body: RevisionReq):
    if body.target not in ("storyboard", "cards"):
        raise ApiError(400, "invalid_input", "target은 storyboard 또는 cards여야 합니다.", "요청을 확인해 주세요.")
    if not body.instruction.strip() or len(body.instruction) > 500:
        raise ApiError(400, "invalid_input", "변경할 내용을 1~500자로 입력해 주세요.", "변경 요청을 적은 뒤 다시 시도해 주세요.")
    if body.target == "storyboard":
        return _start_storyboard(pid, "revisions", body, None, body.instruction, None)
    if len(body.cardIds) != 1:
        raise ApiError(400, "invalid_input", "카드는 한 번에 한 장씩 골라 고칠 수 있습니다.", "카드를 하나만 골라 주세요.")
    cid, instruction = body.cardIds[0], body.instruction.strip()

    def fn(c):
        p = get_project(c, pid)
        check_version(p, body.version)
        if p["stage"] != "review":
            raise wrong_stage(p, "review")
        row = c.execute("SELECT * FROM cards WHERE project_id=? AND card_id=?", (pid, cid)).fetchone()
        if row is None:
            raise ApiError(400, "invalid_input", "존재하지 않는 카드입니다.", "화면을 새로고침해 주세요.")
        if row["status"] != "ready":
            raise ApiError(409, "image_not_ready", "이미지가 준비된 카드만 고칠 수 있습니다.", "먼저 이 카드의 이미지를 만들어 주세요.")
        run_id = new_run(c, p, "card_revision", f"카드 {row['ord']}번 수정 요청을 보내는 중",
                         {"cardId": cid, "cardIds": [cid], "instruction": instruction, "recompose": True})
        c.execute("UPDATE projects SET stage='producing', version=version+1, updated_at=? WHERE id=?", (db.now_iso(), pid))
        return 202, {"runId": run_id, "status": "queued", "stage": "producing", "cardIds": [cid]}, lambda: ENGINES[p["engine"]].start(run_id)

    return run_idempotent(pid, "revisions-card", body.requestId, {"cardIds": body.cardIds, "instruction": instruction, "version": body.version}, fn)


def approve_final(pid: str, body: "ApproveReq"):
    def fn(c):
        p = get_project(c, pid)
        check_version(p, body.version)
        if p["stage"] != "review" or not p["storyboard_approved"]:
            raise wrong_stage(p, "review")
        if active_run(c, pid, "card_revision") or active_run(c, pid, "images"):
            raise ApiError(409, "wrong_stage", "카드를 만드는 중에는 최종 승인할 수 없습니다.", "작업이 끝난 뒤 다시 시도해 주세요.")
        st = cardfiles.final_status(c, pid)
        if not st["ready"]:
            raise ApiError(409, "final_not_ready", "모든 카드에 글자를 올리고 검사를 통과해야 최종 승인할 수 있습니다.",
                           "아래 카드의 글자를 올리거나 고쳐 주세요.", False, {"blockers": st["blockers"]})
        now = db.now_iso()
        c.execute("UPDATE projects SET final_approved_sig=?, final_approved_at=?, version=version+1, updated_at=? WHERE id=?", (st["signature"], now, now, pid))
        return 200, {"finalApproval": {"approved": True, "resultVersion": p["version"] + 1, "approvedAt": now}}, None

    return run_idempotent(pid, "approve-final", body.requestId, {"stage": "final", "version": body.version}, fn)


@app.post("/api/projects/{pid}/approve")
def approve(pid: str, body: ApproveReq):
    if body.stage == "final":
        return approve_final(pid, body)
    if body.stage != "storyboard":
        raise ApiError(400, "invalid_input", "stage는 storyboard 또는 final이어야 합니다.")

    def fn(c):
        p = get_project(c, pid)
        check_version(p, body.version)
        if p["stage"] != "storyboard_ready" or not p["storyboard_version"]:
            raise wrong_stage(p, "storyboard_ready")
        sb = c.execute("SELECT cards_json FROM storyboards WHERE project_id=? AND version=?", (pid, p["storyboard_version"])).fetchone()
        c.execute("DELETE FROM cards WHERE project_id=?", (pid,))
        now = db.now_iso()
        for card in json.loads(sb["cards_json"]):
            c.execute("INSERT INTO cards(project_id, card_id, storyboard_version, ord, status, current_version, updated_at) "
                      "VALUES (?,?,?,?, 'pending', 0, ?)", (pid, card["id"], p["storyboard_version"], card["order"], now))
        run_id = new_run(c, p, "images", "이미지 제작을 준비하는 중")
        c.execute("UPDATE projects SET stage='producing', storyboard_approved=1, version=version+1, updated_at=? WHERE id=?", (now, pid))
        return 202, {"runId": run_id, "status": "queued", "stage": "producing", "storyboardVersion": p["storyboard_version"]}, \
            lambda: ENGINES[p["engine"]].start(run_id)

    return run_idempotent(pid, "approve", body.requestId, {"stage": body.stage, "version": body.version}, fn)


@app.post("/api/projects/{pid}/cards/retry")
def retry_cards(pid: str, body: RetryCardsReq):
    def fn(c):
        p = get_project(c, pid)
        running = active_run(c, pid, "images")
        if running:
            return 202, {"runId": running["id"], "status": running["status"], "stage": p["stage"]}, None
        check_version(p, body.version)
        if p["stage"] != "review":
            raise wrong_stage(p, "review")
        rows = c.execute("SELECT card_id, status FROM cards WHERE project_id=?", (pid,)).fetchall()
        known = {r["card_id"]: r["status"] for r in rows}
        if body.cardIds is not None and not set(body.cardIds) <= set(known):
            raise ApiError(400, "invalid_input", "존재하지 않는 카드가 포함돼 있습니다.", "화면을 새로고침해 주세요.")
        targets = list(body.cardIds) if body.cardIds is not None else [k for k, v in known.items() if v in ("failed", "pending")]
        if not targets:
            raise ApiError(400, "invalid_input", "다시 만들 카드가 없습니다.", "카드를 골라 주세요.")
        for cid in targets:  # 이미 만든 카드를 골랐다면 새 버전으로 다시 만들고 이전 버전은 보관한다
            c.execute("UPDATE cards SET status='pending', error_code=NULL, error_message=NULL, error_next=NULL, updated_at=? "
                      "WHERE project_id=? AND card_id=?", (db.now_iso(), pid, cid))
        run_id = new_run(c, p, "images", "이미지 제작을 준비하는 중", {"cardIds": targets})  # 이 작업은 고른 카드만 만든다
        c.execute("UPDATE projects SET stage='producing', version=version+1, updated_at=? WHERE id=?", (db.now_iso(), pid))
        return 202, {"runId": run_id, "status": "queued", "stage": "producing", "cardIds": targets}, lambda: ENGINES[p["engine"]].start(run_id)

    return run_idempotent(pid, "cards-retry", body.requestId, {"cardIds": body.cardIds, "version": body.version}, fn)


def typeset_target(c, pid: str, cid: str, need_review: bool):
    p = get_project(c, pid)
    row = c.execute("SELECT * FROM cards WHERE project_id=? AND card_id=?", (pid, cid)).fetchone()
    if row is None:
        raise ApiError(404, "not_found", "카드를 찾을 수 없습니다.", "화면을 새로고침해 주세요.")
    if need_review and p["stage"] != "review":
        raise wrong_stage(p, "review")
    ver = c.execute("SELECT * FROM card_versions WHERE project_id=? AND card_id=? AND version=?",
                    (pid, cid, row["current_version"])).fetchone()
    if row["status"] != "ready" or ver is None:
        raise ApiError(409, "image_not_ready", "이미지가 아직 없는 카드에는 글자를 올릴 수 없습니다.", "먼저 이 카드의 이미지를 만들어 주세요.")
    sb = c.execute("SELECT cards_json FROM storyboards WHERE project_id=? AND version=?", (pid, row["storyboard_version"])).fetchone()
    card = next((x for x in json.loads(sb["cards_json"]) if x["id"] == cid), {})
    base = db.DATA_DIR / "projects" / pid / ver["file_rel"]
    return card, base, ver["version"]


def typeset_response(pid: str, cid: str, image_version: int, result: dict, url_name: str, saved_at=None) -> dict:
    rel = f"/api/projects/{pid}/files/cards/{cid}/" + url_name
    return {"cardId": cid, "imageVersion": image_version, "imageUrl": rel + f"?t={int(datetime.now().timestamp() * 1000)}",
            "ok": result["ok"], "checks": result["checks"], "used": result["used"], "savedAt": saved_at}


@app.get("/api/projects/{pid}/cards/{cid}/typeset")
def get_typeset(pid: str, cid: str):
    with db.read() as c:
        card, base, ver = typeset_target(c, pid, cid, False)
    saved = typeset_saved(pid, cid, ver)
    layout = cardfiles.saved_layout(pid, cid) or cardfiles.default_layout(card)  # 그림을 새로 만들어도 고친 글과 배치를 이어 쓴다
    return {"cardId": cid, "imageVersion": ver, "saved": bool(saved), "layout": layout, "checks": (saved or {}).get("checks"),
            "baseUrl": f"/api/projects/{pid}/files/" + base.relative_to(db.DATA_DIR / "projects" / pid).as_posix(),
            "finalUrl": f"/api/projects/{pid}/files/cards/{cid}/v{ver}/final.png" if saved else None,
            "limits": {"title": 120, "body": 400}}


@app.post("/api/projects/{pid}/cards/{cid}/typeset/preview")
def preview_typeset(pid: str, cid: str, body: TypesetReq):
    """저장하지 않고 미리보기 이미지만 만든다(휴대폰 미리보기용). 카드·단계 상태는 바뀌지 않는다."""
    with db.read() as c:
        _card, base, ver = typeset_target(c, pid, cid, True)
    try:
        result = typeset.compose(base, body.layout.model_dump(), card_paths(pid, cid) / "preview.png")
    except typeset.TypesetError as e:
        raise ApiError(409, "typeset_unavailable", str(e), "글꼴을 확인한 뒤 다시 시도해 주세요.")
    return typeset_response(pid, cid, ver, result, "preview.png")


@app.put("/api/projects/{pid}/cards/{cid}/typeset")
def save_typeset(pid: str, cid: str, body: TypesetReq):
    """글자를 올린 합성본을 저장한다. 같은 요청을 다시 보내도 같은 결과다. 그림 파일과 승인한 스토리보드는 바뀌지 않는다."""
    with db.read() as c:
        _card, base, ver = typeset_target(c, pid, cid, True)
    try:
        result = cardfiles.compose_and_save(pid, cid, base, ver, body.layout.model_dump())  # 저장 뒤 파일을 다시 열어 1080×1350 PNG인지 확인한다
    except typeset.TypesetError as e:
        raise ApiError(409, "typeset_unavailable", str(e), "글꼴을 확인한 뒤 다시 시도해 주세요.", True)
    return typeset_response(pid, cid, ver, result, f"v{ver}/final.png", result["savedAt"])


class RestoreReq(BaseModel):
    imageVersion: int
    version: int
    requestId: str = RequestId


@app.post("/api/projects/{pid}/cards/{cid}/restore")
def restore_card_image(pid: str, cid: str, body: RestoreReq):
    """이 카드의 현재 그림을 이전에 만든 버전으로 되돌린다. 새로 만들지 않으므로 Claude·agy를 부르지 않는다.
    저장해 둔 글자 배치를 그 그림 위에 다시 올리고, 합성본이 바뀌므로 최종 승인은 풀린다. 다른 카드는 바뀌지 않는다."""
    def fn(c):
        p = get_project(c, pid)
        check_version(p, body.version)
        if p["stage"] != "review":
            raise wrong_stage(p, "review")
        if active_run(c, pid, "card_revision") or active_run(c, pid, "images"):
            raise ApiError(409, "wrong_stage", "카드를 만드는 중에는 되돌릴 수 없습니다.", "작업이 끝난 뒤 다시 시도해 주세요.")
        row = c.execute("SELECT * FROM cards WHERE project_id=? AND card_id=?", (pid, cid)).fetchone()
        if row is None:
            raise ApiError(404, "not_found", "카드를 찾을 수 없습니다.", "화면을 새로고침해 주세요.")
        if row["status"] != "ready":
            raise ApiError(409, "image_not_ready", "이미지가 준비된 카드만 되돌릴 수 있습니다.", "먼저 이 카드의 이미지를 만들어 주세요.")
        if body.imageVersion == row["current_version"]:
            raise ApiError(400, "invalid_input", "이미 이 그림을 쓰고 있습니다.", "다른 버전을 골라 주세요.")
        ver = c.execute("SELECT * FROM card_versions WHERE project_id=? AND card_id=? AND version=?", (pid, cid, body.imageVersion)).fetchone()
        base = db.DATA_DIR / "projects" / pid / ver["file_rel"] if ver else None
        if ver is None or not base.is_file():
            raise ApiError(400, "invalid_input", "그 버전의 그림 파일을 찾을 수 없습니다.", "화면을 새로고침해 주세요.")
        before = row["current_version"]
        try:
            composed = cardfiles.recompose_saved(pid, cid, base, body.imageVersion)  # 글자 배치가 없으면 글자를 올리지 않은 상태로 돌아간다
        except typeset.TypesetError as e:
            raise ApiError(409, "typeset_unavailable", str(e), "글꼴을 확인한 뒤 다시 시도해 주세요.", True)
        c.execute("UPDATE cards SET current_version=?, updated_at=? WHERE project_id=? AND card_id=?", (body.imageVersion, db.now_iso(), pid, cid))
        c.execute("UPDATE projects SET version=version+1, updated_at=? WHERE id=?", (db.now_iso(), pid))
        cardfiles.log_revision(pid, cid, {"instruction": f"이전 그림(v{body.imageVersion})으로 되돌리기", "kind": "restore",
                                          "reason": f"그림 v{before} → v{body.imageVersion}. 새로 만들지 않고 보관해 둔 그림을 다시 썼습니다.", "notes": []})
        return 200, {"cardId": cid, "imageVersion": body.imageVersion, "previousVersion": before, "textRecomposed": bool(composed)}, None

    return run_idempotent(pid, "restore-card", body.requestId, {"cardId": cid, "imageVersion": body.imageVersion, "version": body.version}, fn)


class ExportReq(BaseModel):
    version: int
    requestId: str = RequestId


@app.post("/api/projects/{pid}/exports")
def create_export(pid: str, body: ExportReq):
    """최종 승인된 카드를 내보내기 폴더에 쓰고, 그 파일을 기준으로 누락·글자 잘림·출처·날짜를 확인한다."""
    def fn(c):
        p = get_project(c, pid)
        check_version(p, body.version)
        if p["stage"] != "review" or not cardfiles.approval_state(c, p)["approved"]:
            raise ApiError(409, "not_approved", "최종 승인된 카드가 없습니다. 카드를 고쳤다면 최종 승인을 다시 받아야 합니다.", "최종 승인을 눌러 주세요.")
        if active_run(c, pid, "card_revision") or active_run(c, pid, "images"):
            raise ApiError(409, "wrong_stage", "카드를 만드는 중에는 내보낼 수 없습니다.", "작업이 끝난 뒤 다시 시도해 주세요.")
        try:
            res = export_mod.build_export(pid)
        except (OSError, KeyError, ValueError) as e:
            raise ApiError(500, "export_failed", f"내보내기 파일을 만들지 못했습니다: {type(e).__name__}", "다시 시도해 주세요.", True)
        c.execute("UPDATE projects SET version=version+1, updated_at=? WHERE id=?", (db.now_iso(), pid))
        return 201, res, None

    return run_idempotent(pid, "export", body.requestId, {"version": body.version}, fn)


@app.get("/api/projects/{pid}/exports")
def list_exports(pid: str):
    with db.read() as c:
        p = get_project(c, pid)
        if p["stage"] != "review" or not cardfiles.approval_state(c, p)["approved"]:
            raise ApiError(409, "not_approved", "최종 승인된 카드가 없습니다.", "최종 승인을 받은 뒤 내보내 주세요.")
    return {"exports": export_mod.latest_exports(pid, 10)}


@app.get("/api/projects/{pid}/exports/{ex_id}/{name:path}")
def download_export(pid: str, ex_id: str, name: str):
    """내보낸 파일 내려받기. 허용한 파일 이름(카드 PNG, ZIP, 기록)만 제공한다."""
    ok_name = name in export_mod.DOWNLOADABLE or (name.startswith("cards/card_") and name.endswith(".png") and "/" not in name[6:] and ".." not in name)
    base = (export_mod.exports_root(pid)).resolve()
    target = (base / ex_id / name).resolve()
    if not ok_name or not ex_id.startswith("ex_") or base not in target.parents or not target.is_file():
        raise ApiError(404, "not_found", "파일을 찾을 수 없습니다.")
    return FileResponse(target, filename=target.name, headers={"Cache-Control": "no-cache"})


@app.get("/api/projects/{pid}/files/{path:path}")
def project_file(pid: str, path: str):
    """프로젝트 폴더 안의 카드 이미지·도안·기록만 내려 준다. 경로 탈출과 다른 파일은 거절한다."""
    base = (db.DATA_DIR / "projects" / pid).resolve()
    target = (base / path).resolve()
    ok = (base in target.parents and target.is_file() and target.parts[len(base.parts)] == "cards"
          and target.suffix.lower() in (".png", ".json") and "agy_work" not in target.parts)
    if not ok:
        raise ApiError(404, "not_found", "파일을 찾을 수 없습니다.")
    return FileResponse(target, headers={"Cache-Control": "no-cache"})


# --- 화면 --------------------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
