"use strict";
// 단일 페이지 화면. 서버 상태(GET /api/projects/{id})만 보고 그린다. 새로고침해도 같은 화면으로 돌아온다.
const root = document.getElementById("app");
const ui = {
  projectId: null, project: null, sig: "", list: null,
  sel: new Set(), selReady: false, saving: 0, saveNote: "", saveChain: Promise.resolve(),
  opt: new Set(), freeText: "", qId: null,
  topic: "", days: "7", defaultEngine: null, agyEnabled: true, sbFormat: null, sbHook: 0, sbInstruction: "",
  busy: false, error: null, offline: false, rev: {}, revBusy: null,
};
const ENGINE_NAME = { mock: "모의", "claude-cli": "Claude CLI" };
const FACT_STATUS = { confirmed: "확인됨", company_claim: "업체 주장", announced: "공개 예정", unverified: "미확인" };
const pending = {}; // 같은 동작을 다시 보낼 때 같은 requestId를 쓰기 위한 보관소
let pollTimer = null;

const STAGE = {
  created: "조사 전", researching: "후보 조사 중", candidates_ready: "후보 선택", selected: "후보 선택됨",
  deep_research: "심층 조사 중", waiting_for_user: "답변 대기", proposal_ready: "제안 확인",
  storyboarding: "스토리보드 작성 중", storyboard_ready: "스토리보드 승인", producing: "이미지 제작 중", review: "이미지 검수",
};
const ROLE = { cover: "표지", body: "본문", summary: "마무리" };
const CARD_STATUS = { pending: "대기", generating: "만드는 중", ready: "완료", failed: "실패" };
const TOOL_LABEL = { agy: "agy(Antigravity)", mock: "모의 이미지", "claude-cli SVG + 브라우저": "Claude SVG + 브라우저 캡처",
  "claude-cli 도안 + Pillow": "Claude 도형 도안 + Pillow(SVG 대체)" };
const PROVIDER_LABEL = { agy: "agy", "claude-svg": "Claude SVG(브라우저 캡처)", "claude-scene": "Claude 도형 도안(Pillow)" };
const CHECK_LABEL = { file: "파일", format: "PNG 형식", size: "크기", margin: "위쪽 여백", textInImage: "이미지 속 글자" };
const CATEGORY = { model: "모델", product: "제품", research: "연구", devtool: "개발 도구", other: "기타" };
const FORMAT = { list: "목록형", story: "이야기형" };
const DAYS_LABEL = { 7: "최근 7일", 30: "최근 30일" };

function el(tag, attrs, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v == null) continue;
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (k === "checked" || k === "disabled" || k === "value") n[k] = v;
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    n.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return n;
}
const uuid = () => (crypto.randomUUID ? crypto.randomUUID() :
  "r" + Array.from(crypto.getRandomValues(new Uint8Array(12)), (b) => b.toString(16).padStart(2, "0")).join(""));
const fmtTime = (iso) => (iso ? iso.replace("T", " ").slice(0, 16) : "");
function safeLink(url, text) {
  try {
    const u = new URL(url);
    if (u.protocol === "https:" || u.protocol === "http:") {
      return el("a", { href: u.href, target: "_blank", rel: "noopener noreferrer" }, text || u.href);
    }
  } catch (_) { /* 잘못된 주소는 글자로만 보여 준다 */ }
  return el("span", {}, text || url);
}

// --- 서버 호출 ----------------------------------------------------------------
async function api(method, path, body) {
  let res;
  try {
    res = await fetch(path, {
      method, headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined, cache: "no-store",
    });
  } catch (_) {
    throw { network: true, message: "서버에 연결하지 못했습니다.", nextAction: "서버가 켜져 있는지 확인하세요. 자동으로 다시 시도합니다." };
  }
  let data = null;
  try { data = await res.json(); } catch (_) { /* 본문이 없을 수 있다 */ }
  if (!res.ok) {
    const e = (data && data.error) || {};
    throw { status: res.status, code: e.code, message: e.message || "요청이 실패했습니다.", nextAction: e.nextAction || "" };
  }
  return data;
}

// 변경 요청: 같은 동작·같은 내용이면 같은 requestId를 쓰고, 응답을 기다리는 동안 버튼을 잠근다.
async function act(name, path, body) {
  if (ui.busy) return null;
  const key = name + JSON.stringify(body);
  pending[key] = pending[key] || uuid();
  ui.busy = true; ui.error = null; draw();
  try {
    const out = await api("POST", path, { ...body, requestId: pending[key] });
    delete pending[key];
    return out;
  } catch (e) {
    if (!e.network) delete pending[key];
    ui.error = e;
    if (e.code === "stale_version" || e.code === "stale_question") { ui.error = { ...e, nextAction: "최신 상태로 새로 불러왔습니다. 내용을 확인한 뒤 다시 눌러 주세요." }; await refresh(true); }
    return null;
  } finally {
    ui.busy = false; draw();
  }
}

async function refresh(force) {
  if (!ui.projectId) return;
  if (ui.saving > 0 && !force) return;
  try {
    const p = await api("GET", `/api/projects/${ui.projectId}`);
    ui.offline = false;
    ui.project = p;
    if (!ui.selReady) { ui.sel = new Set(p.selection.candidateIds); ui.selReady = true; }
    if (!p.question || p.question.questionId !== ui.qId) {
      ui.qId = p.question ? p.question.questionId : null; ui.opt = new Set(); ui.freeText = "";
    }
    const r = p.currentRun;
    const cardSig = (p.cards || []).map((c) => c.status + c.currentVersion).join(",");
    const sig = [p.version, p.stage, r && r.status, r && r.step, r && r.message, cardSig].join("|");
    if (sig !== ui.sig || force) { ui.sig = sig; draw(); }
  } catch (e) {
    if (e.network) { ui.offline = true; draw(); }
    else if (e.status === 404) { ui.project = null; ui.error = e; draw(); }
  }
}

function schedulePoll() {
  clearTimeout(pollTimer);
  const run = ui.project && ui.project.currentRun;
  const active = run && (run.status === "queued" || run.status === "running");
  pollTimer = setTimeout(async () => { await refresh(); schedulePoll(); }, active ? 2000 : 5000);
}

