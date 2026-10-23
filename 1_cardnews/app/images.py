"""카드 이미지 제작: agy(Antigravity CLI)의 내장 이미지 도구를 자연어로 요청하고, 못 만들면 Claude CLI 도안(+Pillow)으로 대체한다.

- agy JSON의 status가 SUCCESS여도 이미지가 만들어졌다는 뜻이 아니다(실제로 이미지 도구가 500 오류를 내도 SUCCESS가 나온다).
  그래서 agy 대화 기록에서 실제 이미지 파일을 찾아 열어 보고, 못 찾으면 대화 기록의 오류를 원인으로 보여 준다.
- 어떤 방식으로 만들었든 저장 파일은 PNG로 열어 보고 크기를 1080×1350으로 맞춘 뒤, 파일·형식·크기·위쪽 여백을 검사해 기록한다.
- 이미지 안에는 글자를 넣지 않는다(프롬프트로 금지하고, 도안 방식은 도형만 그린다). 글자는 다음 단계에서 별도 렌더러로 올린다."""
import hashlib
import json
import os
import re
import shutil
import statistics
import subprocess
import threading
from datetime import datetime
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from . import db, runs, scene_renderer, svg_render
from .runs import Stop

TARGET = (scene_renderer.WIDTH, scene_renderer.HEIGHT)
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
STATE = {"agy": "unknown"}  # unknown | ok | image_failed | missing | auth_required (agy를 켠 경우에만 의미가 있다)
AGY_OFF_REASON = ("agy 이미지 생성이 꺼져 있어 시도하지 않았습니다(--no-agy 또는 CARDNEWS_USE_AGY=0으로 끈 설정). "
                  "다시 켜려면 run.py --agy(또는 그 설정을 빼고) 서버를 켜세요")
AGY_OFF_VALUES = ("0", "false", "off", "no")


def agy_enabled() -> bool:
    """agy 이미지 생성은 기본으로 켜져 있다. 실행 옵션 --no-agy 또는 환경 변수 CARDNEWS_USE_AGY=0(false/off/no)으로 끈다.
    (agy는 사용 한도가 작아서 한동안 기본 꺼짐이었다가 사용자의 결정으로 다시 기본 켜짐이 됐다.)
    호출할 때마다 읽으므로 서버를 켠 뒤에는 바뀌지 않는 값으로 쓰되, 테스트에서는 환경 변수로 바꿀 수 있다."""
    return os.environ.get("CARDNEWS_USE_AGY", "").strip().lower() not in AGY_OFF_VALUES


def image_providers(engine) -> list:
    """이미지를 만드는 수단의 순서: agy(기본 켜짐, 끈 경우에는 건너뜀) → Claude SVG + 브라우저 캡처 → Claude 도형 도안 + Pillow.
    agy를 꺼 두었으면 시도하지 않고 기록만 남긴다. SVG를 쓸 수 없거나(브라우저 없음·검증 실패 등) 실패하면 도형 도안으로 대체한다."""
    return [AgyProvider() if agy_enabled() else AgyOff(), SvgProvider(engine), SceneProvider(engine)]
_procs: dict[str, subprocess.Popen] = {}
_lock = threading.Lock()


class ImageError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def agy_home() -> Path:
    return Path(os.environ.get("CARDNEWS_AGY_HOME") or (Path.home() / ".gemini" / "antigravity-cli"))


def agy_prefix() -> list[str]:
    override = os.environ.get("CARDNEWS_AGY_CMD")
    if override:
        return json.loads(override)
    found = find_agy()
    if not found:
        STATE["agy"] = "missing"
        raise ImageError("agy_unavailable", "agy(Antigravity CLI)를 이 PC에서 찾지 못했습니다(PATH에도, 기본 설치 위치 "
                         f"{default_agy_path()}에도 없습니다). 환경 변수 CARDNEWS_AGY에 agy 실행 파일 경로를 지정할 수 있습니다.")
    return [found]


def default_agy_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "agy" / "bin" / ("agy.exe" if os.name == "nt" else "agy")


