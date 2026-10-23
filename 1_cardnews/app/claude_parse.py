"""Claude CLI가 돌려준 내용을 앱의 규약(progress / need_input / result / error)으로 검증·정규화한다.
모델이 쓴 내용을 그대로 믿지 않는다: 형식이 틀리거나 근거 없는 내용은 걸러내거나 실패로 처리한다."""
import json
import re
from datetime import date
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

CATEGORIES = {"model", "product", "research", "devtool", "other"}
FACT_STATUS = {"confirmed", "company_claim", "announced", "unverified"}
URL_RE = re.compile(r"https?://[^\s\"'<>\\)\]]+")


class FormatError(Exception):
    """모델 답변이 약속한 형식이 아님. 메시지는 모델에게 되돌려 줄 수 있는 설명이다."""


def extract_json(text: str) -> dict | None:
    """답변 안의 JSON 객체 하나를 꺼낸다. 코드펜스가 있어도 되고 없어도 된다."""
    if not text:
        return None
    candidates = [m.group(1) for m in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)]
    first, last = text.find("{"), text.rfind("}")
    if first != -1 and last > first:
        candidates.append(text[first:last + 1])
    for cand in candidates:
        try:
            obj = json.loads(cand)
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def normalize_url(url: str) -> str | None:
    try:
        u = urlsplit(url.strip())
    except ValueError:
        return None
    if u.scheme not in ("http", "https") or not u.hostname:
        return None
    host = u.hostname.lower().removeprefix("www.")
    query = urlencode(sorted((k, v) for k, v in parse_qsl(u.query) if not k.lower().startswith("utm_")))
    return urlunsplit(("https", host, u.path.rstrip("/"), query, ""))


def find_urls(text: str) -> set[str]:
    out = set()
    for raw in URL_RE.findall(text or ""):
        n = normalize_url(raw.rstrip(".,;"))
        if n:
            out.add(n)
    return out


def _s(value, limit: int) -> str:
    text = re.sub(r"[\x00-\x08\x0b-\x1f]", "", str(value or "")).strip()
    return text[:limit]


def _date_or_none(value, latest: date) -> str | None:
    try:
        d = date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
    return d.isoformat() if d <= latest else None


def validate_candidates(raw: dict, window_from: date, window_to: date, seen_urls: set[str]):
    """-> (candidates, dropped: list[str]). id는 앱이 붙인다."""
    items = raw.get("candidates")
    if not isinstance(items, list):
        raise FormatError('"candidates" 배열이 없습니다.')
    out, dropped, seen_titles = [], [], set()
    for item in items:
        if not isinstance(item, dict):
            continue
        title = _s(item.get("title"), 200)
        if not title:
            continue
        sources = []
        for s in item.get("sources") or []:
            if not isinstance(s, dict):
                continue
            n = normalize_url(str(s.get("url", "")))
            if n and n in seen_urls:  # 이번 조사에서 실제로 검색·열람한 주소만 인정
                sources.append({"title": _s(s.get("title"), 150) or title[:60], "url": str(s["url"]).strip(),
                                "publisher": _s(s.get("publisher"), 80)})
        if not sources:
            dropped.append(f"{title[:40]}: 검색·열람 기록에 없는 출처")
            continue
        published = _date_or_none(item.get("publishedAt"), window_to)
        if published and not (window_from <= date.fromisoformat(published) <= window_to):
            dropped.append(f"{title[:40]}: 조사 기간 밖({published})")
            continue
        key = title.lower()
        if key in seen_titles:
            continue
        seen_titles.add(key)
        category = item.get("category") if item.get("category") in CATEGORIES else "other"
        try:
            grouped = max(1, min(20, int(item.get("groupedCount") or 1)))
        except (TypeError, ValueError):
            grouped = 1
        out.append({
            "title": title,
            "summary": _s(item.get("summary"), 500),
            "publishedAt": published,
            "eventDate": _date_or_none(item.get("eventDate"), window_to),
            "dateStatus": "confirmed" if published else "unconfirmed",
            "category": category,
            "sources": sources[:3],
            "whySelect": _s(item.get("whySelect"), 300),
            "uncertainty": _s(item.get("uncertainty"), 300) or "특별한 불확실성을 적지 않았습니다.",
            "groupedCount": grouped,
        })
        if len(out) == 12:
            break
    for n, cand in enumerate(out, start=1):
        cand["id"] = f"cand_{n}"
    return out, dropped


