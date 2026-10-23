"""글(문단) 처리: 문단 나누기, 지적 검증, 수정 적용, 결과 파일(수정본·리포트) 만들기. 모델을 부르지 않는 순수 함수들이다."""
import difflib
import re

CATEGORIES = ("구조", "논리", "근거 부족", "비약", "조건 미충족")
MAX_FINDINGS = 5
MAX_ROUNDS = 3
SEVERITY = ('high', 'medium', 'low')
SEVERITY_LABEL = {'high': '높음', 'medium': '보통', 'low': '낮음'}
MAX_BODY = 30000
CITE_RE = re.compile(r"\[(\d{1,2})\]")
NUMBER_RE = re.compile(r"\d[\d,.]*\d|\d")
GAP_MARK = "⚠[자료 보강 필요: {note}]"


class FormatError(Exception):
    """모델 답변이 약속한 형식이 아님. 메시지는 모델에게 되돌려 줄 수 있는 설명이다."""


def split_paragraphs(text: str) -> list[dict]:
    """빈 줄로 문단을 나눈다. 빈 줄이 하나도 없는 글(txt로 저장하면서 사라진 경우)은 줄바꿈 하나로 나눈다."""
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = [b.strip() for b in re.split(r"\n\s*\n", t) if b.strip()]
    if len(blocks) == 1 and "\n" in blocks[0]:
        raw = [ln.rstrip() for ln in t.split("\n") if ln.strip()]
        indented = [ln[0] in " \t\u3000" for ln in raw]
        if sum(indented[1:]) >= 2 and not all(indented[1:]):
            # 들여쓰기로 문단이 시작되는 글(복사하면서 문장 중간에 줄이 바뀐 경우): 들여쓰기 없는 줄은 앞 문단에 이어 붙인다
            blocks = []
            for ln, ind in zip(raw, indented):
                if not blocks or ind:
                    blocks.append(ln.strip())
                else:
                    blocks[-1] += " " + ln.strip()
        else:
            blocks = [b.strip() for b in blocks[0].split("\n") if b.strip()]
    return [{"id": f"P{i}", "text": b} for i, b in enumerate(blocks, start=1)]


def numbered(paragraphs: list[dict]) -> str:
    return "\n\n".join(f"[{p['id']}]\n{p['text']}" for p in paragraphs)


def _s(value, limit: int, smart: bool = True) -> str:
    """값을 문자열로 정리하고 limit자를 넘으면 줄인다. smart면 문장 끝(없으면 띄어쓰기)에서 자르고 …를 붙여 중간에서 뚝 끊기지 않게 한다."""
    text = re.sub(r"[\x00-\x08\x0b-\x1f]", "", str(value or "")).strip()
    if len(text) <= limit:
        return text
    if not smart:
        return text[:limit]
    cut = text[:limit - 1]
    ends = [m.end() for m in re.finditer(r"[.!?。](?=\s|$)|다\.", cut)]
    ends = [e for e in ends if e >= limit * 0.4]
    if ends:
        return cut[:ends[-1]].rstrip() + " …"
    space = cut.rfind(" ")
    return (cut[:space] if space >= limit * 0.4 else cut).rstrip(" ,;:") + "…"