// --- 화면 그리기 --------------------------------------------------------------
function draw() {
  const engine = ui.project ? ui.project.engine : ui.defaultEngine;
  document.getElementById("engine-badge").textContent = "엔진: " + (engine ? ENGINE_NAME[engine] || engine : "확인 중");
  // 모의 데이터 띠는 모의 엔진으로 만든 프로젝트(또는 새로 만들 프로젝트의 엔진이 모의일 때)에만 보인다.
  document.getElementById("mock-banner").hidden = !(ui.project ? ui.project.mock : engine === "mock");
  const kids = [];
  if (ui.offline) kids.push(el("div", { class: "alert error" }, "서버에 연결하지 못했습니다. 자동으로 다시 시도합니다."));
  if (ui.error) kids.push(errorBox(ui.error));
  if (!ui.projectId) kids.push(...viewStart());
  else if (!ui.project) kids.push(el("p", { class: "muted" }, ui.error ? "" : "불러오는 중…"));
  else kids.push(...viewProject(ui.project));
  root.replaceChildren(...kids);
}

function errorBox(e) {
  return el("div", { class: "alert error", role: "alert" },
    el("strong", {}, e.message || "오류가 발생했습니다."),
    e.nextAction ? el("div", {}, "다음 행동: " + e.nextAction) : null,
    e.code ? el("div", { class: "muted" }, "오류 코드: " + e.code) : null);
}

function viewStart() {
  const sendable = ui.topic.trim().length > 0 && !ui.busy;
  const out = [
    el("h1", {}, "어떤 주제로 카드뉴스를 만들까요?"),
    el("div", { class: "card" },
      el("label", { class: "field", for: "topic" }, "주제"),
      el("input", {
        type: "text", id: "topic", maxlength: "200", placeholder: "예: AI 최신소식", value: ui.topic,
        oninput: (ev) => { ui.topic = ev.target.value; const b = document.getElementById("go"); if (b) b.disabled = !ui.topic.trim() || ui.busy; },
        onkeydown: (ev) => { if (ev.key === "Enter" && ui.topic.trim()) startProject(); },
      }),
      el("label", { class: "field", for: "days" }, "조사 기간"),
      el("select", { id: "days", onchange: (ev) => { ui.days = ev.target.value; } },
        el("option", { value: "7", selected: ui.days === "7" }, "최근 7일 (기본)"),
        el("option", { value: "30", selected: ui.days === "30" }, "최근 30일")),
      el("p", { class: "muted" }, ui.defaultEngine === "mock"
        ? "조사 기준 시각과 기간은 조사를 시작하면 화면에 표시됩니다. 지금은 모의 엔진이라 모의 데이터로 흐름만 확인합니다."
        : "Claude CLI가 웹을 검색해 실제 자료를 조사합니다. 몇 분 걸릴 수 있습니다. 조사 기준 시각과 기간은 시작하면 화면에 표시됩니다."),
      el("button", { class: "primary", id: "go", disabled: !sendable, onclick: startProject }, "조사 시작")),
    el("h2", {}, "이전 프로젝트"),
  ];
  if (ui.list === null) out.push(el("p", { class: "muted" }, "불러오는 중…"));
  else if (!ui.list.length) out.push(el("p", { class: "muted" }, "아직 없습니다."));
  else {
    for (const p of ui.list) {
      out.push(el("div", { class: "card row between" },
        el("div", {}, el("a", { href: `#/p/${p.projectId}` }, p.topic),
          el("div", { class: "muted" }, `${STAGE[p.stage] || p.stage} · ${fmtTime(p.updatedAt)}`)),
        p.mock ? el("span", { class: "badge mock" }, "모의 데이터") : null));
    }
  }
  return out;
}

async function loadList() {
  try { ui.list = (await api("GET", "/api/projects")).projects; } catch (_) { ui.list = []; }
  if (!ui.projectId) draw();
}

async function startProject() {
  const topic = ui.topic.trim();
  if (!topic || ui.busy) return;
  const days = Number(ui.days);
  const created = await act("create", "/api/projects", { topic, period: { days } });
  if (!created) return;
  ui.topic = "";
  location.hash = `#/p/${created.projectId}`;
  await new Promise((r) => setTimeout(r, 0));
  const res = await act("research", `/api/projects/${created.projectId}/research`, { version: created.version, days });
  if (res) await refresh(true);
}

function header(p) {
  const w = p.researchWindow;
  return el("div", {},
    el("div", { class: "row" },
      el("h1", {}, p.topic),
      p.mock ? el("span", { class: "badge mock" }, "모의 데이터") : null,
      el("span", { class: "badge" }, STAGE[p.stage] || p.stage)),
    el("div", { class: "muted" }, `조사 기준 시각 ${fmtTime(w.basis)} · 조사 기간 ${w.from} ~ ${w.to} (${DAYS_LABEL[w.days] || w.days + "일"})`
      + (p.mock ? " · 실제 검색 결과 아님" : "")));
}

function runPanel(p) {
  const r = p.currentRun;
  if (!r) return [];
  if (r.status === "interrupted") {
    return [el("div", { class: "alert info" },
      el("strong", {}, "서버가 다시 시작되면서 작업이 중단되었습니다."),
      el("div", { class: "row", style: "margin-top:8px" },
        el("button", { class: "primary", disabled: ui.busy, onclick: () => resumeRun(r.runId, "continue") }, "이어서 진행"),
        el("button", { disabled: ui.busy, onclick: () => resumeRun(r.runId, "restart") }, "처음부터 다시")))];
  }
  if (r.status === "failed") {
    return [el("div", { class: "alert error", role: "alert" },
      el("strong", {}, r.error && r.error.message),
      r.error && r.error.nextAction ? el("div", {}, "다음 행동: " + r.error.nextAction) : null,
      r.error && r.error.code ? el("div", { class: "muted" }, "오류 코드: " + r.error.code) : null,
      el("div", { style: "margin-top:8px" }, el("button", { class: "primary", disabled: ui.busy, onclick: () => retry(p, r) }, "다시 시도")))];
  }
  if (r.status === "cancelled") return [el("div", { class: "alert info" }, "작업을 취소했습니다. 필요하면 다시 시작하세요.")];
  if (r.status === "queued" || r.status === "running") {
    return [el("div", { class: "card progress" }, el("div", { class: "spinner", "aria-hidden": "true" }),
      el("div", { style: "flex:1" }, el("div", {}, r.message || "진행 중"),
        el("div", { class: "muted" }, "단계: " + (r.step || "-") + " · 경과 ", el("span", { id: "elapsed", "data-start": r.startedAt }, ""))),
      el("button", { disabled: ui.busy, onclick: () => cancelRun(r.runId) }, "취소"))];
  }
  return [];
}

