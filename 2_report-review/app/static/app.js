"use strict";
// 단일 페이지 화면. 서버 상태(GET /api/projects/{id})만 보고 그린다. 새로고침해도 같은 화면으로 돌아온다.
const root = document.getElementById("app");
const ui = { id: null, p: null, list: null, error: null, busy: false, form: { title: "", conditions: "", body: "" }, custom: {}, sig: "", nextSel: new Set(), mat: {} };
const SEV = { high: ["높음", "err"], medium: ["보통", "warn"], low: ["낮음", ""] };
const VERDICT = { resolved: ["해결", "ok"], partial: ["일부 해결", "warn"], unresolved: ["미해결", "err"], insufficient: ["자료 부족", "warn"], skipped: ["건너뜀", ""] };
let pollTimer = null;

function el(tag, attrs, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v == null) continue;
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (k === "checked" || k === "disabled" || k === "value") n[k] = v;
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) { if (kid == null || kid === false) continue; n.append(kid.nodeType ? kid : document.createTextNode(String(kid))); }
  return n;
}

async function api(method, path, body) {
  let res;
  try {
    res = await fetch(path, { method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined, cache: "no-store" });
  } catch (_) { throw { network: true, message: "서버에 연결하지 못했습니다.", nextAction: "서버가 켜져 있는지 확인하세요. 자동으로 다시 시도합니다." }; }
  let data = null;
  try { data = await res.json(); } catch (_) { /* 본문이 없을 수 있다 */ }
  if (!res.ok) { const e = (data && data.error) || {}; throw { status: res.status, code: e.code, message: e.message || "요청이 실패했습니다.", nextAction: e.nextAction || "" }; }
  return data;
}

async function act(fn) {
  if (ui.busy) return null;
  ui.busy = true; ui.error = null; draw();
  try { return await fn(); } catch (e) { ui.error = e; if (e.code === "stale_version") await refresh(true); return null; } finally { ui.busy = false; draw(); }
}

async function refresh(force) {
  if (!ui.id) return;
  try {
    const p = await api("GET", `/api/projects/${ui.id}`);
    const sig = [p.version, p.stage, p.currentRun && p.currentRun.status, p.currentRun && p.currentRun.message].join("|");
    ui.p = p;
    if (sig !== ui.sig || force) { ui.sig = sig; draw(); }
  } catch (e) { if (e.status === 404) { ui.p = null; ui.error = e; draw(); } }
}

function schedulePoll() {
  clearTimeout(pollTimer);
  const run = ui.p && ui.p.currentRun;
  const active = run && (run.status === "queued" || run.status === "running");
  pollTimer = setTimeout(async () => { await refresh(); schedulePoll(); }, active ? 2000 : 5000);
}

function draw() {
  const kids = [];
  if (ui.error) kids.push(el("div", { class: "alert err", role: "alert" }, el("b", {}, ui.error.message || "오류가 발생했습니다."), ui.error.nextAction ? el("div", {}, "다음 행동: " + ui.error.nextAction) : null));
  if (!ui.id) kids.push(...viewStart());
  else if (!ui.p) kids.push(el("p", { class: "muted" }, "불러오는 중…"));
  else kids.push(...viewProject(ui.p));
  root.replaceChildren(...kids.filter(Boolean)); // null을 그대로 넣으면 화면에 'null'이라는 글자가 나온다
}