def find_agy() -> str | None:
    """agy 실행 파일: 환경 변수 CARDNEWS_AGY(경로) → PATH → 기본 설치 위치. 서버를 켠 터미널의 PATH에 agy가 없어도 기본 위치에 있으면 쓴다.
    CARDNEWS_AGY가 있는데 파일이 없으면 다른 것을 몰래 쓰지 않고 없는 것으로 본다."""
    explicit = os.environ.get("CARDNEWS_AGY")
    if explicit:
        return explicit if Path(explicit).is_file() else None
    found = shutil.which("agy")
    if found:
        return found
    default = default_agy_path()
    return str(default) if default.is_file() else None


def agy_health() -> str:
    if not agy_enabled():
        return "disabled"
    try:
        agy_prefix()
    except ImageError:
        return "missing"
    return STATE["agy"]


def cancel_run(run_id: str) -> int:
    """이 작업이 띄운 agy·브라우저 프로세스(와 자식)를 종료한다. 종료한 수를 돌려준다."""
    from .claude_engine import kill_tree
    with _lock:
        proc = _procs.get(run_id)
    return (1 if proc is not None and kill_tree(proc) else 0) + svg_render.cancel_run(run_id)


# --------------------------------------------------------------------------- 프롬프트
def build_image_prompt(card: dict, style: dict) -> str:
    palette = ", ".join(style["palette"])
    return (
        "내장 이미지 생성 도구로 카드뉴스에 들어갈 설명용 일러스트 한 장을 만들어 줘. 이미지 하나만 만들고 다른 작업은 하지 마.\n"
        f"- 이미지의 역할: {card['imageRole']}\n"
        f"- 그릴 것(구도와 피사체): {card['imagePlan']}\n"
        f"- 그림체(모든 카드 공통): {style['artStyle']}\n"
        f"- 분위기: {style['mood']}\n"
        f"- 색: 다음 팔레트의 색만 쓴다 — {palette}\n"
        "- 구도: 세로 4:5에 가장 가까운 세로 비율. 위쪽 30%는 제목, 아래쪽 약 28%는 본문 글자를 올릴 자리라서 배경색(단색 또는 부드러운 그라데이션)만 두고 "
        "아무것도 그리지 않는다. 그림의 중심 피사체는 가로 가운데, 세로 30~70% 사이에 모아서 둔다.\n"
        "- 금지: 이미지 안의 모든 글자·숫자·기호·로고·워터마크·말풍선·UI 문구. 실제 사람의 얼굴, 실제 제품 화면이나 실제 사건 사진처럼 보이는 표현. "
        "누가 봐도 그림(일러스트)으로 보여야 한다.\n"
        "- 이 환경에서는 셸 명령 실행과 파일 복사가 허용되지 않는다. 명령을 실행하거나 폴더를 살펴보려 하지 말고, 이미지 생성 도구만 사용해 이미지를 만든다.\n"
        "만든 이미지 파일의 전체 경로를 알려 줘."
    )


# --------------------------------------------------------------------------- agy
def _sub_conversations(home: Path, conv_id: str) -> list[str]:
    ids = [conv_id]
    sub_dir = home / "brain" / conv_id / ".system_generated" / "subagents"
    if sub_dir.is_dir():
        for f in sub_dir.glob("*.json"):
            try:
                cid = json.loads(f.read_text(encoding="utf-8")).get("conversationId")
            except (OSError, ValueError):
                continue
            if cid and cid not in ids:
                ids.append(cid)
    return ids


def _looks_like_image(path: Path) -> bool:
    try:
        with Image.open(path) as im:
            im.verify()
        return True
    except (OSError, UnidentifiedImageError, ValueError):
        return False


