"""최종 승인된 카드의 내보내기(카드별 PNG, 전체 ZIP, 출처·이미지 사용 기록)와, 내보낸 파일 기준 확인.

확인은 내보낸 폴더 안의 manifest·파일만 읽어서 한다(누락 / 글자 잘림 / 출처 / 날짜).
앱이 직접 확인할 수 없는 것은 '통과'로 두지 않고 unchecked로 남기고, 사용자가 확인할 일을 함께 적는다."""
import hashlib
import json
import re
import shutil
import tempfile
import zipfile
from datetime import date, datetime
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from . import cardfiles, db, typeset
from .claude_parse import normalize_url

DOWNLOADABLE = {"cardnews.zip", "manifest.json", "sources.json", "sources.md", "image_usage.json", "image_usage.md", "verification.json"}
TARGET = (1080, 1350)
FRESH_HOURS = 24  # 조사 기준 시각에서 이 시간 안에 내보내면 "조사 직후"로 본다
DATE_RE = re.compile(r"\d{4}[-./년]\s?\d{1,2}[-./월]\s?\d{1,2}일?|\d{1,2}월\s?\d{1,2}일")


class ExportError(Exception):
    def __init__(self, code: str, message: str, next_action: str = ""):
        super().__init__(message)
        self.code, self.message, self.next_action = code, message, next_action


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def exports_root(pid: str) -> Path:
    return db.DATA_DIR / "projects" / pid / "exports"