def validate_question(raw: dict) -> dict:
    question = _s(raw.get("question"), 200)
    options_raw = raw.get("options")
    if not question or not isinstance(options_raw, list) or not 2 <= len(options_raw) <= 5:
        raise FormatError("need_input에는 question과 2~5개의 options가 필요합니다.")
    options, used = [], set()
    for i, o in enumerate(options_raw, start=1):
        if isinstance(o, str):
            o = {"label": o}
        if not isinstance(o, dict) or not _s(o.get("label"), 60):
            raise FormatError("각 option에는 label이 필요합니다.")
        oid = re.sub(r"[^A-Za-z0-9_-]", "", _s(o.get("id"), 30)) or f"opt{i}"
        if oid in used:
            oid = f"opt{i}"
        used.add(oid)
        options.append({"id": oid, "label": _s(o.get("label"), 60), "description": _s(o.get("description"), 150)})
    rec = raw.get("recommendation")
    recommendation = None
    if isinstance(rec, dict):
        rid = _s(rec.get("option_id") or rec.get("optionId"), 30)
        if rid in used and _s(rec.get("reason"), 300):
            recommendation = {"optionId": rid, "reason": _s(rec.get("reason"), 300)}
    return {"question": question, "options": options,
            "multiSelect": bool(raw.get("multi_select") or raw.get("multiSelect")),
            "allowFreeText": bool(raw.get("allow_free_text", raw.get("allowFreeText", True))),
            "recommendation": recommendation}


def validate_notes(raw, allowed_urls: set[str], fetched_ok: bool) -> dict:
    if not isinstance(raw, dict):
        raise FormatError('"notes" 객체가 없습니다.')
    facts = []
    for f in raw.get("facts") or []:
        if isinstance(f, str):
            f = {"text": f}
        if not isinstance(f, dict) or not _s(f.get("text"), 400):
            continue
        url = _s(f.get("sourceUrl"), 500)
        n = normalize_url(url) if url else None
        status = f.get("status") if f.get("status") in FACT_STATUS else "unverified"
        if not n or n not in allowed_urls:  # 근거 주소를 확인할 수 없으면 확정 사실로 쓰지 않는다
            url, status = "", "unverified"
        elif status == "confirmed" and not fetched_ok:
            status = "unverified"  # 원문을 한 번도 열어 보지 못했다면 제목만으로 확정하지 않는다
        facts.append({"text": _s(f.get("text"), 400), "sourceUrl": url, "status": status})
    if not facts:
        raise FormatError("notes.facts가 비어 있습니다.")
    sources = []
    for s in raw.get("sources") or []:
        if isinstance(s, dict):
            n = normalize_url(str(s.get("url", "")))
            if n and n in allowed_urls:
                sources.append({"title": _s(s.get("title"), 150) or _s(s.get("url"), 60), "url": str(s["url"]).strip(),
                                "publisher": _s(s.get("publisher"), 80)})
    limits = [_s(x, 300) for x in (raw.get("limits") or []) if _s(x, 300)][:10]
    if not fetched_ok:
        limits.insert(0, "원문 페이지를 열어 보지 못해 제목·검색 요약만으로 정리했습니다. 사실로 확정하지 않았습니다.")
    conflicts = [_s(x, 300) for x in (raw.get("conflicts") or []) if _s(x, 300)][:10]
    return {"facts": facts[:15], "sources": sources[:15], "limits": limits, "conflicts": conflicts}


def validate_proposal(raw, freshness: dict) -> dict:
    if not isinstance(raw, dict):
        raise FormatError('"proposal" 객체가 없습니다.')
    audience, why = _s(raw.get("audience"), 100), _s(raw.get("whyUseful"), 300)
    fmt = raw.get("format")
    if not audience or not why or fmt not in ("list", "story"):
        raise FormatError("proposal에는 audience, whyUseful, format(list|story)이 필요합니다.")
    comparison = []
    for f in raw.get("formatComparison") or []:
        if isinstance(f, dict) and f.get("format") in ("list", "story"):
            try:
                cards = max(3, min(12, int(f.get("cards"))))
            except (TypeError, ValueError):
                cards = 6
            comparison.append({"format": f["format"], "fit": _s(f.get("fit"), 200), "cards": cards,
                               "evidenceDifficulty": _s(f.get("evidenceDifficulty"), 200)})
    if {c["format"] for c in comparison} != {"list", "story"}:
        raise FormatError("formatComparison에는 list와 story 두 형식이 모두 있어야 합니다.")
    rec = raw.get("formatRecommendation")
    if not isinstance(rec, dict) or rec.get("format") not in ("list", "story") or not _s(rec.get("reason"), 300):
        raise FormatError("formatRecommendation에는 format과 reason이 필요합니다.")
    hooks = [_s(h, 120) for h in (raw.get("hooks") or []) if _s(h, 120)]
    if not freshness["titleMayUseLatest"]:  # D15: 기준일·게시일로 뒷받침되지 않으면 '최신'을 쓰지 않는다
        hooks = [h for h in hooks if "최신" not in h]
    if not 1 <= len(hooks) <= 3:
        raise FormatError("hooks는 1~3개여야 합니다. ('최신'은 게시일 확인된 경우에만 쓸 수 있습니다.)")
    return {"audience": audience, "whyUseful": why, "format": fmt, "formatComparison": comparison,
            "formatRecommendation": {"format": rec["format"], "reason": _s(rec.get("reason"), 300)},
            "freshnessBasis": freshness, "hooks": hooks}


