"""Claude CLI 엔진 테스트. 실제 모델 대신 tests/fake_claude.py(가짜 claude)를 실행해서
명령줄·세션 이어가기·검색 실패·취소·시간 초과·형식 오류를 검사한다. 실제 모델 호출은 이 파일에 없다."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from test_flow import Base, rid  # noqa: E402  (test_flow가 임시 DATA_DIR와 환경 변수를 먼저 설정한다)

from app import db  # noqa: E402
from app.main import ENGINES  # noqa: E402

FAKE = str(Path(__file__).with_name("fake_claude.py"))
TOPIC = "Claude 연결 시험"


def pid_alive(pid: int) -> bool:
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class ClaudeBase(Base):
    def setUp(self):
        self.saved = {k: os.environ.get(k) for k in ("CARDNEWS_ENGINE", "CARDNEWS_CLAUDE_CMD", "FAKE_CLAUDE_LOG", "CARDNEWS_CLAUDE_TIMEOUT")}
        self.logfile = tempfile.NamedTemporaryFile(delete=False, suffix=".jsonl")
        self.logfile.close()
        os.environ["CARDNEWS_ENGINE"] = "claude-cli"
        os.environ["CARDNEWS_CLAUDE_CMD"] = json.dumps([sys.executable, FAKE])
        os.environ["FAKE_CLAUDE_LOG"] = self.logfile.name
        os.environ.pop("CARDNEWS_CLAUDE_TIMEOUT", None)
        self.engine = ENGINES["claude-cli"]
        self.engine.state.update({"auth": "unknown", "search": "unknown"})
        super().setUp()

    def tearDown(self):
        self.engine.shutdown()
        super().tearDown()
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        os.unlink(self.logfile.name)

    def calls(self):
        text = Path(self.logfile.name).read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def failed(self, pid):
        return self.wait(pid, lambda s: s["currentRun"] and s["currentRun"]["status"] == "failed")

    def research_start(self, topic):
        p = self.create(topic)
        self.assertEqual(self.research(p["projectId"], p["version"]).status_code, 202)
        return p["projectId"]

    def to_question(self, topic=TOPIC, ids=("cand_1", "cand_2")):
        pid = self.research_start(topic)
        s = self.wait(pid, lambda x: x["stage"] == "candidates_ready")
        self.assertEqual(self.select(s, list(ids)).status_code, 200)
        s = self.state(pid)
        r = self.c.post(f"/api/projects/{pid}/deep-research", json={"version": s["version"], "requestId": rid()})
        self.assertEqual(r.status_code, 202, r.text)
        return pid


class RealFlowTests(ClaudeBase):
    def test_research_question_resume_flow(self):
        pid = self.research_start(TOPIC)
        s = self.wait(pid, lambda x: x["stage"] == "candidates_ready")
        self.assertEqual(s["engine"], "claude-cli")
        self.assertFalse(s["mock"])
        # 지어낸 출처·기간 밖·중복은 걸러지고, 검색 결과에 있던 주소(www/utm 달라도)만 남는다
        self.assertEqual([c["title"] for c in s["candidates"]], ["정상 후보 A", "날짜 미확인 후보 B"])
        self.assertEqual([c["dateStatus"] for c in s["candidates"]], ["confirmed", "unconfirmed"])
        self.assertEqual(s["candidates"][0]["groupedCount"], 3)
        self.assertIn("2개를 제외", s["candidatesNote"])
        self.assertIn("후보가 2개뿐", s["candidatesNote"])
        self.assertIn("30일", s["candidatesNote"])
        # 첫 호출의 명령줄: 제한 모드, 웹 도구만, 모델·effort, 주제는 명령줄이 아니라 표준입력으로
        first = self.calls()[0]
        argv = first["argv"]
        for flag in ("-p", "--restricted", "--strict-mcp-config", "--verbose"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("--tools") + 1], "WebSearch,WebFetch")
        self.assertEqual(argv[argv.index("--allowedTools") + 1], "WebSearch,WebFetch")
        self.assertEqual(argv[argv.index("--model") + 1], "sonnet")
        self.assertEqual(argv[argv.index("--effort") + 1], "medium")
        self.assertEqual(argv[argv.index("--output-format") + 1], "stream-json")
        self.assertNotIn("--resume", argv)
        self.assertFalse(any(TOPIC in a for a in argv))
        self.assertIn(TOPIC, first["prompt"])
        self.assertIn(pid, first["cwd"])  # 작업별 디렉터리

        self.assertEqual(self.select(s, ["cand_1", "cand_2"]).status_code, 200)
        s = self.state(pid)
        self.assertEqual(self.c.post(f"/api/projects/{pid}/deep-research",
                                     json={"version": s["version"], "requestId": rid()}).status_code, 202)
        s = self.wait(pid, lambda x: x["stage"] == "waiting_for_user")
        q = s["question"]
        self.assertEqual(q["question"], "어떤 독자에게 전달할까요?")
        self.assertEqual([o["id"] for o in q["options"]], ["general", "developer"])
        self.assertEqual(q["recommendation"]["optionId"], "general")
        self.assertEqual(s["currentRun"]["status"], "waiting_for_user")
        # 질문을 기다리는 동안에도 지금까지의 조사 기록이 보인다. 근거 없는 사실은 확정으로 남지 않는다
        facts = {f["text"][:6]: f for f in s["researchNotes"]["facts"]}
        self.assertEqual(facts["A사가 새 "]["status"], "confirmed")
        self.assertEqual(facts["근거 주소를"]["status"], "unverified")
        self.assertEqual(facts["근거 주소를"]["sourceUrl"], "")
        self.assertEqual([x["url"] for x in s["researchNotes"]["sources"]], ["https://example.org/a"])
        with db.read() as conn:
            sid = conn.execute("SELECT engine_session_id, engine FROM runs WHERE id=?", (s["currentRun"]["runId"],)).fetchone()
        self.assertTrue(sid["engine_session_id"])
        self.assertEqual(sid["engine"], "claude-cli")

        # 질문에 답하면 같은 세션 ID로 --resume 해서 이어간다
        self.assertEqual(self.answer(s, "developer").status_code, 200)
        s = self.wait(pid, lambda x: x["stage"] == "proposal_ready")
        calls = self.calls()
        self.assertEqual(len(calls), 3)  # 후보 조사 1 + 심층 조사 1 + 답변 이어가기 1
        resume = calls[2]
        self.assertEqual(resume["resume"], sid["engine_session_id"])
        self.assertIn("사용자가 질문에 답했다", resume["prompt"])
        self.assertIn("개발자", resume["prompt"])
        self.assertEqual(resume["argv"][resume["argv"].index("--model") + 1], "sonnet")
        # 게시일이 미확인인 후보가 있어 '최신'은 쓸 수 없고, 그 문구는 걸러진다(D15)
        self.assertFalse(s["proposal"]["freshnessBasis"]["titleMayUseLatest"])
        self.assertEqual(len(s["proposal"]["hooks"]), 2)
        self.assertFalse(any("최신" in h for h in s["proposal"]["hooks"]))
        self.assertEqual(s["proposal"]["formatRecommendation"]["format"], "story")
        self.assertIsNone(s["question"])
        health = self.c.get("/api/health").json()
        self.assertEqual(health["engine"], "claude-cli")
        self.assertFalse(health["mock"])
        self.assertEqual(health["checks"]["claude"], "ok")
        self.assertEqual(health["checks"]["search"], "ok")
        self.assertEqual(health["settings"], {"model": "sonnet", "effort": "medium"})

    def test_double_answer_calls_claude_once(self):
        pid = self.to_question()
        s = self.wait(pid, lambda x: x["stage"] == "waiting_for_user")
        req = rid()
        self.assertEqual(self.answer(s, "general", request_id=req).status_code, 200)
        self.assertEqual(self.answer(s, "general", request_id=req).status_code, 200)
        self.wait(pid, lambda x: x["stage"] == "proposal_ready")
        self.assertEqual(len([c for c in self.calls() if c["resume"]]), 1)

    def test_no_question_when_input_is_enough(self):
        pid = self.to_question("질문없음 시험")
        s = self.wait(pid, lambda x: x["stage"] == "proposal_ready")
        self.assertIsNone(s["question"])
        self.assertEqual(len(self.calls()), 2)  # 후보 조사 + 심층 조사. 질문이 없으니 이어가기 호출도 없다

    def test_json_schema_is_passed_and_structured_output_wins_over_broken_text(self):
        # 실제 실행에서 모델이 중괄호 하나를 빼먹은 텍스트를 냈다. structured_output이 있으면 그것을 쓴다.
        pid = self.research_start("괄호누락 시험")
        s = self.wait(pid, lambda x: x["stage"] == "candidates_ready")
        self.assertEqual([c["title"] for c in s["candidates"]], ["정상 후보 A", "날짜 미확인 후보 B"])
        self.assertEqual(len(self.calls()), 1)  # 고쳐 쓰기 호출 없이 끝났다
        argv = self.calls()[0]["argv"]
        schema = json.loads(argv[argv.index("--json-schema") + 1])
        self.assertEqual(schema["properties"]["status"], {"enum": ["result", "error"]})
        self.assertIn("candidates", schema["properties"])

    def test_format_repair_once(self):
        pid = self.research_start("형식복구 시험")
        s = self.wait(pid, lambda x: x["stage"] == "candidates_ready")
        self.assertEqual([c["title"] for c in s["candidates"]], ["복구된 후보"])
        calls = self.calls()
        self.assertEqual(len(calls), 2)
        self.assertIsNotNone(calls[1]["resume"])
        self.assertIn("약속한 형식이 아니다", calls[1]["prompt"])

    def test_lost_session_falls_back_to_new_session_with_saved_answers(self):
        pid = self.to_question("세션없음 시험")
        s = self.wait(pid, lambda x: x["stage"] == "waiting_for_user")
        self.assertEqual(self.answer(s, "general").status_code, 200)
        self.wait(pid, lambda x: x["stage"] == "proposal_ready")
        calls = self.calls()
        self.assertIsNotNone(calls[-2]["resume"])  # 먼저 이어가기를 시도했고
        self.assertIsNone(calls[-1]["resume"])      # 실패하자 저장된 요약으로 새 세션을 열었다
        self.assertIn("사용자가 이미 답한 질문", calls[-1]["prompt"])
        self.assertIn("일반 사용자", calls[-1]["prompt"])


class FailureTests(ClaudeBase):
    def test_search_tool_not_used_is_search_unavailable(self):
        pid = self.research_start("검색불가 시험")
        s = self.failed(pid)
        err = s["currentRun"]["error"]
        self.assertEqual(err["code"], "search_unavailable")
        self.assertTrue(err["nextAction"])
        self.assertEqual(s["stage"], "created")
        self.assertEqual(s["candidates"], [])  # 검색 없이 나온 '그럴듯한 후보'는 쓰지 않는다
        self.assertEqual(self.c.get("/api/health").json()["checks"]["search"], "unknown")

    def test_search_error_shows_cause(self):
        pid = self.research_start("검색오류 시험")
        err = self.failed(pid)["currentRun"]["error"]
        self.assertEqual(err["code"], "search_unavailable")
        self.assertIn("search backend error", err["message"])
        self.assertEqual(self.c.get("/api/health").json()["checks"]["search"], "search_unavailable")

    def test_retry_after_failure_works(self):
        pid = self.research_start("검색불가 시험")
        s = self.failed(pid)
        os.environ["CARDNEWS_CLAUDE_CMD"] = json.dumps([sys.executable, FAKE])
        r = self.research(pid, s["version"])  # 같은 프로젝트 다시 시도(같은 주제라 또 실패하지만 새 작업이 만들어진다)
        self.assertEqual(r.status_code, 202)
        self.assertNotEqual(r.json()["runId"], s["currentRun"]["runId"])

    def test_login_required(self):
        pid = self.research_start("로그인 시험")
        err = self.failed(pid)["currentRun"]["error"]
        self.assertEqual(err["code"], "auth_required")
        self.assertIn("로그인", err["message"])
        self.assertEqual(self.c.get("/api/health").json()["checks"]["claude"], "auth_required")

    def test_unusable_format_is_failure_not_success(self):
        pid = self.research_start("형식오류 시험")
        s = self.failed(pid)
        self.assertEqual(s["currentRun"]["error"]["code"], "engine_failed")
        self.assertIn("형식", s["currentRun"]["error"]["message"])
        self.assertEqual(s["candidates"], [])
        self.assertEqual(len(self.calls()), 2)  # 원래 호출 + 고쳐 쓰기 한 번. 더 반복하지 않는다

    def test_missing_cli_is_reported(self):
        os.environ["CARDNEWS_CLAUDE_CMD"] = json.dumps([str(Path(tempfile.gettempdir()) / "no_such_claude.exe")])
        pid = self.research_start("실행불가 시험")
        err = self.failed(pid)["currentRun"]["error"]
        self.assertEqual(err["code"], "engine_failed")
        self.assertIn("실행하지 못했습니다", err["message"])

    def test_timeout_kills_process(self):
        os.environ["CARDNEWS_CLAUDE_TIMEOUT"] = "2"
        pid = self.research_start("시간초과 시험")
        s = self.failed(pid)
        self.assertEqual(s["currentRun"]["error"]["code"], "engine_failed")
        self.assertIn("초 안에", s["currentRun"]["error"]["message"])
        child = self.calls()[0]["pid"]
        for _ in range(50):
            if not pid_alive(child):
                break
            time.sleep(0.1)
        self.assertFalse(pid_alive(child))


class CancelTests(ClaudeBase):
    def test_cancel_terminates_child_process(self):
        pid = self.research_start("오래걸림 시험")
        for _ in range(100):  # 가짜 claude가 실제로 떠 있을 때까지
            if Path(self.logfile.name).read_text(encoding="utf-8").strip():
                break
            time.sleep(0.1)
        child = self.calls()[0]["pid"]
        self.assertTrue(pid_alive(child))
        run = self.state(pid)["currentRun"]["runId"]
        r = self.c.post(f"/api/runs/{run}/cancel")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["cleanup"]["processesTerminated"], 1)
        for _ in range(50):
            if not pid_alive(child):
                break
            time.sleep(0.1)
        self.assertFalse(pid_alive(child))
        s = self.state(pid)
        self.assertEqual(s["currentRun"]["status"], "cancelled")
        self.assertEqual(s["stage"], "created")
        time.sleep(1)
        self.assertEqual(self.state(pid)["candidates"], [])


if __name__ == "__main__":
    unittest.main()