async function resumeRun(runId, mode) { const r = await act("resume" + mode, `/api/runs/${runId}/resume`, { mode }); if (r) await refresh(true); }
async function cancelRun(runId) {
  if (ui.busy) return;
  ui.busy = true; ui.error = null; draw();
  try { await api("POST", `/api/runs/${runId}/cancel`); } catch (e) { ui.error = e; }
  ui.busy = false; await refresh(true);
}
async function retry(p, r) {
  let res;
  if (r.kind === "research") res = await act("research", `/api/projects/${p.projectId}/research`, { version: p.version, days: p.researchWindow.days });
  else if (r.kind === "storyboard") res = await act("storyboard", `/api/projects/${p.projectId}/storyboard`, { format: currentFormat(p), hookIndex: ui.sbHook, version: p.version });
  else if (r.kind === "images") res = await act("cards-retry", `/api/projects/${p.projectId}/cards/retry`, { version: p.version });
  else res = await act("deep", `/api/projects/${p.projectId}/deep-research`, { version: p.version });
  if (res) await refresh(true);
}

function viewProject(p) {
  const out = [el("p", {}, el("a", { href: "#/" }, "← 처음으로")), header(p), ...runPanel(p)];
  const active = p.currentRun && ["queued", "running", "interrupted"].includes(p.currentRun.status);
  if (p.stage === "created" && !active) {
    const failed = p.currentRun && p.currentRun.status === "failed";
    if (!failed) out.push(el("div", { class: "card" }, el("p", {}, "아직 조사하지 않았습니다."),
      el("button", { class: "primary", disabled: ui.busy, onclick: () => retry(p, { kind: "research" }) }, "조사 시작")));
  }
  if (p.stage === "candidates_ready" || p.stage === "selected") out.push(...viewCandidates(p));
  if (p.stage === "waiting_for_user" && p.question) out.push(...viewQuestion(p));
  if (p.stage === "proposal_ready") out.push(...viewProposal(p), ...viewStoryboardStart(p));
  if (p.stage === "storyboarding") out.push(el("p", { class: "muted" }, "선택한 형식으로 5~8장 스토리보드를 짜는 중입니다. 장마다 메시지 하나, 본문, 이미지 역할, 출처를 정리합니다."));
  if (p.stage === "storyboard_ready") out.push(...viewStoryboard(p));
  if (p.stage === "producing" || p.stage === "review") out.push(...viewCards(p));
  return out;
}

function viewCandidates(p) {
  const out = [el("div", { class: "row between" }, el("h2", {}, `후보 ${p.candidates.length}개`),
    el("button", { disabled: ui.busy, onclick: () => rerun(p) }, "다시 조사"))];
  if (!p.candidates.length) out.push(el("p", { class: "muted" }, "기간 안에서 출처를 확인한 후보가 없습니다."));
  if (p.candidatesNote) {
    out.push(el("div", { class: "alert info" }, p.candidatesNote,
      p.researchWindow.days === 7 ? el("div", { style: "margin-top:8px" },
        el("button", { disabled: ui.busy, onclick: () => widen(p) }, "30일로 넓히기")) : null));
  }
  out.push(el("p", { class: "muted" }, "1~3개를 고르세요. 고른 내용은 서버에 바로 저장되어 새로고침해도 유지됩니다. 고르기 전에는 제작을 시작하지 않습니다."));
  for (const c of p.candidates) {
    const on = ui.sel.has(c.id);
    out.push(el("label", { class: "card cand" + (on ? " sel" : ""), for: "chk_" + c.id },
      el("input", { type: "checkbox", id: "chk_" + c.id, checked: on, onchange: (ev) => toggle(c.id, ev.target.checked, ev.target) }),
      el("div", {},
        el("h3", {}, c.title),
        el("div", {}, c.summary),
        el("div", { class: "meta" },
          el("span", {}, "분류 " + (CATEGORY[c.category] || c.category)),
          el("span", {}, "게시일 " + (c.publishedAt || "미확인")),
          el("span", {}, "사건일 " + (c.eventDate || "미확인")),
          c.groupedCount > 1 ? el("span", {}, `같은 발표 기사 ${c.groupedCount}건 묶음`) : null),
        el("div", { class: "kv" }, el("b", {}, "선택할 가치 "), c.whySelect),
        el("div", { class: "kv" }, el("b", {}, "불확실성 "), c.uncertainty),
        el("div", { class: "kv" }, el("b", {}, "원문 "), ...c.sources.map((s) => safeLink(s.url, s.title))))));
  }
  const n = ui.sel.size;
  out.push(el("div", { class: "stickybar row between" },
    el("span", {}, `선택 ${n}/3 `, el("span", { class: "muted" }, ui.saveNote)),
    el("button", { class: "primary", disabled: n < 1 || n > 3 || ui.busy || ui.saving > 0, onclick: () => nextStep(p) }, "다음 단계: 심층 조사")));
  return out;
}

async function rerun(p) {
  const r = await act("research", `/api/projects/${p.projectId}/research`, { version: p.version, days: p.researchWindow.days });
  if (r) { ui.selReady = false; await refresh(true); }
}

async function widen(p) {
  const r = await act("research", `/api/projects/${p.projectId}/research`, { version: p.version, days: 30 });
  if (r) { ui.selReady = false; await refresh(true); }
}

function toggle(id, checked, input) {
  if (checked && ui.sel.size >= 3) {
    input.checked = false;
    ui.error = { message: "후보는 최대 3개까지 고를 수 있습니다.", nextAction: "다른 후보를 해제한 뒤 고르세요." };
    draw(); return;
  }
  ui.error = null;
  if (checked) ui.sel.add(id); else ui.sel.delete(id);
  ui.saving++; ui.saveNote = "저장 중…"; draw();
  const ids = [...ui.sel];
  ui.saveChain = ui.saveChain.then(() => saveSelection(ids));
}

async function saveSelection(ids) {
  try {
    const res = await api("POST", `/api/projects/${ui.projectId}/selection`,
      { candidateIds: ids, version: ui.project.version, requestId: uuid() });
    ui.project.version = res.version; ui.project.stage = res.stage;
    ui.project.selection = { candidateIds: res.selection.candidateIds };
    ui.saveNote = "저장됨";
  } catch (e) {
    ui.error = e; ui.saveNote = "저장 실패";
    if (e.code === "stale_version") { ui.error = { ...e, nextAction: "최신 상태로 새로 불러옵니다. 다시 골라 주세요." }; ui.selReady = false; }
  } finally {
    ui.saving--;
    if (ui.saving === 0) await refresh(true);
  }
}

