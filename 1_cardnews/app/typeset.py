"""생성 이미지 위에 제목·본문을 별도 글자로 올린다(Pillow). 이미지 안에는 글자가 없고, 합성본은 따로 저장한다.

글자 크기는 기본 자동 맞춤이고, 사용자가 정한 값이 영역을 넘으면 자르지 않고 경고로 알려 준다.
검사: 글꼴 / 영역 안에 들어감(잘림) / 제목이 위쪽 빈 영역 안에 있는지(그림과 겹침) / 글자와 배경 대비 / 휴대폰 크기 가독성.
"""
import os
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1350
MARGIN = 72
TITLE_ZONE_BOTTOM = 405          # 위쪽 30%: 이미지가 비워 둔 제목 자리
TITLE_BOX = (MARGIN, 80, W - MARGIN, 385)
BODY_BOX_H = 520                 # 본문이 쓸 수 있는 가장 큰 높이
PHONE_W = 390                    # 휴대폰 미리보기 폭(CSS px). 1080px 카드가 이 폭으로 줄어든다.
TITLE_RANGE = (56, 88)
BODY_RANGE = (36, 46)
PAD = 36                         # 본문 바탕 띠 안쪽 여백
LIGHT, DARK = (255, 255, 255), (22, 26, 32)

FONT_CANDIDATES = [  # (보통, 굵게)
    (r"C:\Windows\Fonts\malgun.ttf", r"C:\Windows\Fonts\malgunbd.ttf"),
    ("/usr/share/fonts/truetype/nanum/NanumGothic.ttf", "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
    ("/System/Library/Fonts/AppleSDGothicNeo.ttc", "/System/Library/Fonts/AppleSDGothicNeo.ttc"),
]


class TypesetError(Exception):
    pass


DEFAULT_LAYOUT = {"title": "", "body": "", "titleSize": None, "bodySize": None, "color": "auto", "scrim": 70, "bodyAnchor": "bottom"}


def find_fonts() -> tuple[str, str]:
    env = os.environ.get("CARDNEWS_FONT")
    if env:
        bold = os.environ.get("CARDNEWS_FONT_BOLD") or env
        if Path(env).is_file() and Path(bold).is_file():
            return env, bold
        raise TypesetError("CARDNEWS_FONT로 지정한 글꼴 파일을 찾을 수 없습니다.")
    for reg, bold in FONT_CANDIDATES:
        if Path(reg).is_file() and Path(bold).is_file():
            return reg, bold
    raise TypesetError("한국어 글꼴을 찾지 못했습니다. 환경 변수 CARDNEWS_FONT에 한글이 들어 있는 글꼴 파일(.ttf/.otf) 경로를 지정해 주세요.")


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


NBSP = "\u00a0"
NUMBER = re.compile(r"^\d[\d.,]*[A-Za-z%]*[가-힣]?[.,)]*$")  # 1억, 35.3, 10B, 5%, 3.12다. 같은 수
LATIN_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9\-]*$")
HANGUL_UNIT =re.compile(r"^[가-힣]{1,2}[가-힣.,)]*$")


def _glue_numbers(words: list[str]) -> list[str]:
    """'1억 쌍'처럼 숫자(또는 숫자+단위)와 바로 뒤의 짧은 한글 단위를 한 덩어리로 묶어 줄 끝에서 갈라지지 않게 한다."""
    out: list[str] = []
    for w in words:
        last = out[-1].rstrip("(").split(NBSP)[-1] if out and out[-1] else ""
        if out and NUMBER.match(last) and HANGUL_UNIT.match(w):
            out[-1] = out[-1] + NBSP + w
        elif out and LATIN_NAME.match(last) and NUMBER.match(w) and NBSP not in out[-1]:
            out[-1] = out[-1] + NBSP + w  # "Python 3.12", "CUDA 13"처럼 이름과 버전 번호
        else:
            out.append(w)
    return out


