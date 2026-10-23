"""카드 폴더 안의 파일(글자 배치, 합성본, 그림 계획 수정, 수정 기록)과 최종 승인 서명.

카드 폴더: cards/<카드>/v<버전>/image.png(그림) · final.png(글자를 올린 합성본) · cards/<카드>/layout.json(저장한 글자 배치)
          · overrides.json(사용자가 고친 그림 계획) · revisions.jsonl(수정 요청 기록)
최종 승인은 '지금 카드들의 합성본 서명'을 저장해 두고, 서명이 달라지면(글자·그림을 고치면) 자동으로 승인이 풀린다."""
import hashlib
import json
from pathlib import Path

from . import db, images, typeset


def card_dir(pid: str, cid: str) -> Path:
    return db.DATA_DIR / "projects" / pid / "cards" / cid


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def load_overrides(pid: str, cid: str) -> dict:
    return _read_json(card_dir(pid, cid) / "overrides.json") or {}


def save_overrides(pid: str, cid: str, data: dict) -> None:
    d = card_dir(pid, cid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "overrides.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def clear_edit(pid: str, cid: str) -> None:
    ov = load_overrides(pid, cid)
    if ov.pop("edit", None) is not None:
        save_overrides(pid, cid, ov)


def log_revision(pid: str, cid: str, entry: dict) -> None:
    d = card_dir(pid, cid)
    d.mkdir(parents=True, exist_ok=True)
    with (d / "revisions.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({**entry, "at": db.now_iso()}, ensure_ascii=False) + "\n")


def load_revisions(pid: str, cid: str) -> list[dict]:
    p = card_dir(pid, cid) / "revisions.jsonl"
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def saved_layout(pid: str, cid: str) -> dict | None:
    """저장한 글자 배치(그림 버전과 상관없이). 그림을 새로 만들어도 글자 배치는 이어 쓴다."""
    meta = _read_json(card_dir(pid, cid) / "layout.json")
    return (meta or {}).get("layout")


def default_layout(card: dict) -> dict:
    return {"title": card.get("title", ""), "body": card.get("body", ""), "titleSize": None, "bodySize": None,
            "color": "auto", "scrim": 70, "bodyAnchor": "bottom"}


def effective_card(pid: str, card: dict) -> dict:
    """스토리보드 카드에 사용자가 고친 그림 계획과 저장한 글자를 겹쳐 '지금 카드'를 만든다."""
    out = dict(card)
    ov = load_overrides(pid, card["id"])
    for k in ("imagePlan", "imageRole"):
        if ov.get(k):
            out[k] = ov[k]
    if ov.get("edit"):  # 그림 수정 요청: 기존 SVG를 보여 주고 요청한 부분만 고치게 한다(한 번만 쓰이고 만든 뒤 지운다)
        from . import svg_render
        art = card_dir(pid, card["id"]) / f"v{ov['edit']['baseVersion']}" / "art.svg"
        try:
            frag = svg_render.fragment_of(art.read_text(encoding="utf-8"))
        except OSError:
            frag = None
        if frag:
            out["edit"] = {"instruction": ov["edit"]["instruction"], "baseVersion": ov["edit"]["baseVersion"], "svg": frag}
    lay = saved_layout(pid, card["id"])
    if lay:
        out["title"], out["body"] = lay["title"], lay["body"]
    return out


def compose_and_save(pid: str, cid: str, base: Path, version: int, layout: dict) -> dict:
    """글자를 올려 v<버전>/final.png로 저장하고, 파일을 다시 열어 PNG·크기를 확인한 뒤 layout.json을 쓴다. typeset.TypesetError, OSError 가능."""
    d = card_dir(pid, cid)
    final = d / f"v{version}" / "final.png"
    result = typeset.compose(base, layout, final)
    check = images.inspect_png(final)
    if any(check["checks"][k] != "pass" for k in ("file", "format", "size")):
        raise typeset.TypesetError("글자를 올린 이미지 파일을 확인하지 못했습니다.")
    now = db.now_iso()
    (d / "layout.json").write_text(json.dumps({"imageVersion": version, "layout": layout, "ok": result["ok"], "checks": result["checks"],
                                              "savedAt": now}, ensure_ascii=False, indent=1), encoding="utf-8")
    return {**result, "savedAt": now}


def recompose_saved(pid: str, cid: str, base: Path, version: int) -> bool:
    """저장해 둔 글자 배치가 있으면 새 그림 위에 다시 올린다. 없으면 아무것도 하지 않는다."""
    lay = saved_layout(pid, cid)
    if not lay:
        return False
    compose_and_save(pid, cid, base, version, lay)
    return True


# --- 최종 승인 서명 ------------------------------------------------------------------
def final_status(c, pid: str) -> dict:
    """카드마다 합성본이 있고 검사에 실패한 곳이 없는지(승인 가능한지)와 지금 상태의 서명."""
    rows = c.execute("SELECT * FROM cards WHERE project_id=? ORDER BY ord", (pid,)).fetchall()
    blockers, parts = [], []
    if not rows:
        blockers.append({"cardId": None, "order": None, "reason": "카드가 없습니다."})
    for r in rows:
        cid, order = r["card_id"], r["ord"]
        final = card_dir(pid, cid) / f"v{r['current_version']}" / "final.png"
        meta = _read_json(card_dir(pid, cid) / "layout.json") or {}
        if r["status"] != "ready":
            blockers.append({"cardId": cid, "order": order, "reason": "이미지가 아직 준비되지 않았습니다."})
        elif not final.is_file() or meta.get("imageVersion") != r["current_version"]:
            blockers.append({"cardId": cid, "order": order, "reason": "글자를 올리지 않았습니다(또는 그림을 새로 만든 뒤 글자를 다시 올리지 않았습니다)."})
        elif not meta.get("ok"):
            bad = [k["message"] for k in meta.get("checks", []) if k["status"] == "fail"]
            blockers.append({"cardId": cid, "order": order, "reason": "글자 검사에 고쳐야 할 곳이 있습니다: " + " / ".join(bad)[:200]})
        else:
            parts.append([cid, r["current_version"], hashlib.sha256(final.read_bytes()).hexdigest(), meta.get("savedAt")])
    sig = hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest() if not blockers else None
    return {"ready": not blockers, "blockers": blockers, "signature": sig}


def approval_state(c, p) -> dict:
    st = final_status(c, p["id"])
    stored = p["final_approved_sig"]
    approved = bool(stored) and st["signature"] == stored
    return {"approved": approved, "approvedAt": p["final_approved_at"] if approved else None,
            "stale": bool(stored) and not approved, "ready": st["ready"], "blockers": st["blockers"]}
