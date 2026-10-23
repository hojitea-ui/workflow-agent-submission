"""보고서·과제 글 검수 웹 서버. 화면과 영속 상태를 관리하고 실제 작업은 엔진(Claude CLI)이 맡는다. 계약은 API_SPEC.md 참고."""
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import db, doc, procguard, runs
from .engine import Engine

STATIC_DIR = Path(__file__).resolve().parent / "static"
ENGINE = Engine()
OUTPUTS = ("final.md", "review_report.md")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, next_action: str = "", retryable: bool = False):
        self.status, self.code, self.message, self.next_action, self.retryable = status, code, message, next_action, retryable


def error_body(code, message, next_action="", retryable=False) -> dict:
    return {"error": {"code": code, "message": message, "retryable": retryable, "nextAction": next_action}}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    procguard.protect_current_process()  # 서버가 강제로 끝나도 claude 하위 프로세스가 남지 않게 한다
    db.init()
    with db.tx() as c:  # 서버가 꺼지면서 끊긴 작업을 표시하고, 그 프로젝트는 다시 시작할 수 있는 단계로 돌린다
        for r in c.execute("SELECT id, project_id, kind FROM runs WHERE status IN ('queued','running')").fetchall():
            c.execute("UPDATE runs SET status='interrupted', updated_at=? WHERE id=?", (db.now_iso(), r["id"]))
            c.execute("UPDATE projects SET stage=?, updated_at=? WHERE id=?", (runs.stage_before(r["kind"], True), db.now_iso(), r["project_id"]))
    yield
    ENGINE.shutdown()


app = FastAPI(title="검수 에이전트", lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])


@app.exception_handler(ApiError)
async def _api_error(_req: Request, exc: ApiError):
    return JSONResponse(error_body(exc.code, exc.message, exc.next_action, exc.retryable), status_code=exc.status)


@app.exception_handler(RequestValidationError)
async def _validation_error(_req: Request, exc: RequestValidationError):
    fields = [".".join(str(p) for p in e["loc"][1:]) or "body" for e in exc.errors()]
    return JSONResponse(error_body("invalid_input", "요청 형식이 올바르지 않습니다: " + ", ".join(fields), "입력을 확인한 뒤 다시 시도해 주세요."), status_code=400)


@app.exception_handler(StarletteHTTPException)
async def _http_error(_req: Request, exc: StarletteHTTPException):
    return JSONResponse(error_body("not_found" if exc.status_code == 404 else "invalid_input", "요청한 주소를 찾을 수 없거나 허용되지 않는 요청입니다."), status_code=exc.status_code)