def find_agy_image(work_dir: Path, home: Path, conv_ids: list[str], started: float, response: str) -> Path | None:
    """agy가 만든 이미지 파일을 찾는다: 작업 폴더, 대화 폴더(하위 에이전트 포함), 응답에 적힌 경로 순서."""
    roots = [work_dir] + [home / "brain" / c for c in conv_ids]
    # agy는 "SUCCESS"여도 그림을 작업 폴더가 아니라 brain 아래 가장 최근 대화 폴더에 JPG로 저장하기도 한다.
    # 대화 번호를 모르거나 다른 폴더에 저장돼도 이번 실행 뒤에 새로 바뀐 brain 하위 폴더를 최신순으로 함께 본다.
    brain = home / "brain"
    if brain.is_dir():
        recent = [d for d in brain.iterdir() if d.is_dir() and d not in roots and d.stat().st_mtime >= started - 2]
        roots += sorted(recent, key=lambda d: d.stat().st_mtime, reverse=True)[:5]
    found: list[Path] = []
    for root in roots:
        if root.is_dir():
            for p in root.rglob("*"):
                if p.is_file() and p.suffix.lower() in IMAGE_EXT and p.stat().st_size > 0 and p.stat().st_mtime >= started - 2:
                    found.append(p)
    for m in re.finditer(r"[A-Za-z]:[\\/][^\s\"'`<>|*?]+\.(?:png|jpe?g|webp)", response or "", re.I):
        p = Path(m.group(0))
        if p.is_file() and p.stat().st_size > 0:
            found.append(p)
    for p in sorted(set(found), key=lambda x: x.stat().st_mtime, reverse=True):
        if _looks_like_image(p):
            return p
    return None


def agy_failure_reason(home: Path, conv_ids: list[str]) -> str:
    """agy 대화 기록에서 이미지 도구 오류를 모아 원인 한 줄로 만든다."""
    errors = []
    for cid in conv_ids:
        t = home / "brain" / cid / ".system_generated" / "logs" / "transcript.jsonl"
        if not t.is_file():
            continue
        for line in t.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                o = json.loads(line)
            except ValueError:
                continue
            msg = str(o.get("error") or "")
            if o.get("status") == "ERROR" and "image" in msg.lower():
                errors.append(re.sub(r"\s+", " ", msg))
    if not errors:
        return ""
    code = re.search(r"(\d{3} [A-Za-z ]+?),", errors[-1])
    detail = code.group(1) if code else errors[-1][:140]
    return f"agy 이미지 도구가 오류를 돌려줬습니다: {detail} ({len(errors)}번 시도 모두 실패)"


def run_agy(run_id: str, prompt: str, work_dir: Path) -> tuple[Path, dict]:
    """agy를 한 번 실행해 이미지 파일 경로와 실행 기록을 돌려준다. 못 만들면 ImageError."""
    from .claude_engine import kill_tree
    cmd = agy_prefix() + ["-p", prompt, "--output-format", "json"]
    work_dir.mkdir(parents=True, exist_ok=True)
    timeout = float(os.environ.get("CARDNEWS_AGY_TIMEOUT", "420"))
    kwargs = dict(stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                  errors="replace", cwd=work_dir)
    if os.name == "nt":
        kwargs["creationflags"] = 0x08000000 | 0x00000200
    else:
        kwargs["start_new_session"] = True
    started = datetime.now().timestamp()
    try:
        proc = subprocess.Popen(cmd, **kwargs)
    except OSError as e:
        raise ImageError("agy_unavailable", f"agy를 실행하지 못했습니다: {e.strerror or type(e).__name__}")
    with _lock:
        _procs[run_id] = proc
    flags = {"timeout": False}

    def on_timeout() -> None:
        flags["timeout"] = True
        kill_tree(proc)

    timer = threading.Timer(timeout, on_timeout)
    timer.start()
    try:
        out, err = proc.communicate()
    finally:
        timer.cancel()
        kill_tree(proc)
        with _lock:
            _procs.pop(run_id, None)
    runs.ensure_active(run_id)  # 취소·중단된 작업이면 여기서 멈춘다
    if flags["timeout"]:
        raise ImageError("image_failed", f"agy가 {int(timeout)}초 안에 끝나지 않아 중단했습니다.")
    try:
        info = json.loads(out)
    except ValueError:
        info = {}
    conv = info.get("conversation_id")
    record = {"status": info.get("status"), "conversationId": conv, "exitCode": proc.returncode,
              "durationSeconds": info.get("duration_seconds"), "deniedActions": info.get("denied_actions") or []}
    if proc.returncode != 0 and not info:
        low = (err or "").lower()
        if any(h in low for h in ("login", "sign in", "authenticate", "unauthorized")):
            STATE["agy"] = "auth_required"
            raise ImageError("auth_required", "agy 로그인이 필요합니다. 터미널에서 agy를 실행해 로그인한 뒤 다시 시도해 주세요.")
        STATE["agy"] = "image_failed"
        raise ImageError("image_failed", f"agy가 결과 없이 종료됐습니다(종료 코드 {proc.returncode}).")
    home = agy_home()
    ids = _sub_conversations(home, conv) if conv else []
    path = find_agy_image(work_dir, home, ids, started, str(info.get("response") or ""))
    if path is None:
        STATE["agy"] = "image_failed"
        reason = agy_failure_reason(home, ids)
        if not reason:  # 이미지 도구 오류 기록이 없으면 agy가 실제로 한 일을 보여 준다
            denied = ", ".join(sorted({d.get("display_name", "?") for d in record["deniedActions"]}))
            said = re.sub(r"\s+", " ", str(info.get("response") or "")).strip()[:140]
            reason = ("agy가 이미지 도구를 쓰지 못했습니다" + (f" (거부된 요청: {denied})" if denied else "")
                      + (f" — agy 응답: {said}" if said else ""))
        raise ImageError("image_failed", f"{reason} (agy 응답 상태는 {info.get('status')}였지만 이미지는 없었습니다.)")
    STATE["agy"] = "ok"
    return path, record