async function nextStep(p) {
  await ui.saveChain;
  const res = await act("deep", `/api/projects/${p.projectId}/deep-research`, { version: ui.project.version });
  if (res) await refresh(true);
}

function viewQuestion(p) {
  const q = p.question;
  const rec = q.recommendation;
  const canSend = (ui.opt.size > 0 || ui.freeText.trim()) && !ui.busy;
  const out = [el("h2", {}, q.question)];
  const box = el("div", { class: "card" });
  for (const o of q.options) {
    const on = ui.opt.has(o.id);
    box.append(el("label", { class: "opt" + (on ? " sel" : ""), for: "opt_" + o.id },
      el("input", {
        type: q.multiSelect ? "checkbox" : "radio", name: "answer", id: "opt_" + o.id, checked: on,
        onchange: (ev) => {
          if (q.multiSelect) { ev.target.checked ? ui.opt.add(o.id) : ui.opt.delete(o.id); }
          else { ui.opt = new Set([o.id]); }
          draw();
        },
      }),
      o.label, rec && rec.optionId === o.id ? el("span", { class: "badge rec" }, " 추천") : null,
      el("small", {}, o.description)));
  }
  if (q.allowFreeText) {
    box.append(el("label", { class: "field", for: "free" }, "직접 입력 (선택)"),
      el("textarea", { id: "free", maxlength: "500", placeholder: "원하는 독자를 직접 적어도 됩니다.", value: ui.freeText,
        oninput: (ev) => { ui.freeText = ev.target.value; const b = document.getElementById("send"); if (b) b.disabled = !(ui.opt.size > 0 || ui.freeText.trim()) || ui.busy; } }));
  }
  if (rec) {
    box.append(el("p", { class: "muted" }, "추천 이유: " + rec.reason + " 추천은 참고용이며, 직접 고르기 전에는 답변으로 처리되지 않습니다."));
  }
  box.append(el("button", { class: "primary", id: "send", disabled: !canSend, onclick: () => sendAnswer(p) }, "답변 보내기"));
  out.push(box);
  return out;
}

async function sendAnswer(p) {
  const q = p.question;
  const free = ui.freeText.trim();
  const res = await act("answer", `/api/projects/${p.projectId}/answers`, {
    questionId: q.questionId, version: q.version, answer: { optionIds: [...ui.opt], freeText: free || null },
  });
  if (res) await refresh(true);
}

function list(items) { return el("ul", { class: "plain" }, items.map((t) => el("li", {}, t))); }

function viewProposal(p) {
  const pr = p.proposal, n = p.researchNotes;
  const out = [el("h2", {}, "조사 기록과 제안")];
  if (p.currentRun && p.currentRun.message && !p.mock) out.push(el("p", { class: "muted" }, p.currentRun.message));
  if (n) {
    out.push(el("div", { class: "card" }, el("h3", {}, "조사 기록"),
      el("b", {}, "사실"),
      el("ul", { class: "plain" }, n.facts.map((f) => el("li", {}, f.text, " ",
        el("span", { class: "badge" + (f.status === "confirmed" ? " rec" : "") }, FACT_STATUS[f.status] || f.status),
        f.sourceUrl ? [" ", safeLink(f.sourceUrl, "출처")] : null))),
      el("b", {}, "출처"),
      el("ul", { class: "plain" }, n.sources.map((s) => el("li", {}, safeLink(s.url, s.title)))),
      el("b", {}, "한계"), list(n.limits),
      el("b", {}, "상충하는 내용"), n.conflicts.length ? list(n.conflicts) : el("p", { class: "muted" }, "없음")));
  }
  if (pr) {
    out.push(el("div", { class: "card" }, el("h3", {}, "편집 제안"),
      el("div", { class: "kv" }, el("b", {}, "독자 "), pr.audience),
      el("div", { class: "kv" }, el("b", {}, "독자에게 유용한 이유 "), pr.whyUseful),
      el("div", { class: "kv" }, el("b", {}, "추천 형식 "), `${FORMAT[pr.formatRecommendation.format]} — ${pr.formatRecommendation.reason}`),
      el("h3", {}, "두 형식 비교"),
      ...pr.formatComparison.map((f) => el("div", { class: "kv" }, el("b", {}, FORMAT[f.format] + " "),
        `${f.fit} · 카드 약 ${f.cards}장 · 근거 확보: ${f.evidenceDifficulty}`)),
      el("div", { class: "kv" }, el("b", {}, "제목의 '최신' "),
        pr.freshnessBasis.titleMayUseLatest
          ? `사용 가능 (조사 기준일 ${pr.freshnessBasis.researchBasis}, 게시일 ${pr.freshnessBasis.publishedAt.join(", ")})`
          : "사용하지 않음 (게시일 미확인 또는 조사 기간 밖)"),
      el("h3", {}, "후킹 문구 후보"), list(pr.hooks)));
  }
  return out;
}

function currentFormat(p) {
  return ui.sbFormat || (p.storyboard && p.storyboard.format) || (p.proposal && p.proposal.formatRecommendation.format) || "story";
}

function viewStoryboardStart(p) {
  const pr = p.proposal;
  const fmt = currentFormat(p);
  const out = [el("h2", {}, "스토리보드 만들기")];
  const box = el("div", { class: "card" }, el("p", { class: "muted" }, "카드 형식을 고르면 5~8장 스토리보드를 만듭니다. 장마다 메시지는 하나만 담고, 승인하기 전에는 이미지를 만들지 않습니다."));
  for (const f of pr.formatComparison) {
    const on = fmt === f.format;
    box.append(el("label", { class: "opt" + (on ? " sel" : ""), for: "fmt_" + f.format },
      el("input", { type: "radio", name: "fmt", id: "fmt_" + f.format, checked: on, onchange: () => { ui.sbFormat = f.format; draw(); } }),
      FORMAT[f.format], pr.formatRecommendation.format === f.format ? el("span", { class: "badge rec" }, " 추천") : null,
      el("small", {}, `${f.fit} · 카드 약 ${f.cards}장`)));
  }
  box.append(el("p", { class: "kv" }, el("b", {}, "표지 후킹 문구")));
  pr.hooks.forEach((h, i) => box.append(el("label", { class: "opt" + (ui.sbHook === i ? " sel" : ""), for: "hook_" + i },
    el("input", { type: "radio", name: "hook", id: "hook_" + i, checked: ui.sbHook === i, onchange: () => { ui.sbHook = i; draw(); } }), h)));
  box.append(el("button", { class: "primary", disabled: ui.busy, onclick: () => makeStoryboard(p, fmt) }, `${FORMAT[fmt]}으로 스토리보드 만들기`));
  out.push(box);
  return out;
}