def freshness_basis(selected: list[dict], basis_date: date, window_days: int) -> dict:
    start = date.fromordinal(basis_date.toordinal() - window_days)
    ok = bool(selected) and all(
        c["dateStatus"] == "confirmed" and start <= date.fromisoformat(c["publishedAt"]) <= basis_date for c in selected)
    return {"researchBasis": basis_date.isoformat(), "publishedAt": [c["publishedAt"] for c in selected],
            "titleMayUseLatest": ok}


# --------------------------------------------------------------------------- 스토리보드·이미지 도안
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
SENTENCE_END = re.compile(r"[.!?。？！](?:\s|$)")
NUMBER_RE = re.compile(r"\d[\d,.]*\d|\d")
SHAPE_TYPES = {"rect", "rounded_rect", "ellipse", "polygon", "line"}


def sentence_count(text: str) -> int:
    t = text.strip()
    return len(SENTENCE_END.findall(t)) + (0 if SENTENCE_END.search(t[-2:] if t else "") or not t else 1)


def _style(raw) -> dict:
    if not isinstance(raw, dict):
        raise FormatError('"style" 객체가 없습니다.')
    palette = [str(c).strip().upper() for c in (raw.get("palette") or [])]
    if not 4 <= len(palette) <= 6 or not all(HEX_RE.match(c) for c in palette) or len(set(palette)) != len(palette):
        raise FormatError("style.palette는 서로 다른 #RRGGBB 색 4~6개여야 합니다.")
    art, mood = _s(raw.get("artStyle"), 200), _s(raw.get("mood"), 100)
    if not art or not mood:
        raise FormatError("style.artStyle(그림체)과 style.mood(분위기)가 필요합니다.")
    return {"palette": palette, "artStyle": art, "mood": mood}


def validate_storyboard(raw: dict, allowed_urls: set[str], fallback_sources: list[dict], known_text: str, fmt: str) -> dict:
    """모델이 만든 스토리보드를 검증·정규화한다. 카드 5~8장, 카드당 메시지 하나, 출처는 확인된 URL만."""
    cards_raw = raw.get("cards")
    if not isinstance(cards_raw, list) or not 5 <= len(cards_raw) <= 8:
        raise FormatError(f"cards는 5~8장이어야 합니다(지금 {len(cards_raw) if isinstance(cards_raw, list) else 0}장).")
    style = _style(raw.get("style"))
    cards = []
    for i, c in enumerate(cards_raw, start=1):
        if not isinstance(c, dict):
            raise FormatError(f"{i}번째 카드가 객체가 아닙니다.")
        title, keyline, body = _s(c.get("title"), 40), _s(c.get("keyline"), 90), _s(c.get("body"), 220)
        role_img, plan = _s(c.get("imageRole"), 160), _s(c.get("imagePlan"), 400)
        if not (title and keyline and body and role_img and plan):
            raise FormatError(f"{i}번째 카드에 title, keyline, body, imageRole, imagePlan이 모두 필요합니다.")
        if len(title) > 30 or len(keyline) > 70:
            raise FormatError(f"{i}번째 카드의 제목은 30자, 핵심 문장은 70자 이내여야 합니다.")
        if sentence_count(keyline) != 1:
            raise FormatError(f"{i}번째 카드의 핵심 문장은 한 문장이어야 합니다(카드 하나에 메시지 하나).")
        if len(body) > 160 or sentence_count(body) > 2:
            raise FormatError(f"{i}번째 카드의 본문은 160자·두 문장 이내이며 핵심 문장을 풀어 쓰는 내용이어야 합니다.")
        evidence = []
        for e in c.get("evidence") or []:
            if isinstance(e, dict):
                n = normalize_url(str(e.get("url", "")))
                if n and n in allowed_urls and n not in {normalize_url(x["url"]) for x in evidence}:
                    evidence.append({"url": str(e["url"]).strip(), "note": _s(e.get("note"), 160)})
        if not evidence:
            if i in (1, len(cards_raw)):  # 표지·마지막 카드는 선택한 소식의 출처를 그대로 쓴다
                evidence = [dict(s) for s in fallback_sources[:3]]
            else:
                raise FormatError(f"{i}번째 카드에 검색·열람으로 확인된 출처(evidence.url)가 없습니다.")
        known = {x.replace(",", "") for x in NUMBER_RE.findall(known_text)}
        unsupported = sorted({x for x in NUMBER_RE.findall(title + " " + keyline + " " + body)
                              if x.replace(",", "") not in known and len(x.replace(",", "").replace(".", "")) >= 2})
        cards.append({"id": f"card_{i}", "order": i,
                      "role": "cover" if i == 1 else "summary" if i == len(cards_raw) else "body",
                      "title": title, "keyline": keyline, "body": body, "imageRole": role_img, "imagePlan": plan,
                      "evidence": evidence[:3], "unsupportedNumbers": unsupported})
    return {"format": fmt, "style": style, "cards": cards}


