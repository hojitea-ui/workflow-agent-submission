"""2단계 API 테스트(모의 엔진). 실행: .venv\\Scripts\\python.exe -m unittest discover -s tests -v"""
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
import uuid

_TMP = tempfile.TemporaryDirectory()
os.environ["CARDNEWS_DATA_DIR"] = _TMP.name
os.environ["CARDNEWS_MOCK_DELAY"] = "0.02"
os.environ["CARDNEWS_ENGINE"] = "mock"
# agy는 기본 켜짐이므로, 어떤 시험도 실제 agy(사용 한도가 작음)에 닿지 않게 모든 시험의 기본 agy를 "즉시 실패하는 가짜"로 바꿔 둔다.
# agy를 쓰는 시험은 각자 가짜 agy(tests/fake_agy.py)로 다시 지정한다.
os.environ["CARDNEWS_AGY_CMD"] = json.dumps([sys.executable, "-c", "import sys; sys.exit(3)"])

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.main import ENGINES, app  # noqa: E402

engine = ENGINES["mock"]


def rid() -> str:
    return uuid.uuid4().hex


class Base(unittest.TestCase):
    def setUp(self):
        engine.delay = 0.02
        self.cm = TestClient(app, base_url="http://127.0.0.1")
        self.c = self.cm.__enter__()

    def tearDown(self):
        self.cm.__exit__(None, None, None)

    def create(self, topic="AI 최신소식", days=7):
        r = self.c.post("/api/projects", json={"topic": topic, "period": {"days": days}, "requestId": rid()})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def research(self, pid, version, days=None):
        body = {"version": version, "requestId": rid()}
        if days:
            body["days"] = days
        return self.c.post(f"/api/projects/{pid}/research", json=body)

    def state(self, pid):
        r = self.c.get(f"/api/projects/{pid}")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def wait(self, pid, pred, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            s = self.state(pid)
            if pred(s):
                return s
            time.sleep(0.03)
        self.fail(f"시간 안에 조건을 만족하지 못함: {self.state(pid)['stage']} {self.state(pid)['currentRun']}")

    def to_candidates(self, topic="AI 최신소식", days=7):
        p = self.create(topic, days)
        self.assertEqual(self.research(p["projectId"], p["version"]).status_code, 202)
        return self.wait(p["projectId"], lambda s: s["stage"] == "candidates_ready")

    def select(self, s, ids):
        return self.c.post(f"/api/projects/{s['projectId']}/selection",
                           json={"candidateIds": ids, "version": s["version"], "requestId": rid()})

    def to_question(self):
        s = self.to_candidates()
        r = self.select(s, ["cand_1", "cand_3"])
        self.assertEqual(r.status_code, 200, r.text)
        s = self.state(s["projectId"])
        r = self.c.post(f"/api/projects/{s['projectId']}/deep-research", json={"version": s["version"], "requestId": rid()})
        self.assertEqual(r.status_code, 202, r.text)
        return self.wait(s["projectId"], lambda x: x["stage"] == "waiting_for_user")

    def answer(self, s, option="general", free=None, request_id=None, version=None, qid=None):
        q = s["question"]
        return self.c.post(f"/api/projects/{s['projectId']}/answers", json={
            "questionId": qid or q["questionId"], "version": version if version is not None else q["version"],
            "answer": {"optionIds": [option] if option else [], "freeText": free}, "requestId": request_id or rid()})


class FlowTests(Base):
    def test_full_flow(self):
        s = self.to_candidates()
        self.assertTrue(s["mock"])
        self.assertEqual(s["engine"], "mock")
        self.assertEqual(s["researchWindow"]["days"], 7)
        self.assertEqual(len(s["candidates"]), 8)
        self.assertEqual([c["dateStatus"] for c in s["candidates"]].count("unconfirmed"), 1)
        self.assertTrue(all(c["sources"][0]["url"].startswith("https://example.com/") for c in s["candidates"]))
        self.assertEqual(self.select(s, ["cand_1", "cand_2"]).status_code, 200)
        s = self.state(s["projectId"])
        self.assertEqual(s["stage"], "selected")
        self.assertEqual(s["selection"]["candidateIds"], ["cand_1", "cand_2"])
        r = self.c.post(f"/api/projects/{s['projectId']}/deep-research", json={"version": s["version"], "requestId": rid()})
        self.assertEqual(r.status_code, 202)
        s = self.wait(s["projectId"], lambda x: x["stage"] == "waiting_for_user")
        self.assertEqual(s["currentRun"]["status"], "waiting_for_user")
        q = s["question"]
        self.assertEqual(q["question"], "어떤 독자에게 전달할까요?")
        self.assertEqual(q["status"], "open")
        self.assertTrue(q["allowFreeText"])
        self.assertEqual(q["recommendation"]["optionId"], "general")
        ans = self.answer(s, "developer")
        self.assertEqual(ans.status_code, 200, ans.text)
        self.assertTrue(ans.json()["accepted"])
        s = self.wait(s["projectId"], lambda x: x["stage"] == "proposal_ready")
        self.assertEqual(s["proposal"]["audience"], "개발자")
        self.assertEqual(s["proposal"]["format"], "list")
        self.assertIsNone(s["question"])
        self.assertEqual(len(s["proposal"]["hooks"]), 3)

    def test_free_text_answer(self):
        s = self.to_question()
        r = self.answer(s, option=None, free="신입 개발자 모임")
        self.assertEqual(r.status_code, 200, r.text)
        s = self.wait(s["projectId"], lambda x: x["stage"] == "proposal_ready")
        self.assertEqual(s["proposal"]["audience"], "신입 개발자 모임")

    def test_recommendation_is_not_an_answer(self):
        s = self.to_question()
        r = self.c.post(f"/api/projects/{s['projectId']}/answers", json={
            "questionId": s["question"]["questionId"], "version": s["question"]["version"],
            "answer": {"optionIds": [], "freeText": None}, "requestId": rid()})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"]["code"], "invalid_input")
        self.assertEqual(self.state(s["projectId"])["stage"], "waiting_for_user")

    def test_unknown_option_and_multi_rejected(self):
        s = self.to_question()
        self.assertEqual(self.answer(s, "nope").status_code, 400)
        r = self.c.post(f"/api/projects/{s['projectId']}/answers", json={
            "questionId": s["question"]["questionId"], "version": s["question"]["version"],
            "answer": {"optionIds": ["general", "developer"]}, "requestId": rid()})
        self.assertEqual(r.status_code, 400)

    def test_persistence_across_restart_keeps_question(self):
        s = self.to_question()
        self.cm.__exit__(None, None, None)  # 서버 재시작
        self.cm = TestClient(app, base_url="http://127.0.0.1")
        self.c = self.cm.__enter__()
        s2 = self.state(s["projectId"])
        self.assertEqual(s2["stage"], "waiting_for_user")
        self.assertEqual(s2["question"]["questionId"], s["question"]["questionId"])
        self.assertEqual(s2["currentRun"]["status"], "waiting_for_user")
        self.assertEqual(len(s2["candidates"]), 8)
        self.assertEqual(self.answer(s2, "general").status_code, 200)
        self.wait(s["projectId"], lambda x: x["stage"] == "proposal_ready")


class IdempotencyTests(Base):
    def test_create_replay_and_conflict(self):
        req = rid()
        body = {"topic": "같은 주제", "requestId": req}
        a = self.c.post("/api/projects", json=body)
        b = self.c.post("/api/projects", json=body)
        self.assertEqual(a.status_code, 201)
        self.assertEqual(a.json()["projectId"], b.json()["projectId"])
        self.assertEqual(b.headers.get("Idempotent-Replay"), "true")
        c = self.c.post("/api/projects", json={"topic": "다른 주제", "requestId": req})
        self.assertEqual(c.status_code, 409)
        self.assertEqual(c.json()["error"]["code"], "request_conflict")

    def test_double_answer_is_applied_once(self):
        s = self.to_question()
        req = rid()
        a = self.answer(s, "general", request_id=req)
        b = self.answer(s, "general", request_id=req)
        self.assertEqual(a.status_code, 200)
        self.assertEqual(b.status_code, 200)
        self.assertEqual(b.headers.get("Idempotent-Replay"), "true")
        self.assertEqual(a.json(), b.json())
        self.wait(s["projectId"], lambda x: x["stage"] == "proposal_ready")
        with db.read() as conn:
            n = conn.execute("SELECT COUNT(*) FROM runs WHERE project_id=?", (s["projectId"],)).fetchone()[0]
        self.assertEqual(n, 2)  # research + deep_research. 답변 두 번이 작업을 더 만들지 않는다
        # 다른 requestId로 같은 질문에 다시 답하면 오래된 질문으로 거절
        late = self.answer(s, "developer")
        self.assertEqual(late.status_code, 409)
        self.assertEqual(late.json()["error"]["code"], "stale_question")

    def test_same_request_id_other_input_conflicts(self):
        s = self.to_candidates()
        req = rid()
        body = {"candidateIds": ["cand_1"], "version": s["version"], "requestId": req}
        self.assertEqual(self.c.post(f"/api/projects/{s['projectId']}/selection", json=body).status_code, 200)
        body["candidateIds"] = ["cand_2"]
        r = self.c.post(f"/api/projects/{s['projectId']}/selection", json=body)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "request_conflict")

    def test_repeated_research_does_not_start_second_run(self):
        engine.delay = 0.3
        p = self.create()
        a = self.research(p["projectId"], p["version"])
        b = self.research(p["projectId"], p["version"])  # 다른 requestId, 진행 중
        self.assertEqual(a.status_code, 202)
        self.assertEqual(b.status_code, 202)
        self.assertEqual(a.json()["runId"], b.json()["runId"])
        with db.read() as conn:
            n = conn.execute("SELECT COUNT(*) FROM runs WHERE project_id=?", (p["projectId"],)).fetchone()[0]
        self.assertEqual(n, 1)
        self.wait(p["projectId"], lambda s: s["stage"] == "candidates_ready")