async function makeStoryboard(p, fmt) {
  const res = await act("storyboard", `/api/projects/${p.projectId}/storyboard`, { format: fmt, hookIndex: ui.sbHook, version: p.version });
  if (res) await refresh(true);
}

function viewStoryboard(p) {
  const sb = p.storyboard;
  const out = [el("h2", {}, `스토리보드 ${sb.cards.length}장 (${FORMAT[sb.format]}, v${sb.version})`)];
  const style = el("div", { class: "card" }, el("h3", {}, "공통 그림체"),
    el("div", { class: "row" }, ...sb.style.palette.map((c) => el("span", { class: "swatch", style: `background:${c}`, title: c }))),
    el("div", { class: "kv" }, el("b", {}, "그림체 "), sb.style.artStyle), el("div", { class: "kv" }, el("b", {}, "분위기 "), sb.style.mood),
    el("div", { class: "muted" }, "모든 카드 이미지가 이 팔레트와 그림체를 공유합니다. 이미지 안에는 글자를 넣지 않고 위쪽 30%는 제목 자리로 비워 둡니다."));
  out.push(style);
  if (sb.instruction) out.push(el("p", { class: "muted" }, "반영한 변경 요청: " + sb.instruction));
  for (const c of sb.cards) {
    out.push(el("div", { class: "card sbcard" },
      el("div", { class: "row" }, el("h3", {}, `${c.order}. ${c.title}`), el("span", { class: "badge" }, ROLE[c.role])),
      el("div", { class: "keyline" }, c.keyline),
      el("div", {}, c.body),
      el("div", { class: "kv" }, el("b", {}, "이미지 역할 "), c.imageRole),
      el("div", { class: "kv" }, el("b", {}, "그림 계획 "), c.imagePlan),
      el("div", { class: "kv" }, el("b", {}, "출처 "), ...c.evidence.flatMap((e) => [safeLink(e.url, e.note || e.url), " "])),
      c.unsupportedNumbers && c.unsupportedNumbers.length
        ? el("div", { class: "alert info" }, "자료에서 찾지 못한 숫자가 있습니다: " + c.unsupportedNumbers.join(", ") + " — 근거를 확인하세요.") : null));
  }
  const other = sb.format === "list" ? "story" : "list";
  out.push(el("div", { class: "card" }, el("h3", {}, "변경 요청"),
    el("textarea", { id: "sbins", maxlength: "500", placeholder: "예: 3장 제목을 더 짧게, 마지막 장은 다음 행동 중심으로", value: ui.sbInstruction,
      oninput: (ev) => { ui.sbInstruction = ev.target.value; const b = document.getElementById("sbreq"); if (b) b.disabled = !ui.sbInstruction.trim() || ui.busy; } }),
    el("div", { class: "row", style: "margin-top:8px" },
      el("button", { id: "sbreq", disabled: !ui.sbInstruction.trim() || ui.busy, onclick: () => reviseStoryboard(p) }, "변경 요청으로 다시 만들기"),
      el("button", { disabled: ui.busy, onclick: () => makeStoryboard(p, other) }, `${FORMAT[other]}으로 다시 만들기`))));
  out.push(el("div", { class: "stickybar" },
    el("div", { class: "muted", style: "margin-bottom:6px" }, "승인하면 이 스토리보드대로 카드마다 설명용 이미지를 만듭니다. " + (
      p.mock ? "모의 엔진이라 모의 이미지로 만듭니다."
        : ui.agyEnabled ? "agy를 먼저 시도하고(agy 사용 한도를 씁니다), 안 되면 Claude SVG(안 되면 도형 도안)로 만듭니다. 몇 분 걸릴 수 있습니다."
          : "agy는 꺼 두어서(--no-agy 또는 CARDNEWS_USE_AGY=0) 쓰지 않고, Claude가 SVG 그림을 그려 Edge로 이미지로 바꿉니다. Edge를 쓸 수 없거나 SVG 검증을 통과하지 못하면 도형 도안으로 대체합니다. 몇 분 걸릴 수 있습니다.")),
    el("button", { class: "primary", disabled: ui.busy, onclick: () => approveStoryboard(p) }, "이 스토리보드 승인하고 이미지 만들기")));
  return out;
}

async function reviseStoryboard(p) {
  const res = await act("revisions", `/api/projects/${p.projectId}/revisions`, { target: "storyboard", instruction: ui.sbInstruction.trim(), version: p.version });
  if (res) { ui.sbInstruction = ""; await refresh(true); }
}

async function approveStoryboard(p) {
  const res = await act("approve", `/api/projects/${p.projectId}/approve`, { stage: "storyboard", version: p.version });
  if (res) await refresh(true);
}

function checkBadge(key, val) {
  const cls = val === "pass" ? "badge rec" : val === "unchecked" ? "badge" : "badge mock";
  const txt = { pass: "통과", fail: "실패", warn: "주의", unchecked: "미확인" }[val] || val;
  return el("span", { class: cls }, `${CHECK_LABEL[key] || key}: ${txt}`);
}

function viewCards(p) {
  const cards = p.cards;
  const revising = p.currentRun && p.currentRun.kind === "card_revision" && p.stage === "producing";
  const out = [el("h2", {}, revising ? "카드 수정 중" : p.stage === "producing" ? "이미지 만드는 중" : "카드 이미지 검수")];
  const notReady = cards.filter((c) => c.status === "failed" || c.status === "pending").length;
  out.push(el("p", { class: "muted" }, "각 카드의 설명용 이미지입니다. 이미지 안에는 글자가 없고 위쪽은 제목 자리로 비워 두었습니다. 제목·본문 글자는 카드마다 [글자 올리기] 버튼으로 올립니다. 생성 이미지이며 실제 사건이나 제품 화면이 아닙니다."));
  if (p.stage === "review" && notReady) {
    out.push(el("div", { class: "row" }, el("button", { class: "primary", disabled: ui.busy, onclick: () => retryCards(p, null) }, `만들지 못한 카드 ${notReady}장 다시 만들기`)));
  }
  out.push(el("div", { class: "grid" }, ...cards.map((c) => cardView(p, c))));
  if (p.stage === "review") out.push(...finalPanel(p));
  return out;
}

async function reviseCard(p, c) {
  const text = (ui.rev[c.id] || "").trim();
  if (!text) return;
  const res = await act("revise" + c.id, `/api/projects/${p.projectId}/revisions`, { target: "cards", cardIds: [c.id], instruction: text, version: p.version });
  if (res) { ui.rev[c.id] = ""; await refresh(true); }
}