# --------------------------------------------------------------------------- 저장·검사
def finalize_image(src: Image.Image, dest: Path) -> dict:
    """이미지를 1080×1350 PNG로 저장하고, 저장된 파일을 다시 열어 검사한 결과를 돌려준다."""
    orig = src.size
    img = src.convert("RGB")
    note = "원본 크기 그대로"
    if img.size != TARGET:
        tw, th = TARGET
        scale = max(tw / img.width, th / img.height)  # 비율을 유지한 채 덮도록 확대·축소
        img = img.resize((max(tw, round(img.width * scale)), max(th, round(img.height * scale))), Image.LANCZOS)
        left = (img.width - tw) // 2
        top = img.height - th  # 위쪽(제목 여백)을 줄이고 아래쪽 그림은 지킨다
        img = img.crop((left, top, left + tw, top + th))
        note = f"원본 {orig[0]}×{orig[1]}을 4:5로 맞추려 {tw}×{th}로 확대·잘라냄(위쪽 여백에서 잘라냄)"
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, "PNG", optimize=True)
    return {"originalSize": list(orig), "normalization": note}


def inspect_png(path: Path) -> dict:
    """저장된 파일을 다시 읽어 검사한다: 파일 존재·PNG 서명·열림·크기·위쪽 여백."""
    checks: dict = {"textInImage": "unchecked"}
    notes = []
    exists = path.is_file() and path.stat().st_size > 0
    checks["file"] = "pass" if exists else "fail"
    sig = path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" if exists else False
    checks["format"] = "pass" if sig else "fail"
    try:
        with Image.open(path) as im:
            im.load()
            size = im.size
            gray = im.convert("L")
    except (OSError, UnidentifiedImageError):
        checks.update({"size": "fail", "margin": "fail"})
        return {"checks": checks, "notes": ["PNG로 열 수 없습니다."], "width": 0, "height": 0, "bytes": 0, "sha256": ""}
    checks["size"] = "pass" if size == TARGET else "fail"
    if size != TARGET:
        notes.append(f"크기가 {size[0]}×{size[1]}입니다(기대 {TARGET[0]}×{TARGET[1]}).")
    top_h = int(size[1] * 0.28)
    spread = max(statistics.pstdev([gray.getpixel((x, y)) for x in range(0, size[0], 12)]) for y in (int(top_h * f) for f in (0.15, 0.5, 0.9)))
    checks["margin"] = "pass" if spread <= 12 else "warn"
    if spread > 12:
        notes.append(f"위쪽 28%가 완전히 비어 있지 않습니다(가로 밝기 편차 {spread:.0f}). 제목을 올리면 가려질 수 있습니다.")
    notes.append("이미지 안에 글자가 없는지는 자동으로 확인하지 못했습니다(프롬프트로 금지만 했습니다).")
    data = path.read_bytes()
    return {"checks": checks, "notes": notes, "width": size[0], "height": size[1], "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}


# --------------------------------------------------------------------------- 작업 루프
def card_dir(project_id: str, card_id: str) -> Path:
    return db.DATA_DIR / "projects" / project_id / "cards" / card_id


def _set_card(c, pid: str, card_id: str, status: str, error: tuple | None = None) -> None:
    code, msg, nxt = error or (None, None, None)
    c.execute("UPDATE cards SET status=?, error_code=?, error_message=?, error_next=?, updated_at=? WHERE project_id=? AND card_id=?",
              (status, code, msg, nxt, db.now_iso(), pid, card_id))


class SvgProvider:
    """Claude CLI가 SVG 그림(요소 조각)을 쓰고, 검증한 뒤 Edge/Chrome 헤드리스로 PNG로 바꾼다. 도형 도안보다 표현이 풍부하다."""
    name = "claude-svg"

    def __init__(self, engine) -> None:
        self.engine = engine

    def generate(self, run_id: str, card: dict, style: dict, out_dir: Path) -> tuple[Image.Image, dict]:
        from .claude_engine import EngineError
        from .claude_parse import FormatError
        browser = svg_render.browser_name()
        if not browser:  # 브라우저가 없으면 Claude를 부르기 전에 포기한다(호출 낭비 방지)
            raise ImageError("browser_unavailable", "SVG를 이미지로 바꿀 Edge나 Chrome을 찾지 못했습니다(CARDNEWS_BROWSER로 경로를 지정할 수 있습니다).")
        try:
            fragment, prompt = self.engine.make_svg(run_id, card, style)
        except EngineError as e:
            raise ImageError(e.code, f"Claude SVG 만들기 실패: {e.message}")
        except FormatError as e:
            raise ImageError("svg_invalid", f"SVG 검증을 통과하지 못했습니다: {e}")
        doc = svg_render.svg_document(fragment, style["palette"])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "art.svg").write_text(doc, encoding="utf-8")
        try:
            img = svg_render.render_png(doc, out_dir / "browser_work", run_id)
        except svg_render.BrowserError as e:
            raise ImageError(e.code, e.message)
        return img, {"tool": "claude-cli SVG + 브라우저", "prompt": prompt, "svgFile": "art.svg", "browser": browser}