// --- 시작 화면 ---
function viewStart() {
  const f = ui.form;
  const ok = f.title.trim() && f.body.trim() && !ui.busy;
  const out = [el("h1", {}, "검수할 글을 넣어 주세요"),
    el("div", { class: "card" },
      el("label", { class: "field", for: "title" }, "과제 제목(주제)"),
      el("input", { type: "text", id: "title", maxlength: "200", value: f.title, placeholder: "예: 생성형 AI가 대학 교육에 미치는 영향", oninput: (e) => { f.title = e.target.value; sync(); } }),
      el("label", { class: "field", for: "cond" }, "제출 조건 (선택)"),
      el("input", { type: "text", id: "cond", maxlength: "1000", value: f.conditions, placeholder: "예: 2000자 안팎, 보고서, 교수님이 읽음, 출처 3개 이상", oninput: (e) => { f.conditions = e.target.value; } }),
      el("label", { class: "field", for: "body" }, "글 본문 (문단은 빈 줄로 나눕니다)"),
      el("textarea", { id: "body", value: f.body, placeholder: "여기에 글을 붙여 넣거나 아래에서 .md / .txt 파일을 올리세요.", oninput: (e) => { f.body = e.target.value; sync(); } }),
      el("div", { class: "row", style: "margin-top:10px" },
        el("input", { type: "file", accept: ".md,.txt,text/plain,text/markdown", onchange: readFile }),
        el("button", { class: "primary", id: "go", disabled: !ok, onclick: start }, "검수 시작")),
      el("p", { class: "muted" }, "Claude CLI가 글을 읽고 구조·논리·근거 부족을 찾습니다. 몇 분 걸릴 수 있습니다."))];
  out.push(el("h2", {}, "이전 검수"));
  if (!ui.list) loadList();
  else if (!ui.list.length) out.push(el("p", { class: "muted" }, "아직 없습니다."));
  else out.push(...ui.list.map((x) => el("div", { class: "card" }, el("a", { href: "#/p/" + x.projectId }, x.title), el("div", { class: "muted" }, `${x.stage} · ${x.updatedAt.replace("T", " ").slice(0, 16)}`))));
  return out;
}
function sync() { const b = document.getElementById("go"); if (b) b.disabled = !(ui.form.title.trim() && ui.form.body.trim()) || ui.busy; }
function readFile(e) {
  const file = e.target.files[0];
  if (!file) return;
  const r = new FileReader();
  r.onload = () => { ui.form.body = String(r.result); if (!ui.form.title.trim()) ui.form.title = file.name.replace(/\.(md|txt)$/i, ""); draw(); };
  r.readAsText(file, "utf-8");
}
async function loadList() { try { ui.list = (await api("GET", "/api/projects")).projects; } catch (_) { ui.list = []; } draw(); }
async function start() {
  const r = await act(() => api("POST", "/api/projects", ui.form));
  if (r) { ui.form = { title: "", conditions: "", body: "" }; location.hash = "#/p/" + r.projectId; }
}

// --- 프로젝트 화면 ---
function viewProject(p) {
  const head = [el("div", { class: "row" }, el("a", { href: "#/" }, "← 처음으로"), el("span", { class: "badge" }, `라운드 ${p.round}/${p.maxRounds}`)), el("h1", {}, p.title),
    p.conditions ? el("p", { class: "muted" }, "제출 조건: " + p.conditions) : null];
  const run = p.currentRun;
  const active = run && (run.status === "queued" || run.status === "running");
  if (active) return [...head, progress(p, run)];
  const notice = run && (run.status === "failed" || run.status === "interrupted") ? failNotice(p, run) : null;
  if (p.stage === "created") return [...head, notice, el("div", { class: "card" }, el("p", {}, "아직 분석 결과가 없습니다."),
    el("button", { class: "primary", disabled: ui.busy, onclick: () => act(async () => { await api("POST", `/api/projects/${p.projectId}/analyze`, { version: p.version }); await refresh(true); }) }, "다시 분석"))];
  if (p.stage === "findings_ready") return [...head, notice, ...viewFindings(p)];
  if (p.stage === "done") return [...head, ...viewResult(p)];
  return [...head, el("p", { class: "muted" }, "상태: " + p.stage)];
}

function progress(p, run) {
  const label = run.kind === "analyze" ? "글을 검수하는 중" : "수정하고 재검수하는 중";
  return el("div", { class: "card" }, el("div", { class: "row" }, el("span", { class: "spinner" }), el("b", {}, label)),
    el("p", { class: "muted" }, run.message || ""),
    el("button", { onclick: () => act(async () => { await api("POST", `/api/runs/${run.runId}/cancel`, {}); await refresh(true); }) }, "취소"));
}

function failNotice(p, run) {
  const e = run.error || {};
  return el("div", { class: "alert err", role: "alert" }, el("b", {}, run.status === "interrupted" ? "서버가 다시 시작되면서 작업이 중단되었습니다." : (e.message || "작업이 실패했습니다.")),
    e.nextAction ? el("div", {}, "다음 행동: " + e.nextAction) : null, e.code ? el("div", { class: "muted" }, "오류 코드: " + e.code) : null);
}