async function restoreVersion(p, c, v) {
  const res = await act("restore" + c.id + v.version, `/api/projects/${p.projectId}/cards/${c.id}/restore`, { imageVersion: v.version, version: p.version });
  if (res) await refresh(true);
}

async function approveFinal(p) {
  const res = await act("approve-final", `/api/projects/${p.projectId}/approve`, { stage: "final", version: p.version });
  if (res) await refresh(true);
}

async function makeExport(p) {
  const res = await act("export", `/api/projects/${p.projectId}/exports`, { version: p.version });
  if (res) await refresh(true);
}

const VSTATUS = { pass: "통과", fail: "실패", unchecked: "직접 확인 필요" };

function exportView(p, ex) {
  const base = `/api/projects/${p.projectId}/exports/${ex.exportId}/`;
  const box = el("div", { class: "card" }, el("h3", {}, `내보낸 파일 (${ex.exportId.replace("ex_", "")})`));
  const dl = (name, label) => el("a", { class: "btn", href: base + name, download: name.split("/").pop() }, label);
  box.append(el("div", { class: "row" }, dl("cardnews.zip", "전체 ZIP 내려받기"), dl("sources.md", "출처(sources.md)"), dl("image_usage.md", "이미지 사용 기록"),
    dl("verification.json", "확인 결과(json)"), dl("manifest.json", "manifest")));
  box.append(el("div", { class: "row" }, ...ex.files.filter((f) => f.name.startsWith("cards/")).map((f) => dl(f.name, f.name.replace("cards/", "")))));
  const v = ex.verification;
  if (v) {
    box.append(el("h3", {}, "내보낸 파일 기준 확인"),
      el("p", {}, `통과 ${v.summary.pass} · 직접 확인 필요 ${v.summary.unchecked} · 실패 ${v.summary.fail}`,
        el("span", { class: "badge " + (v.allPassed ? "rec" : "mock") }, v.allPassed ? "모두 통과" : "아직 통과 아님")),
      el("p", { class: "muted" }, v.note));
    for (const cat of ["누락", "글자 잘림", "출처", "날짜"]) {
      const list = v.items.filter((i) => i.category === cat);
      box.append(el("h4", {}, cat), el("ul", { class: "ts-checks" }, ...list.map((i) => el("li", {},
        el("span", { class: "badge " + (i.status === "pass" ? "rec" : i.status === "fail" ? "mock" : "") }, VSTATUS[i.status]), " ", i.message,
        i.userAction ? el("div", { class: "alert info" }, el("b", {}, "내가 확인할 것: "), i.userAction) : null))));
    }
  }
  return box;
}

function finalPanel(p) {
  const fa = p.finalApproval;
  const box = el("div", { class: "card" }, el("h3", {}, "최종 승인과 내려받기"));
  if (fa.stale) box.append(el("div", { class: "alert info" }, "카드를 고쳐서 최종 승인이 풀렸습니다. 다시 확인한 뒤 최종 승인을 눌러 주세요."));
  if (!fa.ready) {
    box.append(el("p", { class: "muted" }, "모든 카드에 글자를 올리고 검사를 통과해야 최종 승인할 수 있습니다. 아래 카드를 확인하세요."),
      el("ul", { class: "plain" }, ...fa.blockers.map((b) => el("li", {}, (b.order ? `카드 ${b.order}: ` : "") + b.reason))));
  } else if (!fa.approved) {
    box.append(el("p", {}, "모든 카드의 글자가 올라가 있고 검사를 통과했습니다. 휴대폰 미리보기로 확인했다면 최종 승인하세요."),
      el("button", { class: "primary", disabled: ui.busy, onclick: () => approveFinal(p) }, "최종 승인"));
  } else {
    box.append(el("p", {}, el("span", { class: "badge rec" }, "최종 승인됨"), ` ${fmtTime(fa.approvedAt)}`),
      el("button", { class: "primary", disabled: ui.busy, onclick: () => makeExport(p) }, "카드별 PNG·ZIP·출처 기록 내보내기"),
      el("p", { class: "muted" }, "내보내면 만들어진 파일을 기준으로 누락, 글자 잘림, 출처, 날짜를 자동으로 확인합니다. 확인하지 못한 항목은 통과로 처리하지 않고 내가 확인할 일로 안내합니다."));
  }
  const out = [box];
  if (fa.approved) for (const ex of p.exports || []) out.push(exportView(p, ex));
  return out;
}

async function retryCards(p, ids) {
  const res = await act("cards-retry" + (ids ? ids.join() : ""), `/api/projects/${p.projectId}/cards/retry`, { cardIds: ids, version: p.version });
  if (res) await refresh(true);
}