class ValidationTests(Base):
    def test_selection_rules(self):
        s = self.to_candidates()
        pid = s["projectId"]
        too_many = self.select(s, ["cand_1", "cand_2", "cand_3", "cand_4"])
        self.assertEqual(too_many.status_code, 400)
        self.assertEqual(self.select(s, ["cand_999"]).status_code, 400)
        self.assertEqual(self.select(s, ["cand_1", "cand_1"]).status_code, 400)
        stale = self.c.post(f"/api/projects/{pid}/selection",
                            json={"candidateIds": ["cand_1"], "version": s["version"] - 1, "requestId": rid()})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["error"]["code"], "stale_version")

    def test_empty_selection_blocks_deep_research(self):
        s = self.to_candidates()
        self.assertEqual(self.select(s, ["cand_1"]).status_code, 200)
        s = self.state(s["projectId"])
        self.assertEqual(self.select(s, []).status_code, 200)
        s = self.state(s["projectId"])
        self.assertEqual(s["stage"], "candidates_ready")
        r = self.c.post(f"/api/projects/{s['projectId']}/deep-research", json={"version": s["version"], "requestId": rid()})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "wrong_stage")

    def test_no_production_before_selection(self):
        p = self.create()
        r = self.c.post(f"/api/projects/{p['projectId']}/deep-research", json={"version": p["version"], "requestId": rid()})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "wrong_stage")
        r = self.c.post(f"/api/projects/{p['projectId']}/selection",
                        json={"candidateIds": ["cand_1"], "version": p["version"], "requestId": rid()})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "wrong_stage")

    def test_bad_inputs_and_error_shape(self):
        r = self.c.post("/api/projects", json={"topic": "   ", "requestId": rid()})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(set(r.json()["error"]), {"code", "message", "retryable", "nextAction", "detail"})
        r = self.c.post("/api/projects", json={"topic": "x" * 201, "requestId": rid()})
        self.assertEqual(r.status_code, 400)
        r = self.c.post("/api/projects", json={"topic": "a", "period": {"days": 5}, "requestId": rid()})
        self.assertEqual(r.status_code, 400)
        r = self.c.post("/api/projects", json={"topic": "a"})  # requestId 없음
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.c.get("/api/projects/prj_nope").status_code, 404)
        self.assertEqual(self.c.get("/api/projects/prj_nope").json()["error"]["code"], "not_found")

    def test_topic_is_data_not_command(self):
        topic = '"; rm -rf / & echo <script>alert(1)</script>'
        s = self.to_candidates(topic)
        self.assertEqual(s["topic"], topic)

    def test_foreign_host_rejected(self):
        r = self.c.get("/api/health", headers={"Host": "evil.example.com"})
        self.assertEqual(r.status_code, 400)