def validate_findings(raw: dict, paragraphs: list[dict]) -> list[dict]:
    """분석 결과를 검증한다. 인용문이 문단에 없으면 FormatError(한 번 고쳐 쓰게 한다), 형식이 틀려도 FormatError."""
    items = raw.get("findings")
    if not isinstance(items, list) or not items:
        raise FormatError('"findings" 배열이 비어 있거나 없습니다. 지적할 것이 없다면 이유를 problem에 쓴 지적 1개라도 주세요.')
    by_id = {p["id"]: p["text"] for p in paragraphs}
    out, errors = [], []
    for i, f in enumerate(items[:MAX_FINDINGS], start=1):
        if not isinstance(f, dict):
            raise FormatError(f"{i}번째 지적이 객체가 아닙니다.")
        pid = _s(f.get("paragraphId"), 8)
        cat = _s(f.get("category"), 12)
        if pid not in by_id:
            raise FormatError(f"{i}번째 지적의 paragraphId({pid!r})가 글에 없습니다. 있는 문단: {', '.join(by_id)}")
        if cat not in CATEGORIES:
            raise FormatError(f"{i}번째 지적의 category({cat!r})는 {', '.join(CATEGORIES)} 중 하나여야 합니다.")
        quote = _s(f.get("quote"), 300, smart=False)  # 인용문은 원문 일부여야 하므로 …를 붙이지 않는다
        if quote and quote not in by_id[pid]:
            errors.append(f'{i}번째 지적({pid})의 quote "{quote[:40]}…"가 그 문단에 그대로 없습니다. 문단에서 글자 하나 바꾸지 말고 복사해서 쓰세요.')
        problem, why = _s(f.get("problem"), 700), _s(f.get("why"), 500)
        if not problem:
            raise FormatError(f"{i}번째 지적에 problem이 없습니다.")
        dirs = []
        for d in (f.get("directions") or [])[:2]:
            if isinstance(d, dict) and _s(d.get("label"), 80) and _s(d.get("detail"), 600):
                dirs.append({"id": "ab"[len(dirs)], "label": _s(d["label"], 80), "detail": _s(d["detail"], 600),
                             "needsSearch": bool(d.get("needsSearch"))})
        if len(dirs) != 2:
            raise FormatError(f"{i}번째 지적에는 수정 방향(directions)이 정확히 2개(label, detail, needsSearch)여야 합니다.")
        rec = _s(f.get("recommended"), 1).lower()
        out.append({"id": f"f{i}", "priority": i, "category": cat, "paragraphId": pid, "quote": quote, "problem": problem,
                    "why": why, "directions": dirs, "recommended": rec if rec in ("a", "b") else "a"})
    if errors:
        raise FormatError(" ".join(errors))
    return out


def _directions(raw_dirs, where: str) -> list[dict]:
    dirs = []
    for d in (raw_dirs or [])[:2]:
        if isinstance(d, dict) and _s(d.get("label"), 80) and _s(d.get("detail"), 600):
            dirs.append({"id": "ab"[len(dirs)], "label": _s(d["label"], 80), "detail": _s(d["detail"], 600), "needsSearch": bool(d.get("needsSearch"))})
    if len(dirs) != 2:
        raise FormatError(f"{where}에는 수정 방향(directions)이 정확히 2개(label, detail, needsSearch)여야 합니다.")
    return dirs


def validate_issues(raw_list, default_pid: str, paragraph_ids: set[str], where: str) -> list[dict]:
    """재검수가 찾은 '새로 생긴 문제'를 검증한다. 중요도(high/medium/low)가 있어야 하고 최대 2개.
    high/medium은 다음 라운드로 이을 수 있도록 수정 방향 2개와 추천이 있어야 한다. low는 참고용이라 방향이 없어도 된다."""
    out = []
    for i, x in enumerate((raw_list or [])[:2], start=1):
        if isinstance(x, str):  # 옛 형식(글만)은 중요도 보통의 참고 항목으로 본다
            x = {"text": x, "severity": "low"}
        if not isinstance(x, dict) or not _s(x.get("text"), 700):
            raise FormatError(f"{where}의 {i}번째 새 문제에 text가 없습니다.")
        sev = _s(x.get("severity"), 8).lower()
        if sev not in SEVERITY:
            raise FormatError(f"{where}의 {i}번째 새 문제의 severity는 high / medium / low 중 하나여야 합니다.")
        pid = _s(x.get("paragraphId"), 8)
        issue = {"id": f"{i}", "text": _s(x.get("text"), 700), "severity": sev, "paragraphId": pid if pid in paragraph_ids else default_pid}
        if sev != "low":
            issue["directions"] = _directions(x.get("directions"), f"{where}의 {i}번째 새 문제")
            rec = _s(x.get("recommended"), 1).lower()
            issue["recommended"] = rec if rec in ("a", "b") else "a"
        out.append(issue)
    return out


def held_to_finding(item: dict, material: str, rnd: int) -> dict:
    """자료가 부족해 보류한 항목에 사용자가 자료를 붙여 넣었을 때, 다음 라운드의 지적으로 바꾼다. 같은 선택·수정 흐름을 탄다."""
    f = item["finding"]
    needed = "; ".join(h["needed"] for h in item.get("held", []) if h.get("needed")) or "(구체적으로 정하지 못함)"
    return {"id": "", "priority": 0, "category": f["category"], "paragraphId": f["paragraphId"], "quote": f.get("quote", ""), "problem": f["problem"],
            "why": f"라운드 {rnd}에서 자료가 부족해 보류한 항목입니다. 필요했던 자료: {needed}",
            "directions": [{"id": "a", "label": "제공한 자료로 보강", "detail": "붙여 넣은 자료에 있는 내용만 근거로 고친다. 자료에 없는 것은 계속 보류한다.", "needsSearch": False},
                           {"id": "b", "label": "제공한 자료 + 웹 검색으로 보강", "detail": "붙여 넣은 자료를 근거로 쓰고, 부족한 부분은 웹에서 더 찾는다.", "needsSearch": True}],
            "recommended": "a", "material": _s(material, 8000, smart=False), "fromFinding": f["id"]}