COLON = re.compile(r"[:—]\s")


def colon_split(text: str, font, max_w: int) -> list[str] | None:
    """제목에 ':'나 '—'가 있고 앞뒤가 각각 한 줄에 들어가면 거기서 끊은 두 줄. 아니면 None."""
    plain = text.replace("\n", " ")
    m = COLON.search(plain)
    if not m:
        return None
    a, b = plain[:m.end()].strip(), plain[m.end():].strip()
    return [a, b] if a and b and font.getlength(a) <= max_w and font.getlength(b) <= max_w else None


def balance_title(text: str, font, max_w: int) -> list[str]:
    """제목 줄바꿈: ':' 또는 '—'가 있으면 거기서 끊고(각 줄이 들어갈 때), 아니면 줄 길이가 고르게 되도록 끊는다."""
    plain = text.replace("\n", " ")
    split = colon_split(text, font, max_w)
    if split:
        return split
    greedy = wrap(text, font, max_w)
    n = len(greedy)
    if n < 2 or n > 3 or "\n" in text:
        return greedy
    words = _glue_numbers(plain.split(" "))
    best, best_cost = greedy, None
    # 단어 경계를 n-1곳에서 끊는 모든 경우 중, 가장 긴 줄이 가장 짧은(고른) 경우를 고른다
    from itertools import combinations
    for cuts in combinations(range(1, len(words)), n - 1):
        parts, prev = [], 0
        for c in (*cuts, len(words)):
            parts.append(" ".join(words[prev:c]))
            prev = c
        widths = [font.getlength(x.replace(NBSP, " ")) for x in parts]
        if max(widths) > max_w:
            continue
        cost = max(widths) * 1000 + (max(widths) - min(widths))
        if best_cost is None or cost < best_cost:
            best, best_cost = parts, cost
    return [x.replace(NBSP, " ") for x in best]


def wrap(text: str, font, max_w: int) -> list[str]:
    """줄바꿈: 단어(공백) 단위로 먼저 나누고, 한 단어가 줄보다 길면 글자 단위로 나눈다. 문단 구분(\\n)은 지킨다."""
    lines: list[str] = []
    for para in text.replace("\r", "").split("\n"):
        cur = ""
        for word in _glue_numbers(para.split(" ")):
            word = word  # NBSP로 묶인 덩어리는 한 단어로 취급한다
            trial = word if not cur else cur + " " + word
            if font.getlength(trial.replace(NBSP, " ")) <= max_w:
                cur = trial
                continue
            if cur:
                lines.append(cur)
            cur = ""
            if font.getlength(word.replace(NBSP, " ")) <= max_w:
                cur = word
                continue
            for ch in word.replace(NBSP, " "):  # 공백 없이 긴 말은 글자 단위로
                if cur and font.getlength(cur + ch) > max_w:
                    lines.append(cur)
                    cur = ch
                else:
                    cur += ch
        lines.append(cur)
    lines = [ln.replace(NBSP, " ") for ln in lines]
    return lines if any(lines) else [""]


def _block(text: str, path: str, size: int, max_w: int, spacing: float, title: bool = False):
    font = _font(path, size)
    lines = balance_title(text, font, max_w) if title else wrap(text, font, max_w)
    step = round(size * spacing)
    return font, lines, step, step * len(lines) - round(size * (spacing - 1.0))


def _fit(text: str, path: str, want: int | None, rng: tuple[int, int], max_w: int, max_h: int, spacing: float, title: bool = False):
    """사용자가 크기를 정했으면 그대로, 아니면 영역에 들어가는 가장 큰 크기(범위 안)."""
    if want:
        return _block(text, path, want, max_w, spacing, title) + (want, False)
    size = rng[1]
    if title and COLON.search(text):  # ':'로 끊을 수 있는 가장 큰 크기를 먼저 찾는다(의미 단위로 끊는 쪽을 조금 작은 글자보다 우선)
        for sz in range(rng[1], max(rng[0], 64) - 1, -2):
            if colon_split(text, _font(path, sz), max_w):
                blk = _block(text, path, sz, max_w, spacing, True)
                if blk[3] <= max_h:
                    return blk + (sz, False)
    while size >= rng[0]:
        blk = _block(text, path, size, max_w, spacing, title)
        if blk[3] <= max_h:
            return blk + (size, False)
        size -= 2
    blk = _block(text, path, rng[0], max_w, spacing, title)
    return blk + (rng[0], True)  # 가장 작게 해도 안 들어가면 표시한다