function viewFindings(p) {
  const picks = Object.values(p.choices).filter((v) => v !== "skip").length;
  const out = [el("h2", {}, `지적 ${p.findings.length}개 (중요한 순서)`),
    el("p", { class: "muted" }, "지적마다 수정 방향을 고르세요. 고르지 않은 항목과 건너뛴 항목, 지적이 없는 문단은 그대로 둡니다. 자료가 필요한 방향은 웹에서 검색해 근거를 붙입니다.")];
  if (p.paragraphs.length === 1 && p.paragraphs[0].text.length > 800) {
    out.push(el("div", { class: "alert warn" }, `글 전체(${p.paragraphs[0].text.length}자)가 문단 하나(P1)로 읽혔습니다. 문단 사이에 빈 줄이 없으면 이렇게 됩니다. 이대로 고치면 글 전체를 한 번에 바꾸게 되니, 빈 줄을 넣은 글로 다시 검수하는 것을 권합니다.`));
  }
  for (const f of p.findings) out.push(findingCard(p, f));
  out.push(el("div", { class: "card row" }, el("button", { class: "primary", disabled: ui.busy || !picks,
    onclick: () => act(async () => { await api("POST", `/api/projects/${p.projectId}/revise`, { version: p.version }); await refresh(true); }) }, `선택한 ${picks}개 수정하고 재검수`),
    !picks ? el("span", { class: "muted" }, "하나 이상 골라야 시작할 수 있습니다.") : null));
  return out;
}

async function choose(p, fid, value) {
  const next = { ...p.choices, [fid]: value };
  try { p.choices = (await api("PUT", `/api/projects/${p.projectId}/choices`, { choices: next })).choices; } catch (e) { ui.error = e; }
  draw();
}

function findingCard(p, f) {
  const cur = p.choices[f.id];
  const opt = (id, title, detail, tag) => el("label", { class: "opt" + (cur === id ? " sel" : "") },
    el("input", { type: "radio", name: "ch_" + f.id, checked: cur === id, onchange: () => choose(p, f.id, id) }), " ", el("b", {}, title), tag ? " " : null, tag ? el("span", { class: "badge ok" }, tag) : null,
    detail ? el("div", { class: "muted" }, detail + "") : null);
  const custom = ui.custom[f.id] ?? (cur && cur.custom) ?? "";
  return el("div", { class: "card" },
    el("div", { class: "row" }, el("span", { class: "badge" }, `${f.priority}순위`), el("span", { class: "badge warn" }, f.category),
      f.severity ? el("span", { class: "badge " + SEV[f.severity][1] }, `중요도 ${SEV[f.severity][0]}`) : null, el("span", { class: "muted" }, f.paragraphId)),
    f.quote ? el("blockquote", {}, f.quote) : null,
    el("p", {}, el("b", {}, "문제 "), f.problem), f.why ? el("p", { class: "muted" }, "왜 중요한가: " + f.why) : null,
    ...f.directions.map((d) => opt(d.id, `${d.id.toUpperCase()}. ${d.label}`, d.detail + (d.needsSearch ? " (자료 검색 필요)" : ""), f.recommended === d.id ? "추천" : "")),
    el("label", { class: "opt" + (cur && cur.custom ? " sel" : "") }, el("input", { type: "radio", name: "ch_" + f.id, checked: !!(cur && cur.custom), onchange: () => { if (custom.trim()) choose(p, f.id, { custom }); } }),
      " ", el("b", {}, "직접 쓴 방향"), el("input", { type: "text", value: custom, maxlength: "300", placeholder: "예: 통계청 자료로 수치를 뒷받침하고 싶어요",
        oninput: (e) => { ui.custom[f.id] = e.target.value; }, onchange: (e) => { if (e.target.value.trim()) choose(p, f.id, { custom: e.target.value }); } })),
    opt("skip", "건너뛰기", "이 지적은 고치지 않습니다.", ""));
}

function renderDiff(ops) {
  return (ops || []).map(([kind, text]) => kind === "ins" ? el("ins", {}, text) : kind === "del" ? el("del", {}, text)
    : kind === "skip" ? el("span", { class: "skip" }, ` … 같은 글 ${text}자 생략 … `) : document.createTextNode(text));
}

function fullText(label, text) {
  return el("details", { open: text.length <= 400 }, el("summary", {}, `${label} (${text.length}자)`), el("div", { class: "fulltext" }, text));
}

