"""테스트용 가짜 브라우저. Edge/Chrome 헤드리스의 `--screenshot=<파일> <SVG 주소>` 호출을 흉내 낸다(SVG를 실제로 그리지는 않는다).
FAKE_BROWSER_MODE: ok(그림이 있는 1080×1350 PNG) | blank(한 가지 색 PNG) | wrongsize(800×600) | crash(파일 없이 종료) | hang(오래 걸림)
호출 기록(인자, SVG 앞부분)은 FAKE_BROWSER_LOG에 한 줄씩 남는다."""
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

from PIL import Image, ImageDraw

args = sys.argv[1:]
shot = next(a.split("=", 1)[1] for a in args if a.startswith("--screenshot="))
uri = args[-1]
svg_path = Path(unquote(urlparse(uri).path.lstrip("/"))) if os.name == "nt" else Path(unquote(urlparse(uri).path))
svg = svg_path.read_text(encoding="utf-8") if svg_path.is_file() else ""
mode = os.environ.get("FAKE_BROWSER_MODE", "ok")
log = os.environ.get("FAKE_BROWSER_LOG")
if log:
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"args": args, "pid": os.getpid(), "mode": mode, "svg": svg}, ensure_ascii=False) + "\n")

if mode == "crash":
    sys.exit(1)
if mode == "hang":
    time.sleep(60)
size = (800, 600) if mode == "wrongsize" else (1080, 1350)
img = Image.new("RGB", size, (247, 249, 252))
if mode != "blank":
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([size[0] * 0.2, size[1] * 0.5, size[0] * 0.5, size[1] * 0.75], radius=30, fill=(20, 33, 61))
    d.ellipse([size[0] * 0.55, size[1] * 0.55, size[0] * 0.8, size[1] * 0.8], fill=(244, 162, 89))
img.save(shot)