def issue_to_finding(item_finding_id: str, issue: dict, rnd: int) -> dict:
    """다음 라운드용 지적 한 건으로 바꾼다(처음 지적과 같은 모양이라 같은 선택·수정 흐름을 탄다)."""
    return {"id": "", "priority": 0, "category": "새 문제", "paragraphId": issue["paragraphId"], "quote": "", "problem": issue["text"],
            "why": f"라운드 {rnd}에서 {item_finding_id}를 고치다 생긴 문제입니다(중요도 {SEVERITY_LABEL[issue['severity']]}).",
            "directions": issue["directions"], "recommended": issue.get("recommended", "a"), "severity": issue["severity"]}


GAP_RE = re.compile(r"⚠\[자료 보강 필요: ([^\]]*)\]")


def strip_gaps(text: str) -> str:
    """문단 안의 ⚠[자료 보강 필요: …] 표시를 지운다(사용자가 자료를 줘서 다시 고칠 때, 남은 보류는 모델이 held로 다시 적는다)."""
    return re.sub(r"\s*" + GAP_RE.pattern, "", text).strip()


def gaps_in(paragraphs: list[dict]) -> list[str]:
    """글에 남아 있는 '자료 보강 필요' 표시들(문단 id: 설명). 라운드를 넘어도 글에서 직접 읽으므로 항상 지금 상태와 맞는다."""
    out = []
    for p in paragraphs:
        for m in GAP_RE.finditer(p["text"]):
            # 한 문단의 보류가 여러 개면 "…(필요한 자료: …); 다음 내용 (…)"로 이어 붙어 있다 → 보류마다 한 줄로 나눈다
            for part in re.split(r"(?<=\))\s*;\s+", m.group(1)):
                if part.strip():
                    out.append(f"{p['id']}: {part.strip()}")
    return out


def clean_choices(raw, findings: list[dict]) -> dict:
    """선택을 정리한다: 지적 id -> 'a'|'b'|'skip'|{'custom': 글}. 없는 지적·이상한 값은 건너뛰기로 본다."""
    ids = {f["id"] for f in findings}
    out = {}
    for fid, v in (raw or {}).items():
        if fid not in ids:
            continue
        if v in ("a", "b", "skip"):
            out[fid] = v
        elif isinstance(v, dict) and _s(v.get("custom"), 300):
            out[fid] = {"custom": _s(v["custom"], 300)}
        else:
            out[fid] = "skip"
    return out


def chosen_direction(finding: dict, choice) -> dict | None:
    """선택한 방향을 {label, detail, needsSearch}로. 건너뛰기면 None."""
    if choice in (None, "skip"):
        return None
    if isinstance(choice, dict):
        return {"label": "직접 쓴 방향", "detail": choice["custom"], "needsSearch": True}
    d = next((x for x in finding["directions"] if x["id"] == choice), None)
    return d


def diff_ops(before: str, after: str) -> list[list]:
    """고치기 전·후를 단어 단위로 비교해 [["eq"|"del"|"ins", 글], ...]로 돌려준다."""
    a, b = re.findall(r"\S+|\s+", before), re.findall(r"\S+|\s+", after)
    ops: list[list] = []
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    if sm.ratio() < 0.55 and max(len(before), len(after)) > 120:  # 짧은 글은 단어 단위 비교가 더 읽기 좋다
        # 거의 새로 쓴 글이면 단어를 이리저리 맞추지 않고(뒤섞여 읽기 어렵다) 앞뒤의 같은 부분만 남기고 가운데를 통째로 바꾼 것으로 보여 준다
        i = 0
        while i < min(len(a), len(b)) and a[i] == b[i]:
            i += 1
        j = 0
        while j < min(len(a), len(b)) - i and a[-1 - j] == b[-1 - j]:
            j += 1
        for kind, toks in (("eq", a[:i]), ("del", a[i:len(a) - j]), ("ins", b[i:len(b) - j]), ("eq", a[len(a) - j:])):
            if toks:
                ops.append([kind, "".join(toks)])
        return ops
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            ops.append(["eq", "".join(a[i1:i2])])
            continue
        if i2 > i1:
            ops.append(["del", "".join(a[i1:i2])])
        if j2 > j1:
            ops.append(["ins", "".join(b[j1:j2])])
    return ops