async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch (_) {
    const ta = document.createElement("textarea");
    ta.value = text; document.body.append(ta); ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (_) { /* 복사 권한이 없을 수 있다 */ }
    ta.remove();
    return ok;
  }
}

function copyBtn(label, getText) {
  return el("button", { onclick: async (e) => {
    const b = e.currentTarget;
    const ok = await copyText(await getText());
    b.textContent = ok ? "복사했습니다 ✓" : "복사하지 못했습니다(직접 선택해 복사하세요)";
    setTimeout(() => { b.textContent = label; }, 1800);
  } }, label);
}

function itemCards(items, round, selectable, p, isHistory) {
  const out = [];
  for (const it of items) {
    const [label, cls] = VERDICT[it.verdict];
    const fid = it.finding.id;
    out.push(el("div", { class: "card" }, el("div", { class: "row" }, el("span", { class: "badge " + cls }, label), el("span", { class: "badge" }, `${it.finding.priority}순위 · ${it.finding.category}`), el("span", { class: "muted" }, it.finding.paragraphId)),
      el("p", {}, el("b", {}, "지적 "), it.finding.problem), el("p", { class: "muted" }, "선택한 방향: " + it.directionLabel),
      it.verdict === "skipped" ? null : [
        el("div", { class: "diffbox" }, el("b", {}, "변경 표시 "), el("span", { class: "muted" }, "삭제는 취소선, 추가는 굵은 초록. 같은 글이 긴 곳은 줄여 보여 줍니다."),
          el("div", { class: "difftext" }, ...renderDiff(it.diff))),
        el("div", { class: "row" }, copyBtn("고친 뒤 복사", () => it.after), copyBtn("고치기 전 복사", () => it.before)),
        fullText("고친 뒤 전문", it.after), fullText("고치기 전 전문", it.before),
        el("p", {}, el("b", {}, "재검수 "), it.reason),
        ...it.warnings.map((w) => el("div", { class: "alert warn" }, "확인 필요: " + w)),
        heldBlock(it, fid, selectable),
        ...newIssueBlocks(it, fid, selectable, p, isHistory)]));
  }
  return out;
}

function heldBlock(it, fid, selectable) {
  if (!it.held || !it.held.length) return null;
  return el("div", { class: "alert warn" }, el("b", {}, "근거를 확인하지 못해 보류한 부분"),
    el("ul", {}, ...it.held.map((h) => el("li", {}, el("b", {}, "확인하지 못해 보류한 내용: "), h.claim, el("br"), el("b", {}, "필요한 추가 자료: "), h.needed || "(구체적으로 정하지 못함)"))),
    selectable ? [el("label", { class: "field" }, "추가 자료 붙여넣기 (원문 발췌·수치·메모. 붙여 넣은 자료에 있는 내용만 근거로 씁니다)"),
      el("textarea", { rows: "4", value: ui.mat[fid] || "", placeholder: "예: 통계청 보도자료의 해당 문장을 그대로 붙여 넣기",
        oninput: (e) => { ui.mat[fid] = e.target.value; syncMat(); } }),
      el("div", { class: "counter", id: "cnt_" + fid }, counterText(ui.mat[fid] || ""))] : null);
}

const MAT_LIMIT = 8000;  // 서버가 항목당 받는 최대 글자 수(app/main.py의 materials와 같다)
function counterText(text) {
  const n = text.length;
  return n > MAT_LIMIT ? `${n.toLocaleString()} / ${MAT_LIMIT.toLocaleString()}자 — ${(n - MAT_LIMIT).toLocaleString()}자를 줄여야 보낼 수 있습니다` : `${n.toLocaleString()} / ${MAT_LIMIT.toLocaleString()}자`;
}

function syncMat() {
  for (const [fid, text] of Object.entries(ui.mat)) {  // 글자 수 표시: 한도를 넘으면 빨갛게
    const c = document.getElementById("cnt_" + fid);
    if (c) { c.textContent = counterText(text || ""); c.classList.toggle("over", (text || "").length > MAT_LIMIT); }
  }
  const b = document.getElementById("matbtn");
  const over = Object.values(ui.mat).some((v) => v && v.length > MAT_LIMIT);
  if (b) {
    b.disabled = ui.busy || over || !Object.values(ui.mat).some((v) => v && v.trim());
    b.title = over ? "붙여 넣은 자료가 8,000자를 넘는 항목이 있습니다. 줄인 뒤 다시 눌러 주세요." : "";
  }
}

