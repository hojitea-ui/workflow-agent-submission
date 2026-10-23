"""도형 도안(scene)을 PNG로 그린다. Claude가 만든 도안 또는 모의 도안을 Pillow로 렌더링한다.
- 글자는 그리지 않는다(도형만). 위쪽 여백(제목 자리)과 아래쪽 본문 자리는 배경 그라데이션만 남도록 도형을 잘라 낸다.
- 2배 크기로 그린 뒤 줄여서 가장자리를 부드럽게 한다."""
from PIL import Image, ImageDraw

WIDTH, HEIGHT = 1080, 1350
MARGIN_TOP = 0.30  # 위쪽 30%는 제목을 올릴 여백
MARGIN_BOTTOM = 0.71  # 이보다 아래(약 29%)는 본문 글자를 올릴 자리라 도형을 잘라 낸다
SS = 2  # 슈퍼샘플링 배율


def _rgb(h: str) -> tuple[int, int, int]:
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def _gradient(size: tuple[int, int], c1: str, c2: str) -> Image.Image:
    w, h = size
    top, bottom = _rgb(c1), _rgb(c2)
    col = Image.new("RGB", (1, h))
    px = col.load()
    for y in range(h):
        t = y / max(1, h - 1)
        px[0, y] = tuple(round(a + (b - a) * t) for a, b in zip(top, bottom))
    return col.resize((w, h))


def render(scene: dict, width: int = WIDTH, height: int = HEIGHT, margin_top: float = MARGIN_TOP, bottom_y: float = MARGIN_BOTTOM) -> Image.Image:
    W, H = width * SS, height * SS
    bg = _gradient((W, H), scene["background"]["from"], scene["background"]["to"]).convert("RGBA")
    canvas = bg.copy()
    for s in scene["shapes"]:
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        fill = _rgb(s["color"]) + (round(255 * s.get("opacity", 1.0)),)
        if s["type"] in ("rect", "rounded_rect", "ellipse"):
            box = [s["x"] * W, s["y"] * H, (s["x"] + s["w"]) * W, (s["y"] + s["h"]) * H]
            if s["type"] == "rect":
                d.rectangle(box, fill=fill)
            elif s["type"] == "ellipse":
                d.ellipse(box, fill=fill)
            else:
                d.rounded_rectangle(box, radius=s.get("radius", 0.02) * min(W, H), fill=fill)
        elif s["type"] == "polygon":
            d.polygon([(x * W, y * H) for x, y in s["points"]], fill=fill)
        else:  # line: 둥근 끝의 굵은 선
            pts = [(x * W, y * H) for x, y in s["points"]]
            line_w = max(2, round(s.get("width", 0.01) * W))
            d.line(pts, fill=fill, width=line_w, joint="curve")
            r = line_w / 2
            for x, y in (pts[0], pts[-1]):
                d.ellipse([x - r, y - r, x + r, y + r], fill=fill)
        canvas = Image.alpha_composite(canvas, layer)
    cut = round(margin_top * H)  # 위쪽 여백은 배경만 남긴다
    canvas.paste(bg.crop((0, 0, W, cut)), (0, 0))
    low = round(bottom_y * H)  # 아래쪽 본문 자리도 배경만 남긴다
    canvas.paste(bg.crop((0, low, W, H)), (0, low))
    return canvas.convert("RGB").resize((width, height), Image.LANCZOS)


def mock_scene(palette: list[str], seed: int = 0) -> dict:
    """모의 엔진용 고정 도안: 팔레트 색으로 만든 단순한 구도(가운데 41%에만 도형)."""
    p = palette
    shift = (seed % 3) * 0.04
    return {
        "background": {"from": p[3], "to": p[2]},
        "shapes": [
            {"type": "ellipse", "color": p[1], "opacity": 0.35, "x": 0.10 + shift, "y": 0.50, "w": 0.80, "h": 0.18},
            {"type": "rounded_rect", "color": p[0], "opacity": 1.0, "x": 0.18 + shift, "y": 0.36, "w": 0.26, "h": 0.20, "radius": 0.03},
            {"type": "rounded_rect", "color": p[1], "opacity": 1.0, "x": 0.56 - shift, "y": 0.40, "w": 0.26, "h": 0.20, "radius": 0.03},
            {"type": "line", "color": p[0], "opacity": 1.0, "points": [[0.44 + shift, 0.46], [0.56 - shift, 0.50]], "width": 0.012},
            {"type": "ellipse", "color": p[2 if len(p) > 4 else 2], "opacity": 0.9, "x": 0.40, "y": 0.62, "w": 0.20, "h": 0.05},
        ],
    }