def collapse_ops(ops: list[list], keep: int = 60) -> list[list]:
    """바뀌지 않은 긴 구간은 앞뒤 keep자만 남기고 ["skip", 생략한 글자 수]로 줄인다(긴 문단에서 바뀐 곳이 묻히지 않게)."""
    out: list[list] = []
    for n, (kind, text) in enumerate(ops):
        if kind != "eq" or len(text) <= keep * 2 + 20:
            out.append([kind, text])
            continue
        head = "" if n == 0 else text[:keep]
        tail = "" if n == len(ops) - 1 else text[-keep:]
        if head:
            out.append(["eq", head])
        out.append(["skip", len(text) - len(head) - len(tail)])
        if tail:
            out.append(["eq", tail])
    return out


def ops_to_markdown(ops: list[list]) -> str:
    """삭제는 ~~취소선~~, 추가는 **굵게**로 표시한다. 마크다운이 깨지지 않게 앞뒤 공백은 표시 밖으로 뺀다."""
    def wrap(text: str, mark: str) -> str:
        core = text.strip()
        if not core:
            return text
        lead, trail = text[:len(text) - len(text.lstrip())], text[len(text.rstrip()):]
        return f"{lead}{mark}{core}{mark}{trail}"
    parts = []
    for kind, text in ops:
        if kind == "eq":
            parts.append(text)
        elif kind == "del":
            parts.append(wrap(text, "~~"))
        elif kind == "ins":
            parts.append(wrap(text, "**"))
        else:
            parts.append(f" … (같은 글 {text}자 생략) … ")
    return "".join(parts)


def numbers_in(text: str) -> set[str]:
    return {m.group(0).replace(",", "") for m in NUMBER_RE.finditer(text or "")}


USER_MATERIAL = "user-material"  # 사용자가 직접 붙여 넣은 자료를 가리키는 출처 표시(주소가 아니다)


def parse_held(raw_held) -> list[dict]:
    """보류한 내용과 그걸 해결하려면 필요한 추가 자료. 최대 3개."""
    out = []
    for h in (raw_held or [])[:3]:
        if isinstance(h, dict) and _s(h.get("claim"), 300):
            out.append({"claim": _s(h.get("claim"), 300), "needed": _s(h.get("needed"), 300)})
    return out


def held_note(held: list[dict]) -> str:
    """글 안의 ⚠ 표시에 넣을 문장. 대괄호는 표시를 깨므로 소괄호로 바꾼다."""
    # 글 안의 표시는 짧게(전체 내용은 화면과 리포트의 '보류한 내용' 목록에 있다)
    return "; ".join(f"{_s(h['claim'], 80)} (필요한 자료: {_s(h['needed'] or '출처 확인 필요', 100)})" for h in held).replace("]", ")").replace("[", "(")