class IsolationTests(Base):
    def test_two_projects_do_not_mix(self):
        a = self.to_question()
        b = self.to_question()
        self.assertNotEqual(a["question"]["questionId"], b["question"]["questionId"])
        wrong = self.c.post(f"/api/projects/{b['projectId']}/answers", json={
            "questionId": a["question"]["questionId"], "version": a["question"]["version"],
            "answer": {"optionIds": ["general"]}, "requestId": rid()})
        self.assertEqual(wrong.status_code, 409)
        self.assertEqual(wrong.json()["error"]["code"], "stale_question")
        self.assertEqual(self.answer(a, "developer").status_code, 200)
        self.wait(a["projectId"], lambda x: x["stage"] == "proposal_ready")
        self.assertEqual(self.state(b["projectId"])["stage"], "waiting_for_user")


class ScenarioTests(Base):
    def test_few_results_offer_30_days(self):
        s = self.to_candidates("결과 부족 테스트")
        self.assertEqual(len(s["candidates"]), 3)
        self.assertIn("30일", s["candidatesNote"])
        r = self.research(s["projectId"], s["version"], days=30)
        self.assertEqual(r.status_code, 202, r.text)
        s = self.wait(s["projectId"], lambda x: x["stage"] == "candidates_ready" and len(x["candidates"]) > 3)
        self.assertEqual(len(s["candidates"]), 11)
        self.assertEqual(s["researchWindow"]["days"], 30)
        self.assertIsNone(s["candidatesNote"])

    def test_search_failure_then_retry(self):
        p = self.create("검색 실패 테스트")
        self.assertEqual(self.research(p["projectId"], p["version"]).status_code, 202)
        s = self.wait(p["projectId"], lambda x: x["currentRun"]["status"] == "failed")
        self.assertEqual(s["stage"], "created")
        self.assertEqual(s["currentRun"]["error"]["code"], "search_unavailable")
        self.assertTrue(s["currentRun"]["error"]["nextAction"])
        self.assertEqual(self.research(p["projectId"], s["version"]).status_code, 202)
        self.wait(p["projectId"], lambda x: x["stage"] == "candidates_ready")

    def test_cancel_stops_work(self):
        engine.delay = 0.3
        p = self.create()
        run = self.research(p["projectId"], p["version"]).json()["runId"]
        r = self.c.post(f"/api/runs/{run}/cancel")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "cancelled")
        self.assertEqual(self.c.post(f"/api/runs/{run}/cancel").json()["status"], "cancelled")
        time.sleep(1.5)  # 스레드가 남아 있다면 결과를 썼을 시간
        s = self.state(p["projectId"])
        self.assertEqual(s["stage"], "created")
        self.assertEqual(s["candidates"], [])
        self.assertEqual(s["currentRun"]["status"], "cancelled")

    def test_cancel_while_waiting_for_question(self):
        s = self.to_question()
        run = s["currentRun"]["runId"]
        self.assertEqual(self.c.post(f"/api/runs/{run}/cancel").status_code, 200)
        s2 = self.state(s["projectId"])
        self.assertEqual(s2["stage"], "selected")
        self.assertIsNone(s2["question"])
        self.assertEqual(self.answer(s, "general").status_code, 409)

    def test_cancel_finished_run_rejected(self):
        s = self.to_candidates()
        r = self.c.post(f"/api/runs/{s['currentRun']['runId']}/cancel")
        self.assertEqual(r.status_code, 409)

    def test_interrupted_run_is_recognized_and_resumable(self):
        engine.delay = 0.5
        p = self.create()
        run = self.research(p["projectId"], p["version"]).json()["runId"]
        self.cm.__exit__(None, None, None)  # 작업 도중 서버 종료 흉내
        self.cm = TestClient(app, base_url="http://127.0.0.1")
        self.c = self.cm.__enter__()
        s = self.state(p["projectId"])
        self.assertEqual(s["currentRun"]["status"], "interrupted")
        engine.delay = 0.02
        r = self.c.post(f"/api/runs/{run}/resume", json={"mode": "restart", "requestId": rid()})
        self.assertEqual(r.status_code, 202, r.text)
        self.wait(p["projectId"], lambda x: x["stage"] == "candidates_ready")
        again = self.c.post(f"/api/runs/{run}/resume", json={"mode": "restart", "requestId": rid()})
        self.assertEqual(again.status_code, 409)  # 이미 끝난 작업은 이어갈 수 없다

    def test_list_and_health(self):
        self.create("목록 테스트")
        names = [p["topic"] for p in self.c.get("/api/projects").json()["projects"]]
        self.assertIn("목록 테스트", names)
        h = self.c.get("/api/health").json()
        self.assertEqual(h["checks"], {"claude": "unknown", "search": "unknown", "agy": "unknown"})  # agy는 기본 켜짐(아직 쓰지 않아 unknown)
        self.assertTrue(h["mock"])


if __name__ == "__main__":
    unittest.main()