def _nearest(color: str, palette: list[str]) -> str:
    def rgb(h):
        return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))
    r = rgb(color)
    return min(palette, key=lambda p: sum((a - b) ** 2 for a, b in zip(r, rgb(p))))


def _num(v, lo=0.0, hi=1.0) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise FormatError("도형 좌표는 숫자여야 합니다.")
    if f != f:
        raise FormatError("도형 좌표에 NaN이 있습니다.")
    return max(lo, min(hi, f))


def validate_scene(raw: dict, palette: list[str]) -> dict:
    """Claude가 만든 도형 도안을 검증한다. 색은 스토리보드 팔레트로 맞추고(그림체 통일), 좌표는 0~1로 제한한다."""
    bg = raw.get("background")
    if not isinstance(bg, dict):
        raise FormatError('"background" 객체가 없습니다.')
    for key in ("from", "to"):
        if not HEX_RE.match(str(bg.get(key, ""))):
            raise FormatError(f"background.{key}는 #RRGGBB 색이어야 합니다.")
    shapes_raw = raw.get("shapes")
    if not isinstance(shapes_raw, list) or not 3 <= len(shapes_raw) <= 40:
        raise FormatError("shapes는 3~40개여야 합니다.")
    shapes = []
    for s in shapes_raw:
        if not isinstance(s, dict) or s.get("type") not in SHAPE_TYPES or not HEX_RE.match(str(s.get("color", ""))):
            raise FormatError("각 도형에는 type(rect|rounded_rect|ellipse|polygon|line)과 #RRGGBB color가 필요합니다.")
        item = {"type": s["type"], "color": _nearest(s["color"], palette), "opacity": _num(s.get("opacity", 1.0), 0.05, 1.0)}
        if s["type"] in ("rect", "rounded_rect", "ellipse"):
            item.update({k: _num(s.get(k)) for k in ("x", "y", "w", "h")})
            if item["w"] <= 0 or item["h"] <= 0:
                raise FormatError("도형의 w, h는 0보다 커야 합니다.")
            if s["type"] == "rounded_rect":
                item["radius"] = _num(s.get("radius", 0.02), 0.0, 0.5)
        else:
            pts = s.get("points")
            if not isinstance(pts, list) or not (2 if s["type"] == "line" else 3) <= len(pts) <= 16:
                raise FormatError("polygon은 점 3~16개, line은 점 2~16개가 필요합니다.")
            item["points"] = [[_num(p[0]), _num(p[1])] for p in pts if isinstance(p, (list, tuple)) and len(p) == 2]
            if len(item["points"]) != len(pts):
                raise FormatError("points는 [x, y] 쌍이어야 합니다.")
            if s["type"] == "line":
                item["width"] = _num(s.get("width", 0.01), 0.002, 0.05)
        shapes.append(item)
    return {"background": {"from": _nearest(bg["from"], palette), "to": _nearest(bg["to"], palette)}, "shapes": shapes}