class SceneProvider:
    """Claude CLI가 도형 도안을 만들고 Pillow가 그린다(SVG를 쓸 수 없을 때의 대체 수단)."""
    name = "claude-scene"

    def __init__(self, engine) -> None:
        self.engine = engine

    def generate(self, run_id: str, card: dict, style: dict, out_dir: Path) -> tuple[Image.Image, dict]:
        from .claude_engine import EngineError
        from .claude_parse import FormatError
        try:
            scene, prompt = self.engine.make_scene(run_id, card, style)
        except EngineError as e:
            raise ImageError(e.code, f"Claude 도안 만들기 실패: {e.message}")
        except FormatError as e:
            raise ImageError("engine_failed", f"Claude 도안 형식 오류: {e}")
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "scene.json").write_text(json.dumps(scene, ensure_ascii=False, indent=1), encoding="utf-8")
        return scene_renderer.render(scene), {"tool": "claude-cli 도안 + Pillow", "prompt": prompt, "sceneFile": "scene.json"}


class MockProvider:
    name = "mock"

    def generate(self, run_id: str, card: dict, style: dict, out_dir: Path) -> tuple[Image.Image, dict]:
        from . import claude_parse as cp
        scene = cp.validate_scene(scene_renderer.mock_scene(style["palette"], card["order"]), style["palette"])
        return scene_renderer.render(scene), {"tool": "mock", "prompt": "(모의 이미지: 고정 도형 도안)"}