function newIssueBlocks(it, fid, selectable, p, isHistory) {
  const high = it.newIssues.filter((x) => x.severity !== "low");
  const low = it.newIssues.filter((x) => x.severity === "low");
  const out = high.map((x) => {
    const ref = `${fid}.${x.id}`;
    const can = selectable && x.directions;
    return el("div", { class: "alert " + SEV[x.severity][1] },
      el("b", {}, `새로 생긴 문제(중요도 ${SEV[x.severity][0]}) `), x.text,
      !selectable && x.directions && p && p.stage === "done" && p.round >= p.maxRounds && !isHistory ? el("div", { class: "muted" }, "마지막 라운드라 더 이을 수 없습니다. 리포트의 '남은 문제'를 보고 직접 고치세요.") : null,
      can ? el("label", { class: "opt", style: "margin-top:8px" }, el("input", { type: "checkbox", checked: ui.nextSel.has(ref),
        onchange: (e) => { e.target.checked ? ui.nextSel.add(ref) : ui.nextSel.delete(ref); draw(); } }), " 이 문제도 고치기 (다음 라운드에서 수정 방향을 고릅니다)") : null);
  });
  if (low.length) out.push(el("details", {}, el("summary", {}, `참고: 중요도 낮은 새 문제 ${low.length}개`), ...low.map((x) => el("div", { class: "muted" }, "· " + x.text))));
  return out;
}

