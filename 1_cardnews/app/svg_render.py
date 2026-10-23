"""Claude가 쓴 SVG 조각을 검증하고, Edge/Chrome(헤드리스)으로 1080×1350 PNG로 바꾼다.

SVG는 스크립트·글자·외부 링크를 넣을 수 있는 형식이라 모델이 쓴 조각을 그대로 브라우저에 넣지 않는다.
- XML로 파싱해서 허용한 요소·속성만 남기고(나머지는 거절), 색은 스토리보드 팔레트로 맞춘다.
- 배경과 위쪽 30% 여백은 우리가 그린다: 모델의 그림은 가운데 영역(위 30%와 아래 29%를 뺀 y 405~960)으로 잘려서 들어간다. 아래쪽은 본문 글자 자리다.
- 브라우저 프로필은 임시 폴더로 따로 쓴다(사용자가 쓰는 Edge 창과 섞이지 않게)."""
import json
import os
import re
import shutil
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image, ImageStat, UnidentifiedImageError

from . import claude_parse as cp

NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", NS)  # 직렬화할 때 ns0: 같은 접두사가 붙지 않게 한다
W, H = 1080, 1350
MARGIN_Y = round(H * 0.30)  # 위쪽 30%: 제목 자리
BOTTOM_Y = round(H * 0.71)  # 이보다 아래(약 29%)는 본문 글자 자리
ALLOWED_TAGS = {"g", "path", "circle", "ellipse", "rect", "polygon", "polyline", "line", "defs",
                "linearGradient", "radialGradient", "stop"}
DRAW_TAGS = {"path", "circle", "ellipse", "rect", "polygon", "polyline", "line"}
COLOR_ATTRS = {"fill", "stroke", "stop-color"}
NUM_ATTRS = {"x", "y", "cx", "cy", "r", "rx", "ry", "width", "height", "x1", "y1", "x2", "y2", "fx", "fy",
             "stroke-width", "opacity", "fill-opacity", "stroke-opacity", "stop-opacity", "offset", "stroke-miterlimit"}
OTHER_ATTRS = {"id", "d", "points", "transform", "gradientTransform", "gradientUnits", "fill-rule", "clip-rule",
               "stroke-linecap", "stroke-linejoin", "stroke-dasharray"}
ALLOWED_ATTRS = COLOR_ATTRS | NUM_ATTRS | OTHER_ATTRS
NUM = r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"
NUM_RE = re.compile(rf"^{NUM}%?$")
PATH_RE = re.compile(r"^[MmLlHhVvCcSsQqTtAaZz0-9eE.,\-+\s]+$")
POINTS_RE = re.compile(r"^[0-9eE.,\-+\s]+$")
TRANSFORM_RE = re.compile(rf"^(?:\s*(?:translate|scale|rotate|skewX|skewY|matrix)\(\s*{NUM}(?:[\s,]+{NUM})*\s*\)\s*)+$")
ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,30}$")
URL_RE = re.compile(r"^url\(#([A-Za-z][A-Za-z0-9_-]{0,30})\)$")
KEYWORDS = {"fill-rule": {"nonzero", "evenodd"}, "clip-rule": {"nonzero", "evenodd"}, "stroke-linecap": {"butt", "round", "square"},
            "stroke-linejoin": {"miter", "round", "bevel"}, "gradientUnits": {"userSpaceOnUse", "objectBoundingBox"}}
MAX_CHARS, MAX_ELEMENTS, MAX_ABS = 60_000, 200, 10_000

_procs: dict[str, subprocess.Popen] = {}
_lock = threading.Lock()


class SvgError(Exception):
    """SVG가 검증을 통과하지 못했다. 메시지는 모델에게 되돌려 고쳐 쓰게 할 수 있는 설명이다."""


class BrowserError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _check_numbers(text: str, where: str) -> None:
    for m in re.finditer(NUM, text):
        if abs(float(m.group(0))) > MAX_ABS:
            raise SvgError(f"SVG 검증 실패: {where}의 숫자가 너무 큽니다({m.group(0)}). 좌표는 0~1350 안에서 쓰세요.")