def _lum(rgb) -> float:
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = rgb[:3]
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def _contrast(a, b) -> float:
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _mean_rgb(img: Image.Image, box) -> tuple[int, int, int]:
    px = img.crop(box).resize((1, 1), Image.BOX).getpixel((0, 0))
    return px[:3]


def _worst_contrast(base_with_scrim: Image.Image, mask: Image.Image, color) -> float:
    """글자가 실제로 칠해지는 자리(마스크)의 배경 가운데 글자색과 가장 대비가 낮은 쪽(하위 5%)의 대비."""
    px, m = base_with_scrim.convert("RGB"), mask.point(lambda v: 255 if v > 127 else 0)
    bbox = m.getbbox()
    if not bbox:
        return 21.0
    px, m = px.crop(bbox), m.crop(bbox)
    vals = sorted(_contrast(p, color) for p, k in zip(px.getdata(), m.getdata()) if k)
    return vals[max(0, int(len(vals) * 0.05))] if vals else 21.0


def _picture_rows(base: Image.Image, top: int, bottom: int) -> int:
    """띠 영역(top~bottom) 안에서 그림(배경 그라데이션이 아닌 것)이 있는 가로줄 수. 배경은 한 줄 안에서 거의 한 색이다."""
    gray = base.convert("L")
    n = 0
    for y in range(max(0, top), min(H, bottom)):
        row = [gray.getpixel((x, y)) for x in range(MARGIN, W - MARGIN, 6)]
        mean = sum(row) / len(row)
        if (sum((v - mean) ** 2 for v in row) / len(row)) ** 0.5 > 9:
            n += 1
    return n


def _picture_bottom(base: Image.Image) -> int:
    """그림(배경 그라데이션이 아닌 것)이 닿는 가장 아래 가로줄. 가운데 영역 아래(제목 자리 아래)부터 본문 자리 끝까지 훑는다."""
    gray = base.convert("L")
    bottom = TITLE_ZONE_BOTTOM
    for y in range(TITLE_ZONE_BOTTOM, H - MARGIN):
        row = [gray.getpixel((x, y)) for x in range(MARGIN, W - MARGIN, 6)]
        mean = sum(row) / len(row)
        if (sum((v - mean) ** 2 for v in row) / len(row)) ** 0.5 > 9:
            bottom = y
    return bottom


def _check(key: str, status: str, msg: str) -> dict:
    return {"key": key, "status": status, "message": msg}