function cardView(p, c) {
  const box = el("div", { class: "card cardbox" },
    el("div", { class: "row between" }, el("h3", {}, `${c.order}. ${c.title || ""}`),
      el("span", { class: "badge" + (c.status === "ready" ? " rec" : c.status === "failed" ? " mock" : "") }, CARD_STATUS[c.status] || c.status)));
  if (c.imageUrl) {
    box.append(el("img", { src: c.imageUrl + "?v=" + c.currentVersion, alt: `카드 ${c.order} 설명 이미지(글자 없음)`, loading: "lazy", class: "cardimg" }));
    box.append(el("div", { class: "meta" },
      el("span", {}, "만든 방식 " + (TOOL_LABEL[c.tool] || c.tool) + (c.provenance && c.provenance.browser ? ` (${c.provenance.browser})` : "")), el("span", {}, `${c.width}×${c.height}`),
      el("span", {}, `${Math.round(c.bytes / 1024)}KB`), el("span", {}, `버전 v${c.currentVersion}` + (c.versions.length > 1 ? ` (이전 ${c.versions.length - 1}개 보관)` : ""))));
    box.append(el("div", { class: "row" }, ...Object.entries(c.checks).map(([k, v]) => checkBadge(k, v))));
    if (c.notes && c.notes.length) box.append(el("ul", { class: "plain muted" }, c.notes.map((n) => el("li", {}, n))));
    const att = (c.provenance && c.provenance.attempts) || [];
    const agyOff = att.find((a) => a.provider === "agy" && a.outcome === "disabled");
    if (agyOff) box.append(el("div", { class: "alert info" }, el("b", {}, "agy를 쓰지 않았습니다 "), "꺼 둔 설정이라 Claude로 바로 만들었습니다. 다시 켜려면 서버를 run.py --agy로 켜세요."));
    const failed = att.filter((a) => a.outcome === "failed" || a.outcome === "skipped");
    if (failed.length) box.append(el("div", { class: "alert info" }, el("b", {}, "대체 수단을 쓴 이유 "), ...failed.map((a) => el("div", {}, `${PROVIDER_LABEL[a.provider] || a.provider}: ${a.reason}`))));
  } else if (c.status === "generating") {
    box.append(el("div", { class: "progress" }, el("div", { class: "spinner" }), el("span", {}, "만드는 중…")));
  } else if (c.status === "pending") {
    box.append(el("p", { class: "muted" }, "순서를 기다리는 중입니다."));
  }
  if (c.status === "failed" && c.error) {
    box.append(el("div", { class: "alert error", role: "alert" }, el("strong", {}, c.error.message), el("div", {}, "다음 행동: " + c.error.nextAction)));
  }
  if (p.stage === "review" && (c.status === "failed" || c.status === "ready")) {
    box.append(el("button", { disabled: ui.busy, onclick: () => retryCards(p, [c.id]) }, c.status === "failed" ? "이 카드 다시 시도" : "이 카드 이미지 다시 만들기(새 버전)"));
  }
  if (p.stage === "review" && c.status === "ready") {
    const ts = c.typeset;
    box.append(el("div", { class: "row", style: "margin-top:8px" },
      el("button", { class: "primary", onclick: () => openTypeset(p, c) }, ts ? "글자 고치기·모바일 미리보기" : "글자 올리기·모바일 미리보기"),
      ts ? el("span", { class: "badge " + (ts.ok ? "rec" : "mock") }, ts.ok ? "글자 올림·검사 통과" : "글자 올림·고칠 곳 있음") : el("span", { class: "badge" }, "글자 아직 안 올림")));
    if (c.finalUrl) box.append(el("div", { class: "muted" }, el("a", { href: c.finalUrl + "?v=" + encodeURIComponent(ts.savedAt), target: "_blank", rel: "noopener" }, "글자를 올린 이미지 열기")));
    const ta = el("textarea", { rows: "2", maxlength: "500", placeholder: `예: ${c.order}번 카드의 본문이 길어. 핵심 조건을 유지하며 두 문장으로 줄여 줘 / 그림이 이상해, 배경을 더 단순하게`,
      value: ui.rev[c.id] || "", oninput: (ev) => { ui.rev[c.id] = ev.target.value; const b = document.getElementById("revbtn_" + c.id); if (b) b.disabled = !ev.target.value.trim() || ui.busy; } });
    box.append(el("div", { class: "revbox" }, el("b", {}, "이 카드만 고치기"), ta,
      el("button", { id: "revbtn_" + c.id, disabled: !(ui.rev[c.id] || "").trim() || ui.busy, onclick: () => reviseCard(p, c) }, "수정 요청 보내기"),
      el("div", { class: "muted" }, "글만 고치면 기존 그림을 그대로 쓰고, 그림이 이상하다고 하면 이 카드 그림만 새 버전으로 다시 만듭니다. 다른 카드는 바뀌지 않고, 고치면 최종 승인을 다시 받아야 합니다.")));
  }
  if (p.stage === "review" && c.status === "ready" && c.versions.length > 1) {
    box.append(el("details", {}, el("summary", {}, `이전 그림 ${c.versions.length - 1}개 — 되돌리기`),
      el("div", { class: "row" }, ...c.versions.map((v) => el("div", { class: "verbox" },
        el("img", { src: v.imageUrl, alt: `그림 v${v.version}`, loading: "lazy", class: "verimg" }),
        el("div", { class: "muted" }, `v${v.version} · ${TOOL_LABEL[v.tool] || v.tool}`, el("br", {}), fmtTime(v.createdAt)),
        v.current ? el("span", { class: "badge rec" }, "지금 그림")
          : el("button", { disabled: ui.busy, onclick: () => restoreVersion(p, c, v) }, "이 그림으로 되돌리기")))),
      el("div", { class: "muted" }, "되돌려도 Claude나 agy를 부르지 않습니다. 저장해 둔 글자를 그 그림 위에 다시 올리고, 최종 승인은 다시 받아야 합니다.")));
  }
  if (c.revisions && c.revisions.length) {
    box.append(el("details", {}, el("summary", {}, `수정 기록 ${c.revisions.length}건`), ...c.revisions.map((r) => el("div", { class: "muted" },
      `${fmtTime(r.at)} · ${r.kind === "text" ? "글만 고침(그림 그대로)" : r.kind === "restore" ? "이전 그림으로 되돌림" : "그림 다시 만듦"} · 「${r.instruction}」 → ${r.reason}`,
      ...(r.notes || []).map((n) => el("div", { class: "alert info" }, n))))));
  }
  return box;
}

// --- 글자 올리기·모바일 미리보기 -------------------------------------------------------
// 편집 화면은 #app 밖의 덮개로 그려서, 2초마다 화면이 다시 그려져도 입력 중인 글자가 사라지지 않는다.
const TS_STATUS = { pass: "통과", warn: "주의", fail: "고쳐야 함" };
const TS_LABEL = { font: "글꼴", titleFit: "제목 잘림", bodyFit: "본문 잘림", overlap: "그림과 겹침", titleContrast: "제목 대비", bodyContrast: "본문 대비", phone: "휴대폰 가독성" };