def sanitize_fragment(fragment: str, palette: list[str]) -> str:
    """모델이 쓴 SVG 요소 조각을 검증하고 정리한 문자열(<g> 안에 넣을 내용)을 돌려준다. 문제가 있으면 SvgError."""
    if not isinstance(fragment, str) or not fragment.strip():
        raise SvgError("SVG 검증 실패: svg가 비어 있습니다.")
    if len(fragment) > MAX_CHARS:
        raise SvgError(f"SVG 검증 실패: 너무 깁니다({len(fragment)}자, 최대 {MAX_CHARS}자).")
    if "<!" in fragment or "<?" in fragment:
        raise SvgError("SVG 검증 실패: 주석·DOCTYPE·CDATA·처리 명령은 쓸 수 없습니다.")
    try:
        root = ET.fromstring(f'<svg xmlns="{NS}">{fragment}</svg>')
    except ET.ParseError as e:
        raise SvgError(f"SVG 검증 실패: XML 형식이 올바르지 않습니다({e}). 태그를 모두 닫고 속성 값을 따옴표로 감싸세요.")
    elements = list(root.iter())[1:]
    if len(elements) > MAX_ELEMENTS:
        raise SvgError(f"SVG 검증 실패: 요소가 너무 많습니다({len(elements)}개, 최대 {MAX_ELEMENTS}개).")
    draws = 0
    ids: dict[str, str] = {}
    for el in elements:
        if el.get("id"):
            if not ID_RE.match(el.get("id")) or el.get("id") in ids or el.get("id") in ("bg__", "below__"):
                raise SvgError(f"SVG 검증 실패: id가 올바르지 않거나 겹칩니다({el.get('id')}).")
            ids[el.get("id")] = _local(el.tag)
    for el in elements:
        tag = _local(el.tag)
        if not el.tag.startswith("{" + NS + "}") or tag not in ALLOWED_TAGS:
            raise SvgError(f"SVG 검증 실패: 허용되지 않는 요소 <{tag}>입니다. 허용: {', '.join(sorted(ALLOWED_TAGS))}.")
        if (el.text or "").strip() or (el.tail or "").strip():
            raise SvgError("SVG 검증 실패: 요소 안에 글자를 넣을 수 없습니다(글자 금지).")
        if tag in DRAW_TAGS:
            draws += 1
        if tag == "stop" and el.get("offset") is None:
            raise SvgError("SVG 검증 실패: <stop>에는 offset이 필요합니다.")
        for name, value in list(el.attrib.items()):
            if name.startswith("{") or name not in ALLOWED_ATTRS:
                raise SvgError(f"SVG 검증 실패: 허용되지 않는 속성 {name}입니다(<{tag}>).")
            v = value.strip()
            if name in COLOR_ATTRS:
                m = URL_RE.match(v)
                if v == "none":
                    pass
                elif re.fullmatch(r"#[0-9a-fA-F]{6}", v):
                    el.set(name, cp._nearest(v.upper(), palette))  # 그림체 통일: 팔레트 색으로 맞춘다
                elif m and ids.get(m.group(1)) in ("linearGradient", "radialGradient"):
                    pass
                else:
                    raise SvgError(f"SVG 검증 실패: {name}={value!r}은 쓸 수 없습니다. #RRGGBB, none, 이 SVG 안의 그라데이션 url(#id)만 가능합니다.")
            elif name in NUM_ATTRS:
                if not NUM_RE.match(v):
                    raise SvgError(f"SVG 검증 실패: {name}={value!r}은 숫자여야 합니다(단위 없이).")
                _check_numbers(v, name)
            elif name == "d":
                if not PATH_RE.match(v) or len(v) > 4000:
                    raise SvgError("SVG 검증 실패: path의 d 속성에 허용되지 않는 문자가 있거나 너무 깁니다.")
                _check_numbers(v, "d")
            elif name == "points":
                if not POINTS_RE.match(v):
                    raise SvgError("SVG 검증 실패: points에는 숫자만 쓸 수 있습니다.")
                _check_numbers(v, "points")
            elif name in ("transform", "gradientTransform"):
                if not TRANSFORM_RE.match(v):
                    raise SvgError(f"SVG 검증 실패: {name}은 translate/scale/rotate/skewX/skewY/matrix 숫자만 가능합니다.")
                _check_numbers(v, name)
            elif name in KEYWORDS:
                if v not in KEYWORDS[name]:
                    raise SvgError(f"SVG 검증 실패: {name}={value!r}은 쓸 수 없습니다.")
            elif name == "stroke-dasharray":
                if not POINTS_RE.match(v):
                    raise SvgError("SVG 검증 실패: stroke-dasharray에는 숫자만 쓸 수 있습니다.")
    if draws < 5:
        raise SvgError(f"SVG 검증 실패: 그리는 요소가 너무 적습니다({draws}개). 15개 이상으로 그려 주세요.")
    return "".join(ET.tostring(child, encoding="unicode") for child in root).replace(f' xmlns="{NS}"', "")


def fragment_of(doc: str) -> str | None:
    """우리가 만든 SVG 문서(svg_document)에서 모델이 쓴 조각만 꺼낸다. 형식이 다르면 None."""
    start = doc.find('<g clip-path="url(#below__)">')
    if start < 0 or not doc.rstrip().endswith("</g></svg>"):
        return None
    return doc[start + len('<g clip-path="url(#below__)">'):doc.rstrip().rfind("</g></svg>")]


def svg_document(clean_fragment: str, palette: list[str]) -> str:
    """검증한 조각을 우리가 그린 배경과 위쪽 여백 클립 안에 넣어 완전한 SVG 문서로 만든다."""
    p = palette
    return (f'<svg xmlns="{NS}" width="{W}" height="{H}" viewBox="0 0 {W} {H}">'
            f'<defs><linearGradient id="bg__" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{p[3]}"/>'
            f'<stop offset="1" stop-color="{p[2]}"/></linearGradient>'
            f'<clipPath id="below__"><rect x="0" y="{MARGIN_Y}" width="{W}" height="{BOTTOM_Y - MARGIN_Y}"/></clipPath></defs>'
            f'<rect width="{W}" height="{H}" fill="url(#bg__)"/>'
            f'<g clip-path="url(#below__)">{clean_fragment}</g></svg>')