function viewResult(p) {
  const r = p.result;
  const n = (k) => r.items.filter((i) => i.verdict === k).length;
  const rem = r.remaining;
  const canNext = p.round < p.maxRounds && r.canContinue;
  const selectableCount = r.items.reduce((a, it) => a + it.newIssues.filter((x) => x.severity !== "low" && x.directions).length, 0);
  const out = [el("h2", {}, `라운드 ${p.round} 수정과 재검수 결과`),
    r.canContinue ? null : el("div", { class: "alert warn" }, "이 결과는 이전 버전의 앱이 만들어서 이어서 고칠 수 없습니다(새 문제 이어 고치기·자료 붙여 넣기가 없습니다). 같은 글로 새 검수를 시작하면 보류한 내용과 필요한 추가 자료가 구체적으로 나옵니다."),
    el("div", { class: "alert " + (rem.usable ? "ok" : "warn"), },
      el("b", {}, rem.usable ? "쓸 만한 상태입니다 " : "아직 손볼 곳이 남아 있습니다 "),
      `새 문제 — 높음 ${rem.newIssues.high} · 보통 ${rem.newIssues.medium} · 낮음 ${rem.newIssues.low} / 미해결 ${rem.unresolved} · 일부 해결 ${rem.partial} · 자료 부족 ${rem.insufficient} · 보류한 내용 ${rem.held || 0}`,
      el("div", { class: "muted" }, rem.usable ? "중요도 높은 새 문제, 미해결, 자료 부족, 보류한 내용이 없습니다. 보통·낮은 새 문제와 일부 해결은 직접 판단하세요." : "아래 새 문제를 이어서 고치거나 리포트를 보고 직접 고치세요.")),
    el("p", {}, `해결 ${n("resolved")} · 일부 해결 ${n("partial")} · 미해결 ${n("unresolved")} · 자료 부족 ${n("insufficient")} · 건너뜀 ${n("skipped")}`),
    el("div", { class: "row" }, ...p.outputs.map((name) => el("a", { href: `/api/projects/${p.projectId}/outputs/${name}`, download: name }, el("button", { class: "primary" }, name === "final.md" ? "수정본 내려받기(final.md)" : "검수 리포트 내려받기(review_report.md)"))),
      copyBtn("수정본 전체 복사", async () => (await fetch(`/api/projects/${p.projectId}/outputs/final.md`)).text())),
    r.checks.untouchedChanged.length ? el("div", { class: "alert err" }, "고르지 않은 문단이 바뀌었습니다: " + r.checks.untouchedChanged.join(", "))
      : el("p", { class: "muted" }, `기계 검사: 고르지 않은 문단은 모두 그대로입니다. 수정한 문단: ${r.checks.changed.join(", ") || "없음"}`)];
  if (r.gaps.length) {
    out.push(el("div", { class: "alert warn" }, el("b", {}, `근거를 확인하지 못해 보류한 내용 ${r.gaps.length}개와 필요한 추가 자료`),
      el("ol", { class: "heldlist" }, ...r.gaps.map((g) => {
        const m = g.match(/^(P\d+): ([\s\S]*?) \(필요한 자료: ([\s\S]*)\)$/);  // "P3: 내용 (필요한 자료: …)"
        return m ? el("li", {}, el("span", { class: "badge" }, m[1]), " ", el("b", {}, "확인하지 못한 내용 "), m[2], el("div", {}, el("b", {}, "필요한 추가 자료 "), m[3]))
          : el("li", {}, g);
      }))));
  }
  const heldRefs = r.items.filter((it) => it.held && it.held.length).map((it) => it.finding.id);
  if (heldRefs.length) {
    out.push(el("div", { class: "card row" }, canNext
      ? [el("button", { id: "matbtn", class: "primary", disabled: !Object.values(ui.mat).some((v) => v && v.trim()), onclick: () => act(async () => {
          const materials = Object.fromEntries(Object.entries(ui.mat).filter(([k, v]) => heldRefs.includes(k) && v && v.trim()));
          await api("POST", `/api/projects/${p.projectId}/materials`, { version: p.version, materials }); ui.mat = {}; await refresh(true); }) },
        `붙여 넣은 자료로 보류 항목 다시 고치기 (라운드 ${p.round + 1}/${p.maxRounds})`),
        el("span", { class: "muted" }, "아래 보류 항목의 칸에 필요한 자료를 붙여 넣고 누르세요. 자료가 없으면 보류 표시를 그대로 두고 직접 보완하세요.")]
      : el("span", { class: "muted" }, r.canContinue ? "마지막 라운드라 더 이을 수 없습니다. 보류한 부분은 필요한 자료를 직접 구해 보완하세요." : "이 결과는 이어서 고칠 수 없습니다. 필요한 자료를 직접 구하거나 새 검수를 시작하세요.")));
  }
  if (selectableCount) {
    const sel = [...ui.nextSel].filter((ref) => r.items.some((it) => it.newIssues.some((x) => `${it.finding.id}.${x.id}` === ref)));
    out.push(el("div", { class: "card row" }, canNext
      ? [el("button", { class: "primary", disabled: ui.busy || !sel.length, onclick: () => act(async () => {
          await api("POST", `/api/projects/${p.projectId}/next-round`, { version: p.version, issues: sel }); ui.nextSel = new Set(); await refresh(true); }) },
        `선택한 새 문제 ${sel.length}개 고치기 (라운드 ${p.round + 1}/${p.maxRounds})`),
        !sel.length ? el("span", { class: "muted" }, "아래 새 문제의 체크 상자를 선택하세요.") : null]
      : el("span", { class: "muted" }, `라운드는 최대 ${p.maxRounds}번까지입니다. 남은 문제는 리포트를 보고 직접 고치거나 새 검수로 시작하세요.`)));
  }
  out.push(...itemCards(r.items, p.round, canNext, p));  // 마지막 라운드에서는 '이 문제도 고치기' 체크 상자를 보이지 않는다
  if (r.sources.length) out.push(el("div", { class: "card" }, el("h3", {}, "참고 자료"), el("ul", {}, ...r.sources.map((s) => el("li", {}, `[${s.n}] `, el("a", { href: s.url, target: "_blank", rel: "noopener noreferrer" }, s.title || s.url))))));
  for (const h of p.history.slice().reverse()) {
    out.push(el("details", { class: "card" }, el("summary", {}, `이전 라운드 ${h.round}의 결과 보기`), ...itemCards(h.result.items, h.round, false, p, true)));
  }
  return out;
}

// --- 라우팅 ---
function route() {
  const m = location.hash.match(/^#\/p\/([\w-]+)$/);
  ui.id = m ? m[1] : null; ui.p = null; ui.error = null; ui.sig = ""; ui.list = null;
  draw();
  if (ui.id) refresh(true).then(schedulePoll); else schedulePoll();
}
window.addEventListener("hashchange", route);
route();
api("GET", "/api/health").then((h) => { document.getElementById("engine").textContent = "엔진: Claude CLI · " + h.settings.model + "/" + h.settings.effort; }).catch(() => {});