async function openTypeset(p, c) {
  document.getElementById("ts-overlay")?.remove();
  const base = `/api/projects/${p.projectId}/cards/${c.id}/typeset`;
  let st;
  try { st = await api("GET", base); } catch (e) { ui.error = e; draw(); return; }
  const lay = { ...st.layout };
  const state = { timer: null, seq: 0, dirty: false };
  const overlay = el("div", { id: "ts-overlay", class: "ts-overlay", role: "dialog", "aria-label": `카드 ${c.order} 글자 올리기` });
  const img = el("img", { class: "ts-img", alt: "휴대폰 미리보기" });
  const checks = el("ul", { class: "ts-checks" });
  const note = el("div", { class: "muted", id: "ts-note" }, "");
  const saveBtn = el("button", { class: "primary" }, "이 모습으로 저장");
  const num = (id) => Number(document.getElementById(id).value);

  function field(label, node) { return el("label", { class: "field" }, label, node); }
  const title = el("textarea", { id: "ts-title", maxlength: String(st.limits.title), rows: "2", oninput: change }, lay.title);
  const body = el("textarea", { id: "ts-body", maxlength: String(st.limits.body), rows: "5", oninput: change }, lay.body);
  const autoT = el("input", { type: "checkbox", id: "ts-autot", checked: lay.titleSize == null, onchange: change });
  const autoB = el("input", { type: "checkbox", id: "ts-autob", checked: lay.bodySize == null, onchange: change });
  const tsz = el("input", { type: "range", id: "ts-tsz", min: "40", max: "120", value: String(lay.titleSize || 72), oninput: change });
  const bsz = el("input", { type: "range", id: "ts-bsz", min: "28", max: "64", value: String(lay.bodySize || 40), oninput: change });
  const scr = el("input", { type: "range", id: "ts-scr", min: "0", max: "90", value: String(lay.scrim), oninput: change });
  const col = el("select", { id: "ts-col", onchange: change },
    ...[["auto", "자동(배경에 맞춤)"], ["dark", "어두운 글자"], ["light", "밝은 글자"]].map(([v, t]) => el("option", { value: v, selected: lay.color === v }, t)));
  const anc = el("select", { id: "ts-anc", onchange: change },
    ...[["bottom", "본문을 아래쪽에"], ["center", "본문을 가운데쪽에"]].map(([v, t]) => el("option", { value: v, selected: lay.bodyAnchor === v }, t)));

  function current() {
    return { title: title.value, body: body.value, titleSize: autoT.checked ? null : num("ts-tsz"), bodySize: autoB.checked ? null : num("ts-bsz"),
      color: col.value, scrim: num("ts-scr"), bodyAnchor: anc.value };
  }
  function change() {
    state.dirty = true; saveBtn.disabled = true;
    tsz.disabled = autoT.checked; bsz.disabled = autoB.checked;
    note.textContent = "미리보기를 만드는 중…";
    clearTimeout(state.timer);
    state.timer = setTimeout(preview, 350);
  }
  function showChecks(list) {
    checks.replaceChildren(...list.map((k) => el("li", {}, el("span", { class: "badge " + (k.status === "pass" ? "rec" : k.status === "fail" ? "mock" : "") }, TS_STATUS[k.status]),
      " ", el("b", {}, (TS_LABEL[k.key] || k.key) + " "), k.message)));
  }
  async function preview() {
    const mine = ++state.seq;
    try {
      const r = await api("POST", base + "/preview", { layout: current() });
      if (mine !== state.seq) return; // 더 최근 요청이 있으면 이 결과는 버린다
      img.src = r.imageUrl; showChecks(r.checks);
      note.textContent = r.ok ? "미리보기입니다. 아직 저장되지 않았습니다." : "고쳐야 할 곳이 있습니다. 저장은 할 수 있지만 내보내기 전에 고치는 것을 권합니다.";
      saveBtn.disabled = false;
    } catch (e) { if (mine === state.seq) { note.textContent = (e.message || "미리보기를 만들지 못했습니다.") + " " + (e.nextAction || ""); } }
  }
  saveBtn.onclick = async () => {
    saveBtn.disabled = true; note.textContent = "저장하는 중…";
    try {
      const r = await api("PUT", base, { layout: current() });
      img.src = r.imageUrl; showChecks(r.checks); state.dirty = false;
      note.textContent = `저장했습니다(${fmtTime(r.savedAt)}). 저장된 파일을 다시 열어 1080×1350 PNG인지 확인했습니다.`;
    } catch (e) { note.textContent = (e.message || "저장하지 못했습니다.") + " " + (e.nextAction || ""); }
    saveBtn.disabled = false;
  };
  const close = () => {
    if (state.dirty && !confirm("저장하지 않은 변경이 있습니다. 닫을까요?")) return;
    overlay.remove(); document.body.classList.remove("ts-open"); refresh(true);
  };
  overlay.append(el("div", { class: "ts-panel" },
    el("div", { class: "row between" }, el("h2", {}, `카드 ${c.order}. 글자 올리기`), el("button", { onclick: close }, "닫기")),
    el("p", { class: "muted" }, "그림 위에 제목과 본문을 별도 글자로 올립니다. 그림 파일은 바뀌지 않고, 글자를 올린 이미지는 따로 저장됩니다. 오른쪽은 휴대폰 폭(390px) 미리보기입니다."),
    el("div", { class: "ts-cols" },
      el("div", { class: "ts-form" },
        field("제목", title), field("본문", body),
        field("제목 크기", el("span", {}, tsz, el("label", { class: "inline" }, autoT, " 자동 맞춤"))),
        field("본문 크기", el("span", {}, bsz, el("label", { class: "inline" }, autoB, " 자동 맞춤"))),
        field("글자색", col), field("본문 위치", anc), field("본문 바탕 띠 농도", scr),
        el("h3", {}, "검사"), checks, note, el("div", { class: "row" }, saveBtn)),
      el("div", { class: "ts-phone" }, el("div", { class: "ts-frame" }, img)))));
  document.body.append(overlay); document.body.classList.add("ts-open");
  tsz.disabled = autoT.checked; bsz.disabled = autoB.checked;
  saveBtn.disabled = true; note.textContent = "미리보기를 만드는 중…";
  preview();
}

// 경과 시간: 서버 호출 없이 화면에서만 센다(오래 걸리는 실제 조사가 멈춘 것처럼 보이지 않게).
setInterval(() => {
  const n = document.getElementById("elapsed");
  if (!n) return;
  const secs = Math.max(0, Math.round((Date.now() - new Date(n.dataset.start).getTime()) / 1000));
  n.textContent = secs >= 60 ? `${Math.floor(secs / 60)}분 ${secs % 60}초` : `${secs}초`;
}, 1000);

async function loadEngine() {
  try {
    const h = await api("GET", "/api/health");
    ui.defaultEngine = h.engine;
    ui.agyEnabled = h.agyEnabled !== false; // 기본은 켜짐. 꺼 둔 경우(--no-agy)에만 false
  } catch (_) { /* 연결 안내는 draw가 한다 */ }
  draw();
}

// --- 라우팅 -------------------------------------------------------------------
function route() {
  const m = location.hash.match(/^#\/p\/([\w-]+)$/);
  const next = m ? m[1] : null;
  if (next === ui.projectId && ui.project) return;
  Object.assign(ui, { projectId: next, project: null, sig: "", sel: new Set(), selReady: false, saving: 0, saveNote: "",
    saveChain: Promise.resolve(), error: null, opt: new Set(), freeText: "", qId: null });
  draw();
  if (next) refresh(true).then(schedulePoll); else { loadList(); schedulePoll(); }
}
window.addEventListener("hashchange", route);
route();
loadEngine();
