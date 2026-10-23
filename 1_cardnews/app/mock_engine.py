"""모의 엔진. 공통 엔진 인터페이스(start / answer / resume / cancel / status)를 따른다.
실제 검색 없이 고정된 모의 데이터를 돌려주며, 화면에 모의 데이터로 표시된다."""
import json
import os
import threading
import time
from datetime import date

from . import claude_parse as cp
from . import db, images, mock_data, revise, runs
from .runs import Stop


class MockEngine:
    name = "mock"

    def __init__(self) -> None:
        self.delay = float(os.environ.get("CARDNEWS_MOCK_DELAY", "1.0"))

    # --- 공통 인터페이스 -------------------------------------------------
    def start(self, run_id: str) -> None:
        self._spawn(self._execute, run_id)

    def answer(self, run_id: str) -> None:
        self._spawn(self._finish, run_id)

    def resume(self, run_id: str, mode: str) -> None:
        with db.read() as c:
            run = c.execute("SELECT kind FROM runs WHERE id=?", (run_id,)).fetchone()
            answered = c.execute("SELECT 1 FROM questions WHERE run_id=? AND status='answered'", (run_id,)).fetchone()
        if mode == "continue" and run["kind"] == "deep_research" and answered:
            self.answer(run_id)
        else:
            self.start(run_id)

    def cancel(self, run_id: str) -> int:
        return 0  # 하위 프로세스가 없다. DB의 취소 상태가 기준이다.

    def shutdown(self) -> None:
        pass

    def health(self) -> dict:
        return {"claude": "unknown", "search": "unknown"}

    # --- 내부 -----------------------------------------------------------
    def _spawn(self, target, run_id: str) -> None:
        threading.Thread(target=self._guard, args=(target, run_id), daemon=True).start()

    def _guard(self, target, run_id: str) -> None:
        try:
            target(run_id)
        except Stop:
            return
        except Exception as exc:  # 알 수 없는 오류는 완료로 처리하지 않고 실패로 남긴다
            runs.fail(run_id, "engine_failed", f"모의 엔진 오류: {exc}", "다시 시도해 주세요.")

    def _progress(self, run_id: str, step: str, message: str) -> None:
        time.sleep(self.delay)
        runs.progress(run_id, step, message)

    def _execute(self, run_id: str) -> None:
        with db.read() as c:
            run = c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if run["kind"] == "research":
            self._research(run)
        elif run["kind"] == "storyboard":
            self._storyboard(run)
        elif run["kind"] == "images":
            images.run_cards(run["id"], [images.MockProvider()])
        elif run["kind"] == "card_revision":
            self._progress(run["id"], "revising", "카드 수정 요청을 해석하는 중 (모의)")
            revise.execute(run["id"], lambda card, ins: revise.validate_revision(revise.mock_revision(card, ins), card, ins, card["body"] + " " + card["title"]),
                           [images.MockProvider()])
        else:
            self._deep_research(run)

    def _research(self, run) -> None:
        run_id, pid = run["id"], run["project_id"]
        self._progress(run_id, "searching", "검색어를 만드는 중 (모의)")
        self._progress(run_id, "collecting", "후보를 수집하는 중 (모의)")
        self._progress(run_id, "grouping", "같은 발표를 묶는 중 (모의)")
        with db.read() as c:
            p = c.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
            attempts = c.execute("SELECT COUNT(*) FROM runs WHERE project_id=? AND kind='research'", (pid,)).fetchone()[0]
        if "실패" in p["topic"] and attempts == 1:
            runs.fail(run_id, "search_unavailable",
                      "검색 도구를 호출하지 못했습니다. (모의 실패 시나리오: 첫 조사만 실패합니다)", "잠시 뒤 다시 시도해 주세요.")
            return
        today = date.fromisoformat(p["window_basis"][:10])
        candidates, note = mock_data.build_candidates(p["topic"], p["window_days"], today)
        runs.write_candidates(run_id, candidates, note, "후보 조사를 마쳤습니다 (모의)")

    def _storyboard(self, run) -> None:
        run_id = run["id"]
        self._progress(run_id, "drafting", "스토리보드를 짜는 중 (모의)")
        ctx = runs.load_context(run_id)
        params = ctx["params"]
        proposal = json.loads(ctx["project"]["proposal_json"])
        raw = mock_data.build_storyboard(ctx["project"]["topic"], ctx["selected"], params["format"], params.get("hook") or proposal["hooks"][0])
        allowed = {cp.normalize_url(s["url"]) for c in ctx["selected"] for s in c["sources"]}
        fallback = [{"url": c["sources"][0]["url"], "note": c["title"][:60]} for c in ctx["selected"]]
        sb = cp.validate_storyboard(raw, allowed, fallback, "", params["format"])
        runs.write_storyboard(run_id, sb, f"스토리보드 {len(sb['cards'])}장을 만들었습니다 (모의)")

    def _deep_research(self, run) -> None:
        run_id = run["id"]
        self._progress(run_id, "reading", "선택한 원문을 읽는 중 (모의)")
        self._progress(run_id, "judging", "독자와 카드 형식을 판단하는 중 (모의)")
        runs.open_question(run_id, {
            "question": "어떤 독자에게 전달할까요?", "options": mock_data.AUDIENCE_OPTIONS,
            "multiSelect": False, "allowFreeText": True, "recommendation": mock_data.AUDIENCE_RECOMMENDATION,
        })

    def _finish(self, run_id: str) -> None:
        self._progress(run_id, "writing", "조사 기록과 제안을 정리하는 중 (모의)")
        ctx = runs.load_context(run_id)
        p = ctx["project"]
        with db.read() as c:
            q = c.execute("SELECT answer_json FROM questions WHERE run_id=? AND status='answered' ORDER BY rowid DESC LIMIT 1",
                          (run_id,)).fetchone()
        notes, proposal = mock_data.build_notes_and_proposal(
            p["topic"], ctx["selected"], json.loads(q["answer_json"]), p["window_days"], date.fromisoformat(p["window_basis"][:10]))
        runs.write_result(run_id, notes, proposal, "심층 조사와 제안을 마쳤습니다 (모의)")