class AgyOff:
    """agy가 꺼져 있을 때 자리만 차지하는 수단. 실행하지 않고 '쓰지 않았다'는 기록만 남긴다."""
    name = "agy"
    off = True
    reason = AGY_OFF_REASON


class AgyProvider:
    name = "agy"

    def generate(self, run_id: str, card: dict, style: dict, out_dir: Path) -> tuple[Image.Image, dict]:
        prompt = build_image_prompt(card, style)
        path, record = run_agy(run_id, prompt, out_dir / "agy_work")
        with Image.open(path) as im:
            im.load()
            img = im.copy()
        return img, {"tool": "agy", "prompt": prompt, "agy": record, "agySourceFile": path.name}


def run_cards(run_id: str, providers: list) -> None:
    """승인된 스토리보드의 카드 중 pending 카드의 이미지를 만든다. 카드 하나가 실패해도 나머지는 계속한다."""
    from . import cardfiles, typeset  # 늦게 가져온다(cardfiles가 이 모듈을 쓴다)
    ctx = runs.load_context(run_id)
    pid = ctx["project"]["id"]
    with db.read() as c:
        sb = c.execute("SELECT * FROM storyboards WHERE project_id=? AND version=?", (pid, ctx["project"]["storyboard_version"])).fetchone()
        todo = [dict(r) for r in c.execute("SELECT * FROM cards WHERE project_id=? AND status='pending' ORDER BY ord", (pid,))]
    only = ctx["params"].get("cardIds")
    if only is not None:  # 카드를 골라 다시 만들 때는 고른 카드만 만든다(앞선 취소로 대기 중인 다른 카드까지 몰래 만들지 않는다)
        todo = [r for r in todo if r["card_id"] in only]
    style = json.loads(sb["style_json"])
    cards = {x["id"]: x for x in json.loads(sb["cards_json"])}
    disabled: dict[str, str] = {}
    summary = {"ok": 0, "failed": 0}
    try:
        for n, row in enumerate(todo, start=1):
            card = cardfiles.effective_card(pid, cards[row["card_id"]])  # 사용자가 고친 그림 계획이 있으면 그것으로 만든다
            if not ctx["params"].get("recompose"):
                card.pop("edit", None)  # 기존 SVG를 고치는 방식은 수정 요청 작업에서만 쓴다(그냥 다시 만들기는 처음부터 그린다)
            runs.progress(run_id, "images", f"카드 {n}/{len(todo)} 「{card['title']}」 이미지 만드는 중")
            with db.tx() as c:
                runs.require_running(c, run_id)
                _set_card(c, pid, card["id"], "generating")
            attempts, done = [], False
            with db.read() as c:  # 이전 그림으로 되돌린 뒤에도 기존 버전 번호와 겹치지 않게 가장 큰 번호 다음을 쓴다
                version = (c.execute("SELECT MAX(version) FROM card_versions WHERE project_id=? AND card_id=?", (pid, row["card_id"])).fetchone()[0] or 0) + 1
            out_dir = card_dir(pid, card["id"]) / f"v{version}"
            for prov in providers:
                if getattr(prov, "off", False):  # 꺼진 수단: agy를 호출하지 않는다
                    attempts.append({"provider": prov.name, "outcome": "disabled", "reason": prov.reason})
                    continue
                if prov.name == "agy" and card.get("edit"):  # 수정 요청은 기존 SVG를 고치는 일이라 agy를 쓰지 않는다(한도도 아낀다)
                    attempts.append({"provider": prov.name, "outcome": "skipped", "reason": "수정 요청은 기존 그림(SVG)을 고치는 방식이라 agy를 쓰지 않았습니다."})
                    continue
                if prov.name in disabled:
                    attempts.append({"provider": prov.name, "outcome": "skipped", "reason": disabled[prov.name]})
                    continue
                runs.progress(run_id, "images", f"카드 {n}/{len(todo)} 「{card['title']}」 {_label(prov)}로 만드는 중")
                try:
                    img, meta = prov.generate(run_id, card, style, out_dir)
                    extra = finalize_image(img, out_dir / "image.png")
                    insp = inspect_png(out_dir / "image.png")
                    if insp["checks"]["file"] != "pass" or insp["checks"]["format"] != "pass" or insp["checks"]["size"] != "pass":
                        raise ImageError("image_failed", "저장한 파일 검사에 실패했습니다: " + " ".join(insp["notes"]))
                except ImageError as e:
                    attempts.append({"provider": prov.name, "outcome": "failed", "code": e.code, "reason": e.message})
                    # 같은 실행의 나머지 카드에서 agy를 또 기다리지 않고, 브라우저를 쓸 수 없으면 SVG를 또 시도하지 않는다
                    if prov.name == "agy" or e.code in ("browser_unavailable", "browser_failed"):
                        disabled[prov.name] = e.message
                    continue
                attempts.append({"provider": prov.name, "outcome": "ok"})
                prov_json = {**meta, **extra, "attempts": attempts, "createdAt": db.now_iso(),
                             "disclosure": "생성 이미지입니다. 실제 사건이나 제품 화면이 아닙니다.",
                             "license": "AI가 만든 그림입니다. 저작권이 없다고 단정하지 않습니다."}
                (out_dir / "provenance.json").write_text(json.dumps(prov_json, ensure_ascii=False, indent=1), encoding="utf-8")
                rel = (out_dir / "image.png").relative_to(db.DATA_DIR / "projects" / pid).as_posix()
                with db.tx() as c:
                    runs.require_running(c, run_id)
                    c.execute("INSERT INTO card_versions(project_id, card_id, version, tool, prompt, file_rel, width, height, bytes, sha256, "
                              "provenance_json, checks_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                              (pid, card["id"], version, meta["tool"], meta["prompt"], rel, insp["width"], insp["height"], insp["bytes"],
                               insp["sha256"], json.dumps(prov_json, ensure_ascii=False),
                               json.dumps({"checks": insp["checks"], "notes": insp["notes"]}, ensure_ascii=False), db.now_iso()))
                    c.execute("UPDATE cards SET current_version=? WHERE project_id=? AND card_id=?", (version, pid, card["id"]))
                    _set_card(c, pid, card["id"], "ready")
                cardfiles.clear_edit(pid, card["id"])
                if ctx["params"].get("recompose"):  # 수정 요청으로 그림을 다시 만들었으면 저장해 둔 글자 배치를 새 그림 위에 다시 올린다
                    try:
                        cardfiles.recompose_saved(pid, card["id"], out_dir / "image.png", version)
                    except (typeset.TypesetError, OSError, ValueError):
                        pass  # 합성본이 없으면 카드에 '글자 아직 안 올림'으로 보이고 최종 승인이 막힌다
                summary["ok"] += 1
                done = True
                break
            if not done:
                reasons = "; ".join(f"{a['provider']}: {a.get('reason', '')}" for a in attempts if a["outcome"] not in ("ok", "disabled"))
                with db.tx() as c:
                    runs.require_running(c, run_id)
                    _set_card(c, pid, card["id"], "failed", ("image_failed", reasons[:500], "다시 시도해 주세요. agy 로그인과 Claude CLI 상태를 확인하세요."))
                summary["failed"] += 1
    finally:
        # 취소·중단·예외로 멈췄을 때 '만드는 중'으로 남은 카드를 다시 만들 수 있게 되돌린다
        with db.tx() as c:
            c.execute("UPDATE cards SET status='pending' WHERE project_id=? AND status='generating'", (pid,))
    runs.finish_images(run_id, f"이미지 {summary['ok']}장 완료" + (f", {summary['failed']}장 실패" if summary["failed"] else ""))


def _label(prov) -> str:
    return {"agy": "agy", "claude-svg": "Claude SVG", "claude-scene": "Claude 도안", "mock": "모의 이미지"}.get(prov.name, prov.name)