def validate_revision(raw: dict, original: str, allowed_urls: set[str], used_search: bool, norm, offset: int = 0, material: str | None = None) -> dict:
    """수정 결과를 검증한다. 자료가 있으면 revised, 없으면 insufficient. 지어낸 출처·짝이 안 맞는 [n]은 FormatError.
    material은 사용자가 직접 제공한 자료(있으면 sources에 url="user-material"로 쓸 수 있고, 인용문은 그 자료에 있어야 한다)."""
    status = _s(raw.get("status"), 12)
    if status not in ("revised", "insufficient"):
        raise FormatError('status는 "revised" 또는 "insufficient"여야 합니다.')
    gap = _s(raw.get("gap"), 700)
    held = parse_held(raw.get("held"))
    sources = []
    warnings = []
    for s in raw.get("sources") or []:
        if not isinstance(s, dict):
            continue
        url = _s(s.get("url"), 400)
        if url == USER_MATERIAL:
            if material is None:
                raise FormatError("사용자가 제공한 자료가 없는데 user-material을 출처로 썼습니다.")
            q = _s(s.get("quote"), 400, smart=False)
            if q and q not in material:
                warnings.append(f"사용자가 제공한 자료에 그대로 없는 인용문이다: \"{q[:40]}\" — 제공한 자료와 대조하세요.")
            sources.append({"url": USER_MATERIAL, "title": "사용자 제공 자료", "quote": q})
            continue
        n = norm(url)
        if not n:
            continue
        if n not in allowed_urls:
            raise FormatError(f"sources의 주소 {url[:80]}는 이번 검색·열람 기록에 없습니다. 검색 결과로 받았거나 직접 연 주소만 쓰세요.")
        sources.append({"url": url, "title": _s(s.get("title"), 160), "quote": _s(s.get("quote"), 400)})
    if status == "insufficient":
        gap = gap or "근거로 쓸 만한 자료를 찾지 못했다."
        return {"status": "insufficient", "newText": original, "sources": [], "gap": gap, "warnings": [],
                "held": held or [{"claim": gap, "needed": ""}]}
    new = _s(raw.get("newText"), 6000)
    if not new:
        raise FormatError("revised인데 newText가 비어 있습니다.")
    cites = {int(x) for x in CITE_RE.findall(new)}
    old_cites = {int(x) for x in CITE_RE.findall(original)}  # 지난 라운드에서 이미 붙은 인용 표시는 그대로 둘 수 있다
    if (used_search or material is not None) and not sources:
        raise FormatError("자료를 쓰는 수정인데 sources가 없습니다. 쓴 근거의 주소를 sources에 넣거나 insufficient로 답하세요.")
    allowed = old_cites | set(range(offset + 1, offset + len(sources) + 1))
    bad = sorted(cites - allowed)
    if bad:
        raise FormatError(f"본문의 [{bad[0]}]에 해당하는 자료가 없습니다. 새로 쓴 자료는 sources 순서대로 [{offset + 1}]부터 번호를 이어 쓰고(지금 sources는 {len(sources)}개), 이미 있던 번호는 그대로 두세요.")
    cites = cites - old_cites
    known = numbers_in(original) | numbers_in(" ".join(s["quote"] for s in sources)) | (numbers_in(material) if material else set())
    fresh = sorted(numbers_in(new) - known - {str(n) for n in cites})
    if fresh:
        warnings.append("근거 인용문과 원문에서 찾지 못한 수치가 있다: " + ", ".join(fresh) + " — 출처에서 직접 확인하세요.")
    if sources and not cites:
        warnings.append("자료를 찾았지만 본문에 새 [n] 인용 표시가 없다.")
    return {"status": "revised", "newText": new, "sources": sources, "gap": "", "warnings": warnings, "held": held}


def apply_revisions(paragraphs: list[dict], results: dict[str, dict]) -> list[dict]:
    """results: 문단 id -> {text, gap}. 바뀐 문단만 교체하고(자료 부족은 표시를 덧붙임) 나머지는 그대로 둔다."""
    out = []
    for p in paragraphs:
        r = results.get(p["id"])
        if not r:
            out.append(dict(p))
            continue
        text = r["text"]
        if r.get("gap"):
            text = text.rstrip() + " " + GAP_MARK.format(note=r["gap"])
        out.append({"id": p["id"], "text": text})
    return out


def untouched_ok(before: list[dict], after: list[dict], changed_ids: set[str]) -> list[str]:
    """고르지 않은 문단이 그대로인지 검사한다. 어긋난 문단 id 목록을 돌려준다(비어 있으면 통과)."""
    if [p["id"] for p in before] != [p["id"] for p in after]:
        return ["문단 수·순서가 바뀜"]
    return [b["id"] for b, a in zip(before, after) if b["id"] not in changed_ids and b["text"] != a["text"]]


def build_final_md(title: str, paragraphs: list[dict], sources: list[dict], gaps: list[str]) -> str:
    lines = [f"# {title}", ""]
    lines += [p["text"] + "\n" for p in paragraphs]
    if sources:
        lines += ["---", "", "## 참고 자료", ""] + [f"[{s['n']}] {s['title'] or s['url']} — {s['url']}" for s in sources] + [""]
    if gaps:
        lines += ["---", "", "## 자료 보강이 필요한 부분", ""] + [f"- {g}" for g in gaps] + [""]
    return "\n".join(lines).rstrip() + "\n"


VERDICT = {"resolved": "해결", "partial": "일부 해결", "unresolved": "미해결", "insufficient": "자료 부족", "skipped": "건너뜀"}