class CreateProject(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    conditions: str = Field(default="", max_length=1000)
    body: str = Field(min_length=1, max_length=doc.MAX_BODY)


class VersionReq(BaseModel):
    version: int


class ChoicesReq(BaseModel):
    choices: dict


class MaterialsReq(BaseModel):
    version: int
    materials: dict  # {"f3": "붙여 넣은 자료", ...} 이번 라운드 지적 id -> 자료


class NextRoundReq(BaseModel):
    version: int
    issues: list[str]  # "f2.1" 꼴: 이번 라운드 지적 id와 그 안의 새 문제 번호


def get_project(c, pid: str):
    p = c.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    if p is None:
        raise ApiError(404, "not_found", "프로젝트를 찾을 수 없습니다.", "처음 화면에서 프로젝트를 다시 고르세요.")
    return p


def active_run(c, pid: str):
    return c.execute("SELECT * FROM runs WHERE project_id=? AND status IN ('queued','running') ORDER BY rowid DESC LIMIT 1", (pid,)).fetchone()


def check_version(p, version: int) -> None:
    if p["version"] != version:
        raise ApiError(409, "stale_version", "화면이 오래되었습니다. 다른 곳에서 상태가 바뀌었습니다.", "화면을 새로고침한 뒤 다시 시도해 주세요.")


def prepare_result(result: dict) -> dict:
    """저장된 결과를 화면용으로 다듬는다: 변경 표시(diff)를 읽을 때 계산하고, 이전 버전이 만든 결과(새 문제가 글자뿐인 것)도 같은 모양으로 맞춘다."""
    for it in result.get("items", []):
        it["diff"] = doc.collapse_ops(doc.diff_ops(it["before"], it["after"])) if it["verdict"] != "skipped" else []
        it["newIssues"] = [ni if isinstance(ni, dict) else {"id": str(k), "text": ni, "severity": "low", "paragraphId": it["finding"]["paragraphId"]}
                           for k, ni in enumerate(it.get("newIssues", []), start=1)]
    for it in result.get("items", []):
        it.setdefault("held", [{"claim": it["gap"], "needed": ""}] if it.get("gap") else [])  # 이전 버전 결과: 자료 부족 설명을 보류 항목으로 본다
    if result.get("finalParagraphs"):  # 보류 목록은 지금 글에서 다시 읽는다(옛 결과도 보류마다 한 줄로 나뉜다)
        result["gaps"] = doc.gaps_in(result["finalParagraphs"])
    result["canContinue"] = "finalParagraphs" in result  # 이전 버전이 만든 결과는 고친 글이 저장돼 있지 않아 이어서 고칠 수 없다
    result["remaining"] = doc.remaining_issues(result.get("items", []))  # 항상 지금 규칙으로 다시 센다
    return result


def all_rounds(p) -> list[dict]:
    """끝난 라운드들과 지금 라운드를 순서대로. 리포트를 다시 만들 때 쓴다."""
    rounds = json.loads(p["history_json"] or "[]")
    if p["result_json"]:
        rounds.append({"round": p["round"], "findings": json.loads(p["findings_json"]), "result": json.loads(p["result_json"])})
    return rounds


def build_state(c, pid: str) -> dict:
    p = get_project(c, pid)
    run = c.execute("SELECT * FROM runs WHERE project_id=? ORDER BY rowid DESC LIMIT 1", (pid,)).fetchone()
    current = None
    if run:
        err = {"code": run["error_code"], "message": run["error_message"], "nextAction": run["error_next"]} if run["status"] == "failed" else None
        current = {"runId": run["id"], "kind": run["kind"], "status": run["status"], "step": run["step"], "message": run["message"],
                   "startedAt": run["created_at"], "updatedAt": run["updated_at"], "error": err}
    paragraphs = json.loads(p["paragraphs_json"]) if p["paragraphs_json"] else doc.split_paragraphs(p["body"])
    result = prepare_result(json.loads(p["result_json"])) if p["result_json"] else None
    history = [{"round": h["round"], "findings": h["findings"], "result": prepare_result(h["result"])} for h in json.loads(p["history_json"] or "[]")]
    out_dir = db.DATA_DIR / "projects" / pid / "outputs"
    return {"projectId": pid, "title": p["title"], "conditions": p["conditions"], "stage": p["stage"], "version": p["version"],
            "paragraphs": paragraphs, "findings": json.loads(p["findings_json"]) if p["findings_json"] else None,
            "choices": json.loads(p["choices_json"]), "result": result, "round": p["round"], "maxRounds": doc.MAX_ROUNDS, "history": history,
            "currentRun": current, "outputs": [n for n in OUTPUTS if (out_dir / n).is_file()] if p["stage"] == "done" else [],
            "createdAt": p["created_at"], "updatedAt": p["updated_at"]}


# --- 엔드포인트 ---
@app.get("/api/health")
def health():
    return {"ok": True, "engine": ENGINE.name, "checks": ENGINE.health(), "settings": ENGINE.settings() | {"timeout": None},
            "childProcessGuard": procguard.STATUS}


@app.post("/api/projects")
def create_project(body: CreateProject):
    paragraphs = doc.split_paragraphs(body.body)
    if not paragraphs:
        raise ApiError(400, "invalid_input", "글이 비어 있습니다.", "검수할 글을 붙여 넣거나 파일을 올려 주세요.")
    pid = db.new_id("rv")
    now = db.now_iso()
    with db.tx() as c:
        c.execute("INSERT INTO projects(id, title, conditions, body, stage, version, created_at, updated_at) VALUES (?,?,?,?, 'analyzing', 1, ?, ?)",
                  (pid, body.title.strip(), body.conditions.strip(), body.body.strip(), now, now))
        run_id = runs.new_run(c, pid, "analyze", "검수를 준비하는 중")
    ENGINE.start(run_id)
    return JSONResponse({"projectId": pid, "runId": run_id, "stage": "analyzing", "paragraphs": len(paragraphs)}, status_code=201)


@app.get("/api/projects")
def list_projects():
    with db.read() as c:
        rows = c.execute("SELECT id, title, stage, updated_at FROM projects ORDER BY updated_at DESC, rowid DESC LIMIT 50").fetchall()
    return {"projects": [{"projectId": r["id"], "title": r["title"], "stage": r["stage"], "updatedAt": r["updated_at"]} for r in rows]}


@app.get("/api/projects/{pid}")
def get_state(pid: str):
    with db.read() as c:
        return build_state(c, pid)


@app.post("/api/projects/{pid}/analyze")
def reanalyze(pid: str, body: VersionReq):
    """분석이 실패·중단됐을 때 다시 시도한다(처음 단계이거나 분석 결과가 아직 없을 때만)."""
    with db.tx() as c:
        p = get_project(c, pid)
        check_version(p, body.version)
        if active_run(c, pid):
            return JSONResponse({"runId": active_run(c, pid)["id"], "stage": p["stage"]}, status_code=202)
        if p["stage"] != "created":
            raise ApiError(409, "wrong_stage", f"지금 단계({p['stage']})에서는 다시 분석할 수 없습니다.", "화면을 새로고침해서 현재 단계를 확인하세요.")
        c.execute("UPDATE projects SET stage='analyzing', version=version+1, updated_at=? WHERE id=?", (db.now_iso(), pid))
        run_id = runs.new_run(c, pid, "analyze", "검수를 준비하는 중")
    ENGINE.start(run_id)
    return JSONResponse({"runId": run_id, "stage": "analyzing"}, status_code=202)


@app.put("/api/projects/{pid}/choices")
def save_choices(pid: str, body: ChoicesReq):
    """지적마다 고른 방향을 저장한다(a / b / skip / {"custom": 글}). 분석 결과가 나온 단계에서만 가능하다."""
    with db.tx() as c:
        p = get_project(c, pid)
        if p["stage"] != "findings_ready":
            raise ApiError(409, "wrong_stage", f"지금 단계({p['stage']})에서는 선택을 저장할 수 없습니다.", "화면을 새로고침해서 현재 단계를 확인하세요.")
        clean = doc.clean_choices(body.choices, json.loads(p["findings_json"]))
        c.execute("UPDATE projects SET choices_json=?, updated_at=? WHERE id=?", (json.dumps(clean, ensure_ascii=False), db.now_iso(), pid))
    return {"choices": clean}


@app.post("/api/projects/{pid}/revise")
def revise(pid: str, body: VersionReq):
    with db.tx() as c:
        p = get_project(c, pid)
        check_version(p, body.version)
        if p["stage"] != "findings_ready":
            raise ApiError(409, "wrong_stage", f"지금 단계({p['stage']})에서는 수정을 시작할 수 없습니다.", "화면을 새로고침해서 현재 단계를 확인하세요.")
        findings = json.loads(p["findings_json"])
        picked = [f for f in findings if doc.chosen_direction(f, json.loads(p["choices_json"]).get(f["id"]))]
        if not picked:
            raise ApiError(400, "invalid_input", "수정할 지적을 하나 이상 골라 주세요.", "지적마다 방향을 고르거나 직접 쓴 뒤 다시 눌러 주세요.")
        c.execute("UPDATE projects SET stage='revising', version=version+1, updated_at=? WHERE id=?", (db.now_iso(), pid))
        run_id = runs.new_run(c, pid, "revise", "수정을 준비하는 중")
    ENGINE.start(run_id)
    return JSONResponse({"runId": run_id, "stage": "revising", "selected": [f["id"] for f in picked]}, status_code=202)


@app.post("/api/projects/{pid}/next-round")
def next_round(pid: str, body: NextRoundReq):
    """재검수가 찾은 새 문제(중요도 높음·보통)를 다음 라운드의 지적으로 잇는다. 고친 글을 이어서 쓰고, 선택·수정·재검수는 처음과 같다."""
    with db.tx() as c:
        p = get_project(c, pid)
        check_version(p, body.version)
        if p["stage"] != "done":
            raise ApiError(409, "wrong_stage", f"지금 단계({p['stage']})에서는 다음 라운드를 시작할 수 없습니다.", "화면을 새로고침해서 현재 단계를 확인하세요.")
        if p["round"] >= doc.MAX_ROUNDS:
            raise ApiError(409, "round_limit", f"라운드는 최대 {doc.MAX_ROUNDS}번까지입니다.", "남은 문제는 리포트를 보고 직접 고치거나 새 검수로 시작하세요.")
        result = json.loads(p["result_json"])
        if "finalParagraphs" not in result:
            raise ApiError(409, "wrong_stage", "이 결과는 이전 버전에서 만들어져 다음 라운드로 이을 수 없습니다.", "같은 글로 새 검수를 시작하세요.")
        wanted = list(dict.fromkeys(body.issues))
        picked = []
        for ref in wanted:
            fid, _, num = ref.partition(".")
            it = next((x for x in result["items"] if x["finding"]["id"] == fid), None)
            issue = next((x for x in (it or {}).get("newIssues", []) if isinstance(x, dict) and x.get("id") == num), None)
            if not issue or issue.get("severity") not in ("high", "medium") or "directions" not in issue:
                raise ApiError(400, "invalid_input", f"'{ref}'는 이을 수 있는 새 문제가 아닙니다.", "중요도 높음·보통인 새 문제만 고를 수 있습니다.")
            picked.append((fid, issue))
        if not picked:
            raise ApiError(400, "invalid_input", "이어서 고칠 새 문제를 하나 이상 골라 주세요.", "새 문제 옆의 체크 상자를 선택하세요.")
        picked.sort(key=lambda t: 0 if t[1]["severity"] == "high" else 1)  # 높음 먼저(같은 중요도는 고른 순서)
        findings = []
        for k, (fid, issue) in enumerate(picked, start=1):
            f = doc.issue_to_finding(fid, issue, p["round"])
            f["id"], f["priority"] = f"f{k}", k
            findings.append(f)
        history = json.loads(p["history_json"] or "[]") + [{"round": p["round"], "findings": json.loads(p["findings_json"]),
                                                            "choices": json.loads(p["choices_json"]), "result": result}]
        c.execute("UPDATE projects SET stage='findings_ready', findings_json=?, choices_json='{}', result_json=NULL, paragraphs_json=?, round=round+1, "
                  "history_json=?, version=version+1, updated_at=? WHERE id=?",
                  (json.dumps(findings, ensure_ascii=False), json.dumps(result["finalParagraphs"], ensure_ascii=False),
                   json.dumps(history, ensure_ascii=False), db.now_iso(), pid))
    return {"round": p["round"] + 1, "findings": len(findings), "stage": "findings_ready"}


@app.post("/api/projects/{pid}/materials")
def supply_materials(pid: str, body: MaterialsReq):
    """근거가 부족해 보류한 항목에 사용자가 추가 자료를 붙여 넣으면, 그 항목을 다음 라운드의 지적으로 이어서 다시 고친다."""
    with db.tx() as c:
        p = get_project(c, pid)
        check_version(p, body.version)
        if p["stage"] != "done":
            raise ApiError(409, "wrong_stage", f"지금 단계({p['stage']})에서는 자료를 추가할 수 없습니다.", "화면을 새로고침해서 현재 단계를 확인하세요.")
        if p["round"] >= doc.MAX_ROUNDS:
            raise ApiError(409, "round_limit", f"라운드는 최대 {doc.MAX_ROUNDS}번까지입니다.", "보류한 항목은 리포트를 보고 직접 보완하거나 새 검수로 시작하세요.")
        result = json.loads(p["result_json"])
        if "finalParagraphs" not in result:
            raise ApiError(409, "wrong_stage", "이 결과는 이전 버전에서 만들어져 이어갈 수 없습니다.", "같은 글로 새 검수를 시작하세요.")
        findings = []
        for fid, text in body.materials.items():
            text = str(text or "").strip()
            if not text:
                continue
            it = next((x for x in result["items"] if x["finding"]["id"] == fid), None)
            if it is None or not it.get("held"):
                raise ApiError(400, "invalid_input", f"'{fid}'는 보류한 항목이 아닙니다.", "근거가 부족해 보류한 항목에만 자료를 붙일 수 있습니다.")
            if len(text) > 8000:
                raise ApiError(400, "invalid_input", "붙여 넣은 자료가 8,000자를 넘습니다.", "필요한 부분만 줄여서 다시 넣어 주세요.")
            findings.append(doc.held_to_finding(it, text, p["round"]))
        if not findings:
            raise ApiError(400, "invalid_input", "붙여 넣은 자료가 없습니다.", "보류한 항목 아래 칸에 자료(원문 발췌·메모)를 붙여 넣어 주세요.")
        for k, f in enumerate(findings, start=1):
            f["id"], f["priority"] = f"f{k}", k
        history = json.loads(p["history_json"] or "[]") + [{"round": p["round"], "findings": json.loads(p["findings_json"]),
                                                            "choices": json.loads(p["choices_json"]), "result": result}]
        c.execute("UPDATE projects SET stage='findings_ready', findings_json=?, choices_json='{}', result_json=NULL, paragraphs_json=?, round=round+1, "
                  "history_json=?, version=version+1, updated_at=? WHERE id=?",
                  (json.dumps(findings, ensure_ascii=False), json.dumps(result["finalParagraphs"], ensure_ascii=False),
                   json.dumps(history, ensure_ascii=False), db.now_iso(), pid))
    return {"round": p["round"] + 1, "findings": len(findings), "stage": "findings_ready"}


@app.post("/api/runs/{run_id}/cancel")
def cancel(run_id: str):
    with db.tx() as c:
        run = c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if run is None:
            raise ApiError(404, "not_found", "작업을 찾을 수 없습니다.")
        if run["status"] not in runs.ACTIVE:
            raise ApiError(409, "wrong_stage", "이미 끝난 작업은 취소할 수 없습니다.", "화면을 새로고침해 주세요.")
        c.execute("UPDATE runs SET status='cancelled', message='사용자가 취소했습니다.', updated_at=? WHERE id=?", (db.now_iso(), run_id))
        c.execute("UPDATE projects SET stage=?, version=version+1, updated_at=? WHERE id=?",
                  (runs.stage_before(run["kind"], True), db.now_iso(), run["project_id"]))
    killed = ENGINE.cancel(run_id)
    return {"runId": run_id, "status": "cancelled", "cleanup": {"processesTerminated": killed}}


@app.get("/api/projects/{pid}/outputs/{name}")
def download(pid: str, name: str):
    if name not in OUTPUTS:
        raise ApiError(404, "not_found", "파일을 찾을 수 없습니다.")
    path = db.DATA_DIR / "projects" / pid / "outputs" / name
    with db.read() as c:
        p = get_project(c, pid)
    if p["stage"] != "done" or not path.is_file():
        raise ApiError(404, "not_found", "아직 만들어진 파일이 없습니다.", "수정과 재검수가 끝난 뒤 내려받을 수 있습니다.")
    if name == "final.md":  # 수정본도 저장된 결과로 다시 만든다(보류 목록이 보류마다 한 줄로 나뉘도록)
        r0 = json.loads(p["result_json"])
        if r0.get("finalParagraphs"):
            path.write_text(doc.build_final_md(p["title"], r0["finalParagraphs"], r0["sources"], doc.gaps_in(r0["finalParagraphs"])), encoding="utf-8")
    if name == "review_report.md":  # 리포트는 저장된 결과로 다시 만들어 준다(옛 프로젝트도 최신 형식으로 받는다)
        rounds = all_rounds(p)
        proj = {"title": p["title"], "conditions": p["conditions"], "finishedAt": rounds[-1]["result"]["finishedAt"]}
        path.write_text(doc.build_report_md(proj, rounds), encoding="utf-8")
    return FileResponse(path, filename=name, media_type="text/markdown; charset=utf-8", headers={"Cache-Control": "no-cache"})


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