# --------------------------------------------------------------------------- 브라우저
def find_browser() -> list[str] | None:
    """헤드리스로 쓸 브라우저 실행 명령(앞부분). 환경 변수 CARDNEWS_BROWSER_CMD(JSON 배열, 시험용) → CARDNEWS_BROWSER(경로) → Edge → Chrome 순서."""
    override = os.environ.get("CARDNEWS_BROWSER_CMD")
    if override:
        cmd = json.loads(override)
        return cmd if cmd and (Path(cmd[0]).exists() or shutil.which(cmd[0])) else None
    explicit = os.environ.get("CARDNEWS_BROWSER")
    if explicit:
        return [explicit] if Path(explicit).is_file() else None
    candidates = []
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        if base:
            candidates += [Path(base) / "Microsoft/Edge/Application/msedge.exe", Path(base) / "Google/Chrome/Application/chrome.exe"]
    candidates += [Path(x) for x in (shutil.which("msedge"), shutil.which("chrome"), shutil.which("google-chrome"), shutil.which("chromium")) if x]
    for c in candidates:
        if c.is_file():
            return [str(c)]
    return None


def browser_name() -> str | None:
    cmd = find_browser()
    if not cmd:
        return None
    return Path(cmd[-1] if len(cmd) > 1 and cmd[-1].endswith(".py") else cmd[0]).stem


def cancel_run(run_id: str) -> int:
    from .claude_engine import kill_tree
    with _lock:
        proc = _procs.get(run_id)
    return 1 if proc is not None and kill_tree(proc) else 0


def _remove_profile(profile: Path) -> None:
    """임시 브라우저 프로필을 지운다. Windows에서는 브라우저의 보조 프로세스가 막 끝난 직후에 파일이 잠겨 있을 수 있어
    잠시 기다리며 몇 번 다시 지운다. 끝내 못 지워도 작업은 실패시키지 않는다(임시 폴더라 나중에 지워도 된다)."""
    for _ in range(20):
        shutil.rmtree(profile, ignore_errors=True)
        if not profile.exists():
            return
        time.sleep(0.25)


def render_png(svg_text: str, work_dir: Path, run_id: str) -> Image.Image:
    """SVG 문서를 헤드리스 브라우저로 캡처해 PIL 이미지로 돌려준다. 못 하면 BrowserError."""
    from . import runs
    from .claude_engine import kill_tree
    browser = find_browser()
    if not browser:
        raise BrowserError("browser_unavailable", "SVG를 이미지로 바꿀 Edge나 Chrome을 찾지 못했습니다.")
    work_dir.mkdir(parents=True, exist_ok=True)
    svg_path, png_path, profile = work_dir / "art.svg", work_dir / "shot.png", work_dir / "profile"
    svg_path.write_text(svg_text, encoding="utf-8")
    png_path.unlink(missing_ok=True)
    cmd = browser + ["--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run", "--disable-extensions",
                     "--disable-background-networking", "--force-device-scale-factor=1", f"--user-data-dir={profile}",
                     f"--window-size={W},{H}", f"--screenshot={png_path}", svg_path.resolve().as_uri()]
    timeout = float(os.environ.get("CARDNEWS_BROWSER_TIMEOUT", "90"))
    kwargs = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if os.name == "nt":
        kwargs["creationflags"] = 0x08000000 | 0x00000200
    else:
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(cmd, **kwargs)
    except OSError as e:
        raise BrowserError("browser_unavailable", f"브라우저를 실행하지 못했습니다: {e.strerror or type(e).__name__}")
    with _lock:
        _procs[run_id] = proc
    flags = {"timeout": False}

    def on_timeout() -> None:
        flags["timeout"] = True
        kill_tree(proc)

    timer = threading.Timer(timeout, on_timeout)
    timer.start()
    try:
        proc.wait()
    finally:
        timer.cancel()
        kill_tree(proc)
        with _lock:
            _procs.pop(run_id, None)
        _remove_profile(profile)
    runs.ensure_active(run_id)  # 취소·중단된 작업이면 여기서 멈춘다
    if flags["timeout"]:
        raise BrowserError("browser_failed", f"브라우저가 {int(timeout)}초 안에 끝나지 않아 중단했습니다.")
    if not png_path.is_file() or png_path.stat().st_size == 0:
        raise BrowserError("browser_failed", f"브라우저가 캡처 파일을 만들지 못했습니다(종료 코드 {proc.returncode}).")
    try:
        with Image.open(png_path) as im:
            im.load()
            img = im.convert("RGB")
    except (OSError, UnidentifiedImageError):
        raise BrowserError("browser_failed", "브라우저가 만든 캡처 파일을 PNG로 열 수 없습니다.")
    if max(ImageStat.Stat(img).stddev) < 2:  # 한 가지 색뿐인 이미지는 그림이 그려지지 않은 것
        raise BrowserError("browser_failed", "브라우저 캡처가 빈 이미지입니다(그림이 그려지지 않았습니다).")
    return img