# --------------------------------------------------------------------------- 만들기
def build_export(pid: str) -> dict:
    """승인된 상태의 카드를 내보내기 폴더에 쓴다. 승인 여부는 호출하는 쪽이 확인한다."""
    with db.read() as c:
        p = c.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
        sb = c.execute("SELECT * FROM storyboards WHERE project_id=? AND version=?", (pid, p["storyboard_version"])).fetchone()
        rows = c.execute("SELECT * FROM cards WHERE project_id=? ORDER BY ord", (pid,)).fetchall()
        vers = {(v["card_id"], v["version"]): v for v in c.execute("SELECT * FROM card_versions WHERE project_id=?", (pid,))}
        ids = json.loads(p["selection_json"])
        selected = [d for d in (json.loads(r["data_json"]) for r in c.execute("SELECT data_json FROM candidates WHERE project_id=? ORDER BY ord", (pid,)))
                    if d["id"] in ids]
    notes = json.loads(p["notes_json"]) if p["notes_json"] else {}
    proposal = json.loads(p["proposal_json"]) if p["proposal_json"] else {}
    cards_sb = {x["id"]: x for x in json.loads(sb["cards_json"])}
    style = json.loads(sb["style_json"])
    ex_id = "ex_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    out = exports_root(pid) / ex_id
    (out / "cards").mkdir(parents=True)
    basis = p["window_basis"]
    cards, usage = [], []
    for r in rows:
        cid, ver = r["card_id"], r["current_version"]
        v = vers[(cid, ver)]
        meta = json.loads((cardfiles.card_dir(pid, cid) / "layout.json").read_text(encoding="utf-8"))
        final = cardfiles.card_dir(pid, cid) / f"v{ver}" / "final.png"
        name = f"cards/card_{r['ord']:02d}.png"
        shutil.copyfile(final, out / name)
        card = cards_sb[cid]
        lay = meta["layout"]
        prov = json.loads(v["provenance_json"])
        base_rel = v["file_rel"]
        cards.append({
            "id": cid, "order": r["ord"], "role": card["role"], "file": name, "sha256": _sha(out / name), "bytes": (out / name).stat().st_size,
            "width": TARGET[0], "height": TARGET[1], "title": lay["title"], "body": lay["body"], "keyline": card["keyline"],
            "storyboardTitle": card["title"], "storyboardBody": card["body"],
            "textChanged": (lay["title"], lay["body"]) != (card["title"], card["body"]),
            "evidence": card["evidence"], "unsupportedNumbers": card.get("unsupportedNumbers", []),
            "imageVersion": ver, "imageTool": v["tool"], "baseImage": base_rel, "baseImageSha256": v["sha256"],
            "typeset": {"layout": lay, "ok": meta["ok"], "checks": meta["checks"], "savedAt": meta["savedAt"]},
            "revisions": cardfiles.load_revisions(pid, cid)})
        usage.append({
            "order": r["ord"], "cardId": cid, "file": name, "imageTool": v["tool"], "imageVersion": ver, "imageCreatedAt": v["created_at"],
            "baseImageSha256": v["sha256"], "finalImageSha256": cards[-1]["sha256"], "attempts": prov.get("attempts", []),
            "prompt": v["prompt"], "originalSize": prov.get("originalSize"), "normalization": prov.get("normalization"),
            "disclosure": prov.get("disclosure", "생성 이미지입니다. 실제 사건이나 제품 화면이 아닙니다."),
            "license": prov.get("license", "AI가 만든 그림입니다. 저작권이 없다고 단정하지 않습니다."),
            "textInImage": "unchecked", "textOverlay": "Pillow로 별도 합성(글자는 그림 파일에 들어 있지 않음)"})
    to = date.fromisoformat(basis[:10])
    sources = {
        "topic": p["topic"], "researchBasis": basis, "windowDays": p["window_days"],
        "windowFrom": (to.fromordinal(to.toordinal() - p["window_days"])).isoformat(), "windowTo": to.isoformat(),
        "selectedNews": [{"title": s["title"], "publishedAt": s.get("publishedAt"), "dateStatus": s.get("dateStatus"),
                          "sources": s["sources"], "candidateStageNote": s.get("uncertainty")} for s in selected],
        "readOriginals": sorted({f["sourceUrl"] for f in notes.get("facts", []) if f.get("status") == "confirmed" and f.get("sourceUrl")}),
        "facts": notes.get("facts", []), "limits": notes.get("limits", []), "conflicts": notes.get("conflicts", []),
        "cards": [{"order": c["order"], "cardId": c["id"], "title": c["title"], "evidence": c["evidence"],
                   "unsupportedNumbers": c["unsupportedNumbers"]} for c in cards]}
    (out / "sources.json").write_text(json.dumps(sources, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "sources.md").write_text(_sources_md(sources), encoding="utf-8")
    usage_doc = {"note": "카드 그림은 AI(또는 Claude가 그린 SVG/도안)가 만든 설명용 일러스트이며 실제 사건·제품 화면이 아닙니다. 그림 안에는 글자가 없고 제목·본문은 Pillow로 따로 올렸습니다.",
                 "style": style, "cards": usage}
    (out / "image_usage.json").write_text(json.dumps(usage_doc, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "image_usage.md").write_text(_usage_md(usage_doc), encoding="utf-8")
    files = {n: {"sha256": _sha(out / n), "bytes": (out / n).stat().st_size}
             for n in ["sources.json", "sources.md", "image_usage.json", "image_usage.md"] + [c["file"] for c in cards]}
    manifest = {"exportId": ex_id, "createdAt": db.now_iso(), "projectId": pid, "topic": p["topic"], "engine": p["engine"],
                "storyboardVersion": p["storyboard_version"], "format": sb["format"], "cardCount": len(cards),
                "research": {"basis": basis, "windowDays": p["window_days"], "from": sources["windowFrom"], "to": sources["windowTo"]},
                "freshness": proposal.get("freshnessBasis"), "approvedAt": p["final_approved_at"], "cards": cards, "files": files}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    with zipfile.ZipFile(out / "cardnews.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for n in list(files) + ["manifest.json"]:
            z.write(out / n, n)
    report = verify_export(pid, ex_id)
    return {"exportId": ex_id, "files": list_files(out), "verification": report}


def _sources_md(s: dict) -> str:
    lines = [f"# 출처 — {s['topic']}", "", f"- 조사 기준 시각: {s['researchBasis']}", f"- 조사 기간: {s['windowFrom']} ~ {s['windowTo']} ({s['windowDays']}일)", "",
             "## 선택한 소식", ""]
    for n in s["selectedNews"]:
        lines.append(f"- **{n['title']}** — 발행일 {n.get('publishedAt') or '확인 못함'} ({n.get('dateStatus') or '날짜 상태 없음'})")
        for src in n["sources"]:
            lines.append(f"  - {src.get('title') or ''} {src['url']}".rstrip())
        if n.get("candidateStageNote"):  # 후보 조사 때(원문을 읽기 전)의 메모다. 심층 조사 뒤의 결과와 다를 수 있다
            lines.append(f"  - 후보 조사 단계의 메모(심층 조사 전의 상태, 이후 달라졌을 수 있음): {n['candidateStageNote']}")
    if s.get("readOriginals"):
        lines += ["", "## 심층 조사에서 직접 읽어 확인한 원문", ""] + [f"- {u}" for u in s["readOriginals"]]
    lines += ["", "## 카드별 근거", ""]
    for c in s["cards"]:
        lines.append(f"### {c['order']}. {c['title']}")
        for e in c["evidence"]:
            lines.append(f"- {e['url']} — {e.get('note') or ''}".rstrip())
        if c["unsupportedNumbers"]:
            lines.append(f"- ⚠ 자료에서 찾지 못한 숫자: {', '.join(c['unsupportedNumbers'])}")
        lines.append("")
    if s["limits"]:
        lines += ["## 한계", ""] + [f"- {x}" for x in s["limits"]] + [""]
    return "\n".join(lines) + "\n"


def _usage_md(u: dict) -> str:
    lines = ["# 이미지 사용 기록", "", u["note"], ""]
    for c in u["cards"]:
        lines += [f"## 카드 {c['order']} ({c['file']})", f"- 만든 방식: {c['imageTool']} (그림 버전 v{c['imageVersion']}, {c['imageCreatedAt']})",
                  f"- 그림 파일 sha256: {c['baseImageSha256']}", f"- 최종 PNG sha256: {c['finalImageSha256']}",
                  f"- 표시: {c['disclosure']}", f"- 저작권: {c['license']}", f"- 이미지 속 글자 자동 확인: 하지 못함(프롬프트로 금지만 함)"]
        for a in c["attempts"]:
            lines.append(f"- 시도: {a.get('provider')} → {a.get('outcome')}" + (f" ({a['reason']})" if a.get("reason") else ""))
        lines += ["- 프롬프트:", "", "```", c["prompt"], "```", ""]
    return "\n".join(lines) + "\n"


def list_files(out: Path) -> list[dict]:
    files = []
    for p in sorted(out.rglob("*")):
        if p.is_file():
            rel = p.relative_to(out).as_posix()
            files.append({"name": rel, "bytes": p.stat().st_size, "kind": "card_png" if rel.startswith("cards/") else rel.rsplit(".", 1)[0]})
    return files


def latest_exports(pid: str, limit: int = 5) -> list[dict]:
    root = exports_root(pid)
    if not root.is_dir():
        return []
    out = []
    for d in sorted((x for x in root.iterdir() if x.is_dir()), reverse=True)[:limit]:
        m = _json(d / "manifest.json") or {}
        v = _json(d / "verification.json")
        out.append({"exportId": d.name, "createdAt": m.get("createdAt"), "cardCount": m.get("cardCount"), "files": list_files(d),
                    "verification": v})
    return out


def _json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------- 확인
def _item(category: str, key: str, status: str, message: str, user_action: str = "") -> dict:
    return {"category": category, "key": key, "status": status, "message": message, "userAction": user_action}


def verify_export(pid: str, ex_id: str) -> dict:
    """내보낸 폴더의 파일만 보고 누락·글자 잘림·출처·날짜를 확인해 verification.json에 저장한다."""
    out = exports_root(pid) / ex_id
    items: list[dict] = []
    m = _json(out / "manifest.json")
    if not m:
        items.append(_item("누락", "manifest", "fail", "manifest.json이 없거나 읽을 수 없습니다.", "내보내기를 다시 만드세요."))
        return _save(out, ex_id, items)
    cards = m.get("cards", [])
    items += _check_missing(out, m, cards)
    items += _check_text(out, pid, m, cards)
    items += _check_sources(out, m, cards)
    items += _check_dates(m, cards)
    return _save(out, ex_id, items)


def _save(out: Path, ex_id: str, items: list[dict]) -> dict:
    count = {s: sum(1 for i in items if i["status"] == s) for s in ("pass", "fail", "unchecked")}
    report = {"exportId": ex_id, "verifiedAt": db.now_iso(), "summary": count,
              "allPassed": count["fail"] == 0 and count["unchecked"] == 0,
              "note": "통과는 앱이 파일로 직접 확인한 것만입니다. '직접 확인 필요'는 통과가 아니며, 안내한 항목을 사용자가 확인해야 합니다.",
              "items": items}
    if out.is_dir():
        (out / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


def _check_missing(out: Path, m: dict, cards: list[dict]) -> list[dict]:
    C = "누락"
    items = []
    n = m.get("cardCount")
    if n != len(cards) or not cards:
        items.append(_item(C, "count", "fail", f"manifest의 카드 수({n})와 카드 목록({len(cards)}장)이 맞지 않습니다."))
    orders = [c["order"] for c in cards]
    if orders != list(range(1, len(cards) + 1)):
        items.append(_item(C, "order", "fail", f"카드 번호가 1부터 이어지지 않습니다: {orders}"))
    bad = []
    for c in cards:
        path = out / c["file"]
        if not path.is_file():
            bad.append(f"{c['file']} 없음")
            continue
        try:
            with Image.open(path) as im:
                im.load()
                size = im.size
                fmt = im.format
        except (OSError, UnidentifiedImageError):
            bad.append(f"{c['file']} 열 수 없음")
            continue
        if fmt != "PNG" or size != TARGET:
            bad.append(f"{c['file']} 형식/크기 {fmt} {size[0]}×{size[1]}")
        elif _sha(path) != c["sha256"]:
            bad.append(f"{c['file']} 내용이 manifest 기록과 다름")
    items.append(_item(C, "cardFiles", "fail" if bad else "pass",
                       ("카드 PNG에 문제가 있습니다: " + "; ".join(bad)) if bad else f"카드 PNG {len(cards)}장이 모두 있고 열리며 1080×1350 PNG이고 기록과 같습니다."))
    for name in ("sources.json", "sources.md", "image_usage.json", "image_usage.md"):
        ok = (out / name).is_file() and (out / name).stat().st_size > 0 and _sha(out / name) == m["files"].get(name, {}).get("sha256")
        items.append(_item(C, name, "pass" if ok else "fail", f"{name} " + ("있음, 기록과 같음." if ok else "이 없거나 비어 있거나 기록과 다릅니다.")))
    src = _json(out / "sources.json") or {}
    use = _json(out / "image_usage.json") or {}
    miss_src = [c["order"] for c in cards if c["id"] not in {x["cardId"] for x in src.get("cards", [])}]
    miss_use = [c["order"] for c in cards if c["id"] not in {x["cardId"] for x in use.get("cards", [])}]
    items.append(_item(C, "recordsPerCard", "fail" if miss_src or miss_use else "pass",
                       f"카드별 기록이 빠졌습니다(출처: {miss_src}, 이미지 사용: {miss_use})." if miss_src or miss_use else "모든 카드가 출처 기록과 이미지 사용 기록에 들어 있습니다."))
    z = out / "cardnews.zip"
    try:
        with zipfile.ZipFile(z) as zf:
            names = set(zf.namelist())
            broken = zf.testzip()
            want = set(m["files"]) | {"manifest.json"}
            missing = sorted(want - names)
            mism = [n for n in want & names if hashlib.sha256(zf.read(n)).hexdigest() != (m["files"].get(n) or {}).get("sha256") and n != "manifest.json"]
        ok = not missing and not broken and not mism
        items.append(_item(C, "zip", "pass" if ok else "fail", f"ZIP에 파일 {len(want)}개가 모두 있고 내용이 같습니다." if ok else
                           f"ZIP 문제 — 빠진 파일: {missing}, 손상: {broken}, 내용 다름: {mism}"))
    except (OSError, zipfile.BadZipFile) as e:
        items.append(_item(C, "zip", "fail", f"cardnews.zip을 열 수 없습니다: {e}"))
    items.append(_item(C, "pdf", "unchecked", "PDF는 이번 범위에서 만들지 않습니다(선택 기능, 결정 D12).", "PDF가 필요하면 알려 주세요."))
    return items


def _check_text(out: Path, pid: str, m: dict, cards: list[dict]) -> list[dict]:
    C = "글자 잘림"
    items = []
    problems, unreproduced, ok_n = [], [], 0
    tmp = Path(tempfile.mkdtemp(prefix="cardnews_verify_"))
    try:
        for c in cards:
            fails = [k["message"] for k in c["typeset"]["checks"] if k["status"] == "fail"]
            if fails:
                problems.append(f"카드 {c['order']}: " + " / ".join(fails))
                continue
            base = db.DATA_DIR / "projects" / pid / c["baseImage"]
            try:
                again = tmp / f"{c['id']}.png"
                typeset.compose(base, c["typeset"]["layout"], again)
                same = _sha(again) == c["sha256"]
            except (typeset.TypesetError, OSError, UnidentifiedImageError, ValueError):
                same = None
            if same is True:
                ok_n += 1
            else:
                unreproduced.append(c["order"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    items.append(_item(C, "layoutChecks", "fail" if problems else "pass",
                       "글자 검사에 실패한 카드가 있습니다: " + "; ".join(problems) if problems else
                       "모든 카드의 기록된 글자 검사(잘림·그림과 겹침·대비·휴대폰 크기)에 실패 항목이 없습니다."))
    items.append(_item(C, "reproduce", "unchecked" if unreproduced else "pass",
                       f"카드 {unreproduced}는 저장된 글자 배치로 같은 PNG를 다시 만들지 못해 내보낸 파일이 그 검사 결과와 같은지 확인하지 못했습니다." if unreproduced else
                       f"내보낸 PNG {ok_n}장 모두 저장된 글자 배치로 다시 만든 이미지와 같습니다(검사한 배치가 이 파일의 배치입니다).",
                       "해당 카드의 글자를 다시 저장하고 내보내기를 새로 만드세요." if unreproduced else ""))
    items.append(_item(C, "eyes", "unchecked",
                       "앱은 글자를 글자 인식(OCR)으로 읽어 보지 않았고, 계산한 배치(줄바꿈·영역·대비)로만 확인했습니다. 실제 휴대폰 화면에서의 느낌은 확인하지 못했습니다.",
                       "내려받은 카드 PNG를 휴대폰이나 폭 390px 화면에서 열어 제목·본문이 잘리지 않고 읽히는지 눈으로 확인하세요."))
    return items


def _check_sources(out: Path, m: dict, cards: list[dict]) -> list[dict]:
    C = "출처"
    items = []
    src = _json(out / "sources.json") or {}
    allowed = {normalize_url(s["url"]) for n in src.get("selectedNews", []) for s in n["sources"]}
    allowed |= {normalize_url(f.get("sourceUrl", "")) for f in src.get("facts", [])}
    allowed.discard(None)
    none, outside, nums = [], [], []
    urls: list[str] = []
    for c in cards:
        ev = c["evidence"]
        if not ev:
            none.append(c["order"])
        for e in ev:
            u = normalize_url(e["url"])
            if not u or not e["url"].startswith(("http://", "https://")):
                outside.append(f"카드 {c['order']}: {e['url']}")
            elif u not in allowed:
                outside.append(f"카드 {c['order']}: {e['url']}")
            urls.append(e["url"])
        if c["unsupportedNumbers"]:
            nums.append(f"카드 {c['order']}: {', '.join(c['unsupportedNumbers'])}")
    items.append(_item(C, "hasEvidence", "fail" if none else "pass", f"출처가 없는 카드: {none}" if none else "모든 카드에 출처 주소가 1개 이상 있습니다."))
    items.append(_item(C, "inResearch", "fail" if outside else "pass",
                       "조사 자료에 없는 출처 주소가 있습니다: " + "; ".join(outside) if outside else "카드 출처 주소가 모두 이번 조사에서 연 자료의 주소입니다(주소 형식 확인)."))
    changed = [c["order"] for c in cards if c["textChanged"]]
    if changed:
        items.append(_item(C, "revisedText", "unchecked", f"카드 {changed}의 글을 사용자 요청으로 고쳤습니다. 고친 문장이 출처 내용과 맞는지는 확인하지 못했습니다.",
                           "고친 카드의 제목·본문이 아래 출처가 말하는 범위를 넘지 않는지 읽어 보세요(manifest.json의 revisions에 고치기 전·후가 있습니다)."))
    if nums:
        items.append(_item(C, "numbers", "unchecked", "자료에서 찾지 못한 숫자가 카드에 있습니다: " + "; ".join(nums), "해당 숫자가 출처에 실제로 있는지 확인하고, 없으면 카드에서 빼세요."))
    else:
        items.append(_item(C, "numbers", "pass", "카드 스토리보드 기준으로 자료에서 찾지 못한 숫자가 표시된 카드가 없습니다."))
    items.append(_item(C, "contentMatch", "unchecked",
                       "앱은 출처 페이지를 다시 열어 카드 문장이 정말 거기에 있는지 대조하지 않았습니다. 주소가 조사 자료에서 온 것인지만 확인했습니다.",
                       "카드마다 출처 링크를 열어 그 내용이 카드 문장을 뒷받침하는지 확인하세요: " + ", ".join(dict.fromkeys(urls))[:600]))
    return items


def _check_dates(m: dict, cards: list[dict]) -> list[dict]:
    C = "날짜"
    items = []
    r = m["research"]
    items.append(_item(C, "basis", "pass" if r.get("basis") and r.get("from") and r.get("to") else "fail",
                       f"조사 기준 시각 {r.get('basis')}, 조사 기간 {r.get('from')} ~ {r.get('to')}가 기록돼 있습니다." if r.get("basis") else "조사 기준 시각이 기록돼 있지 않습니다."))
    src_news = []
    try:
        src_news = json.loads((exports_root(m["projectId"]) / m["exportId"] / "sources.json").read_text(encoding="utf-8"))["selectedNews"]
    except (OSError, ValueError, KeyError):
        pass
    lo, hi = (date.fromisoformat(r["from"]), date.fromisoformat(r["to"])) if r.get("from") else (None, None)
    undated, outside = [], []
    for n in src_news:
        d = n.get("publishedAt")
        if not d or n.get("dateStatus") != "confirmed":
            undated.append(n["title"])
        elif lo and not (lo <= date.fromisoformat(d) <= hi):
            outside.append(f"{n['title']}({d})")
    items.append(_item(C, "published", "unchecked" if undated else ("fail" if outside else "pass"),
                       ("발행일이 확인되지 않은 소식: " + "; ".join(undated)) if undated else
                       ("조사 기간 밖의 발행일: " + "; ".join(outside)) if outside else "선택한 소식의 발행일이 모두 기록돼 있고 조사 기간 안입니다.",
                       "발행일이 확인되지 않은 소식은 원문에서 날짜를 직접 확인하세요." if undated else ""))
    uses_latest = [c["order"] for c in cards if "최신" in c["title"] + c["body"]]
    allow = (m.get("freshness") or {}).get("titleMayUseLatest")
    if uses_latest:
        items.append(_item(C, "latestWord", "pass" if allow else "fail",
                           f"카드 {uses_latest}에 '최신'이 있고, 기준일({r['basis'][:10]})과 발행일이 확인돼 허용된 표현입니다." if allow else
                           f"카드 {uses_latest}에 '최신'이 있지만 발행일·기준일이 확인되지 않아 쓰면 안 되는 표현입니다."))
    dated = [f"카드 {c['order']}: {x}" for c in cards for x in DATE_RE.findall(c["title"] + " " + c["body"])]
    if dated:
        items.append(_item(C, "datesInText", "unchecked", "카드 글에 날짜가 있습니다: " + "; ".join(dated), "그 날짜가 출처의 날짜와 같은지 확인하세요."))
    # 달력 날짜가 아니라 실제 지난 시간으로 본다(자정을 넘겨도 몇 시간 안 지났으면 문제없다)
    hours = (datetime.fromisoformat(m["createdAt"]) - datetime.fromisoformat(r["basis"])).total_seconds() / 3600
    fresh = hours <= FRESH_HOURS
    when = f"{hours:.0f}시간" if hours < 48 else f"{hours / 24:.0f}일"
    items.append(_item(C, "age", "pass" if fresh else "unchecked",
                       f"조사한 지 {when} 만에 내보냈습니다(기준 {FRESH_HOURS}시간 이내)." if fresh else
                       f"조사한 지 {when}이 지났습니다. 그 사이 새 발표가 있었는지는 확인하지 못했습니다.",
                       "" if fresh else "발행 전에 새 소식이 있는지 한 번 더 확인하세요."))
    items.append(_item(C, "pageDate", "unchecked", "소식의 발행일은 조사 때 기록한 값이며, 앱이 원문 페이지에서 다시 확인하지는 않았습니다.",
                       "원문 페이지에 표시된 발행일이 sources.md의 발행일과 같은지 확인하세요."))
    return items