def compose(base_path: Path, layout: dict, out_path: Path) -> dict:
    """이미지 위에 글자를 올려 out_path에 PNG로 저장하고, 검사 결과와 실제로 쓴 값을 돌려준다."""
    reg, bold = find_fonts()
    lay = {**DEFAULT_LAYOUT, **{k: v for k, v in layout.items() if k in DEFAULT_LAYOUT}}
    title, body = lay["title"].strip(), lay["body"].strip()
    with Image.open(base_path) as im:
        base = im.convert("RGB")
    if base.size != (W, H):
        raise TypesetError(f"배경 이미지 크기가 {base.size[0]}×{base.size[1]}입니다. {W}×{H} 이미지만 쓸 수 있습니다.")
    checks: list[dict] = []
    max_w = W - 2 * MARGIN

    # 제목: 위쪽 빈 영역 안
    t_box_h = TITLE_BOX[3] - TITLE_BOX[1]
    tfont, tlines, tstep, th, tsize, tover = _fit(title, bold, lay["titleSize"], TITLE_RANGE, max_w, t_box_h, 1.25, True)
    # 본문
    bfont = None
    shrunk_for_picture = False
    if body and lay["bodySize"] is None and lay["bodyAnchor"] == "bottom" and lay["scrim"] > 0:
        # 글자 크기가 자동이면, 본문 띠가 그림 아래를 덮지 않는 가장 큰 크기를 고른다(휴대폰 최소 크기 안에서)
        pic_bottom = _picture_bottom(base)
        for sz in range(BODY_RANGE[1], BODY_RANGE[0] - 1, -2):
            blk = _block(body, reg, sz, max_w - 2 * PAD, 1.45)
            if blk[3] <= BODY_BOX_H - 2 * PAD and H - MARGIN - (blk[3] + 2 * PAD) >= pic_bottom + 12:
                bfont, blines, bstep, bh = blk
                bsize, bover = sz, False
                shrunk_for_picture = sz < BODY_RANGE[1]
                break
    if bfont is None:
        bfont, blines, bstep, bh, bsize, bover = _fit(body, reg, lay["bodySize"], BODY_RANGE, max_w - 2 * PAD + 0, BODY_BOX_H - 2 * PAD, 1.45)

    # 배치
    t_top = TITLE_BOX[1]
    t_bottom = t_top + th
    band_h = bh + 2 * PAD
    if lay["bodyAnchor"] == "center":
        band_top = (H + TITLE_ZONE_BOTTOM) // 2 - band_h // 2
    else:
        band_top = H - MARGIN - band_h
    band_box = (MARGIN - 0, band_top, W - MARGIN, band_top + band_h)
    body_top = band_top + PAD

    # 글자색: 자동이면 제목 자리 배경의 밝기로 정한다
    if lay["color"] == "light":
        color = LIGHT
    elif lay["color"] == "dark":
        color = DARK
    else:
        color = DARK if _lum(_mean_rgb(base, (0, 0, W, TITLE_ZONE_BOTTOM))) > 0.35 else LIGHT
    scrim_rgb = DARK if color == LIGHT else LIGHT
    # 본문은 바탕 띠를 깔아 그림 위에서도 읽히게 한다. 띠 색은 글자색의 반대.
    work = base.convert("RGBA")
    if body and lay["scrim"] > 0:
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(layer).rounded_rectangle(band_box, radius=28, fill=scrim_rgb + (round(255 * lay["scrim"] / 100),))
        work = Image.alpha_composite(work, layer)
    under = work.copy()  # 글자 바로 아래 배경(대비 측정용)

    def draw_text(lines, font, step, x, y, fill):
        mask = Image.new("L", (W, H), 0)
        d = ImageDraw.Draw(mask)
        for i, line in enumerate(lines):
            d.text((x, y + i * step), line, font=font, fill=255)
        return mask

    tmask = draw_text(tlines, tfont, tstep, MARGIN, t_top, 255) if title else Image.new("L", (W, H), 0)
    bmask = draw_text(blines, bfont, bstep, MARGIN + PAD, body_top, 255) if body else Image.new("L", (W, H), 0)
    solid = Image.new("RGBA", (W, H), color + (255,))
    work = Image.composite(solid, work, tmask)
    work = Image.composite(solid, work, bmask)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    work.convert("RGB").save(out_path, "PNG", optimize=True)

    # --- 검사 -----------------------------------------------------------------------
    checks.append(_check("font", "pass", f"글꼴 {Path(reg).name}(한국어)를 썼습니다."))
    if not title:
        checks.append(_check("titleFit", "fail", "제목이 비어 있습니다."))
    elif tover or th > t_box_h:
        checks.append(_check("titleFit", "fail", f"제목이 영역을 넘어 잘립니다({len(tlines)}줄, {th}px > {t_box_h}px). 글자를 줄이거나 문구를 짧게 해 주세요."))
    else:
        checks.append(_check("titleFit", "pass", f"제목이 {len(tlines)}줄로 영역 안에 들어갑니다."))
    if not body:
        checks.append(_check("bodyFit", "warn", "본문이 비어 있습니다."))
    elif bover or bh > BODY_BOX_H - 2 * PAD or band_top < TITLE_ZONE_BOTTOM:
        checks.append(_check("bodyFit", "fail", f"본문이 영역을 넘어 제목 자리와 겹치거나 잘립니다({len(blines)}줄). 글자를 줄이거나 문구를 짧게 해 주세요."))
    else:
        checks.append(_check("bodyFit", "pass", f"본문이 {len(blines)}줄로 영역 안에 들어갑니다."))
    if title:
        if t_bottom > TITLE_ZONE_BOTTOM:
            checks.append(_check("overlap", "fail", "제목이 위쪽 빈 영역(30%)을 넘어 그림과 겹칩니다."))
        elif lay["bodyAnchor"] == "center" and band_top < t_bottom + 24:
            checks.append(_check("overlap", "fail", "본문 띠가 제목과 너무 가깝거나 겹칩니다."))
        else:
            checks.append(_check("overlap", "pass", "제목이 위쪽 빈 영역 안에 있어 그림과 겹치지 않습니다."))
    if body and shrunk_for_picture:
        checks.append(_check("bodyAutoSize", "pass", f"본문이 그림을 가리지 않도록 글자를 {bsize}px로 자동 조정했습니다(휴대폰에서 약 {bsize * PHONE_W / W:.0f}px)."))
    if body and lay["scrim"] > 0:
        covered = _picture_rows(base, band_box[1], band_box[3])
        checks.append(_check("bandOverlap", "warn" if covered >= 12 else "pass",
                             f"본문 바탕 띠가 그림 아래쪽 약 {covered}px을 가립니다. 본문을 줄이거나 그림을 다시 만들어 피사체를 위로 올리세요." if covered >= 12
                             else "본문 바탕 띠가 그림을 가리지 않습니다."))
    # 대비: 제목은 큰 글자라 3.0, 본문은 4.5 이상
    if title:
        c = _worst_contrast(under, tmask, color)
        checks.append(_check("titleContrast", "pass" if c >= 3.0 else "fail",
                             f"제목과 배경의 대비 {c:.1f}:1 (기준 3.0 이상)." + ("" if c >= 3.0 else " 글자색을 바꿔 보세요.")))
    if body:
        c = _worst_contrast(under, bmask, color)
        checks.append(_check("bodyContrast", "pass" if c >= 4.5 else "fail",
                             f"본문과 배경의 대비 {c:.1f}:1 (기준 4.5 이상)." + ("" if c >= 4.5 else " 바탕 띠 농도를 올리거나 글자색을 바꿔 보세요.")))
    # 휴대폰에서 실제 보이는 크기
    scale = PHONE_W / W
    small = []
    if title and tsize * scale < 20:
        small.append(f"제목 {tsize * scale:.0f}px")
    if body and bsize * scale < 13:
        small.append(f"본문 {bsize * scale:.0f}px")
    checks.append(_check("phone", "warn" if small else "pass",
                         ("휴대폰(폭 390px)에서 글자가 작습니다: " + ", ".join(small) + ".") if small
                         else f"휴대폰(폭 390px)에서 제목 약 {tsize * scale:.0f}px, 본문 약 {bsize * scale:.0f}px로 보입니다."))
    return {"used": {"title": title, "body": body, "titleSize": tsize, "bodySize": bsize, "color": "light" if color == LIGHT else "dark",
                     "scrim": lay["scrim"], "bodyAnchor": lay["bodyAnchor"], "titleLines": len(tlines), "bodyLines": len(blines)},
            "checks": checks, "ok": all(c["status"] != "fail" for c in checks)}