def _round_md(rnd: dict, project: dict) -> list[str]:
    findings, items, checks = rnd["findings"], rnd["result"]["items"], rnd["result"]["checks"]
    lines = []
    n = {k: sum(1 for i in items if i["verdict"] == k) for k in VERDICT}
    lines += [f"요약: 지적 {len(findings)}개 — 해결 {n['resolved']} · 일부 해결 {n['partial']} · 미해결 {n['unresolved']} · 자료 부족 {n['insufficient']} · 건너뜀 {n['skipped']}", ""]
    for it in items:
        f = it["finding"]
        lines += [f"### {f['priority']}. [{f['category']}] {f['paragraphId']} — {VERDICT[it['verdict']]}", ""]
        if f["quote"]:
            lines += [f"> {f['quote']}", ""]
        lines += [f"- 문제: {f['problem']}", f"- 왜 중요한가: {f['why']}", f"- 선택한 방향: {it['directionLabel']}"]
        if it.get("detail"):
            lines.append(f"- 방향 설명: {it['detail']}")
        if it["verdict"] != "skipped":
            md = ops_to_markdown(collapse_ops(diff_ops(it["before"], it["after"])))
            lines += ["", "**변경 표시** (~~삭제~~ / **추가**)", "", md, ""]
            if len(it["after"]) <= 1500:
                lines += ["**고친 뒤 전문**", "", it["after"], ""]
            else:
                lines += [f"(문단이 길어(고친 뒤 {len(it['after'])}자) 전문은 수정본 final.md에서 확인하세요.)", ""]
            if it["sources"]:
                lines += ["- 근거: " + "; ".join(f"[{s['n']}] {s['url']}" for s in it["sources"])]
            lines += [f"- 재검수: {it['reason']}"]
            for w in it["warnings"]:
                lines.append(f"- 확인 필요: {w}")
            for h in it.get("held", []):
                lines.append(f"- 확인하지 못해 보류한 내용: {h['claim']} → 필요한 추가 자료: {h['needed'] or '(구체적으로 정하지 못함)'}")
            for ni in it.get("newIssues", []):
                text = ni["text"] if isinstance(ni, dict) else ni
                sev = SEVERITY_LABEL.get(ni.get("severity"), "") if isinstance(ni, dict) else ""
                lines.append(f"- 새로 생긴 문제{f'(중요도 {sev})' if sev else ''}: {text}")
        lines.append("")
    lines += [f"기계 검사: 고르지 않은 문단 변경 여부 — {'모두 그대로' if not checks['untouchedChanged'] else '바뀐 문단 있음 ' + ', '.join(checks['untouchedChanged'])}. 수정한 문단: {', '.join(checks['changed']) or '없음'}", ""]
    return lines


def remaining_issues(items: list[dict]) -> dict:
    """마지막 라운드 기준 '남은 문제'. 새 문제 중요도별 개수와 미해결·일부 해결·자료 부족 개수."""
    sev = {k: 0 for k in SEVERITY}
    for it in items:
        for ni in it.get("newIssues", []):
            if isinstance(ni, dict) and ni.get("severity") in sev:
                sev[ni["severity"]] += 1
    verdicts = {k: sum(1 for i in items if i["verdict"] == k) for k in ("unresolved", "partial", "insufficient")}
    held = sum(len(i.get("held", [])) for i in items)  # 근거가 없어 보류한 내용의 수
    return {"newIssues": sev, **verdicts, "held": held,
            "usable": sev["high"] == 0 and verdicts["unresolved"] == 0 and verdicts["insufficient"] == 0 and held == 0}


def build_report_md(project: dict, rounds: list[dict]) -> str:
    """rounds: [{round, findings, result}] 라운드 순서. 마지막이 최종이다."""
    last = rounds[-1]["result"]
    rem = remaining_issues(last["items"])
    lines = [f"# 검수 리포트 — {project['title']}", "", f"- 검수 시각: {project['finishedAt']}", f"- 제출 조건: {project['conditions'] or '(입력 없음)'}", f"- 라운드: {len(rounds)}번",
             "", "## 남은 문제 (마지막 라운드 기준)", "",
             f"- 새로 생긴 문제 — 중요도 높음 {rem['newIssues']['high']} · 보통 {rem['newIssues']['medium']} · 낮음 {rem['newIssues']['low']}",
             f"- 미해결 {rem['unresolved']} · 일부 해결 {rem['partial']} · 자료 부족 {rem['insufficient']}",
             f"- 판단: {'중요도 높은 새 문제·미해결·자료 부족이 없어 쓸 만한 상태입니다(보통·낮은 새 문제와 일부 해결은 직접 판단하세요).' if rem['usable'] else '아직 손볼 곳이 남아 있습니다. 아래 항목을 확인하세요.'}", ""]
    for rnd in rounds:
        lines += [f"## 라운드 {rnd['round']}", ""] + _round_md(rnd, project)
    return "\n".join(lines).rstrip() + "\n"
