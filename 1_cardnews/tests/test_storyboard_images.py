"""스토리보드·이미지 단계 테스트: 모의 엔진(전체 흐름), 가짜 claude + 가짜 agy(실제 호출 경로), 파일 검사."""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from PIL import Image

from test_flow import Base, rid  # noqa: E402  (임시 DATA_DIR와 환경 변수를 먼저 설정한다)

from app import db, images  # noqa: E402
from app.main import ENGINES  # noqa: E402

HERE = Path(__file__).parent
FAKE_CLAUDE = str(HERE / "fake_claude.py")
FAKE_AGY = str(HERE / "fake_agy.py")
FAKE_BROWSER = str(HERE / "fake_browser.py")


class StoryBase(Base):
    engine_name = "mock"
    agy_on = False  # agy는 기본 켜짐이지만 시험은 명시적으로 정한다: True면 켜고(가짜 agy), False면 끈다. 진짜 agy는 어떤 시험도 부르지 않는다

    def setUp(self):
        self.saved = {k: os.environ.get(k) for k in ("CARDNEWS_ENGINE", "CARDNEWS_CLAUDE_CMD", "CARDNEWS_AGY_CMD", "CARDNEWS_AGY_HOME",
                                                     "FAKE_CLAUDE_LOG", "FAKE_AGY_LOG", "FAKE_AGY_MODE", "CARDNEWS_AGY_TIMEOUT", "FAKE_SCENE_FAIL", "CARDNEWS_USE_AGY",
                                                     "CARDNEWS_BROWSER_CMD", "CARDNEWS_BROWSER_TIMEOUT", "FAKE_BROWSER_LOG", "FAKE_BROWSER_MODE", "FAKE_SVG_MODE")}
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["CARDNEWS_ENGINE"] = self.engine_name
        os.environ["CARDNEWS_USE_AGY"] = "1" if self.agy_on else "0"
        if self.engine_name == "claude-cli":
            self.claude_log = os.path.join(self.tmp.name, "claude.jsonl")
            self.agy_log = os.path.join(self.tmp.name, "agy.jsonl")
            os.environ["CARDNEWS_CLAUDE_CMD"] = json.dumps([sys.executable, FAKE_CLAUDE])
            os.environ["CARDNEWS_AGY_CMD"] = json.dumps([sys.executable, FAKE_AGY])
            os.environ["CARDNEWS_AGY_HOME"] = os.path.join(self.tmp.name, "agyhome")
            os.environ["FAKE_CLAUDE_LOG"] = self.claude_log
            os.environ["FAKE_AGY_LOG"] = self.agy_log
            self.browser_log = os.path.join(self.tmp.name, "browser.jsonl")
            os.environ["CARDNEWS_BROWSER_CMD"] = json.dumps([sys.executable, FAKE_BROWSER])  # 진짜 Edge가 아니라 가짜 브라우저
            os.environ["FAKE_BROWSER_LOG"] = self.browser_log
            for k in ("FAKE_BROWSER_MODE", "FAKE_SVG_MODE", "CARDNEWS_BROWSER_TIMEOUT"):
                os.environ.pop(k, None)
        images.STATE["agy"] = "unknown"
        ENGINES["claude-cli"].state.update({"auth": "unknown", "search": "unknown"})
        super().setUp()

    def tearDown(self):
        ENGINES["claude-cli"].shutdown()
        super().tearDown()
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def set_agy(self, mode):
        os.environ["FAKE_AGY_MODE"] = mode

    def to_proposal(self, topic="스토리보드 시험"):
        pid, _ = self.create_and_research(topic)
        s = self.state(pid)
        self.assertEqual(self.select(s, ["cand_1", "cand_2"]).status_code, 200)
        s = self.state(pid)
        self.c.post(f"/api/projects/{pid}/deep-research", json={"version": s["version"], "requestId": rid()})
        s = self.wait(pid, lambda x: x["stage"] in ("waiting_for_user", "proposal_ready"), 30)
        if s["stage"] == "waiting_for_user":
            self.answer(s, "general")
            s = self.wait(pid, lambda x: x["stage"] == "proposal_ready", 30)
        return pid, s

    def create_and_research(self, topic):
        p = self.create(topic)
        self.research(p["projectId"], p["version"])
        s = self.wait(p["projectId"], lambda x: x["stage"] == "candidates_ready", 30)
        return p["projectId"], s

    def make_storyboard(self, pid, fmt="story", hook=0):
        s = self.state(pid)
        r = self.c.post(f"/api/projects/{pid}/storyboard", json={"format": fmt, "hookIndex": hook, "version": s["version"], "requestId": rid()})
        self.assertEqual(r.status_code, 202, r.text)
        return self.wait(pid, lambda x: x["stage"] == "storyboard_ready", 30)

    def approve(self, pid):
        s = self.state(pid)
        return self.c.post(f"/api/projects/{pid}/approve", json={"stage": "storyboard", "version": s["version"], "requestId": rid()})


class MockFlowTests(StoryBase):
    def test_storyboard_rules_and_no_images_before_approval(self):
        pid, _ = self.to_proposal()
        for fmt, n in (("list", 5), ("story", 7)):
            s = self.make_storyboard(pid, fmt)
            sb = s["storyboard"]
            self.assertEqual(sb["format"], fmt)
            self.assertEqual(len(sb["cards"]), n)
            self.assertFalse(sb["approved"])
            self.assertEqual(s["cards"], [])  # 승인 전에는 카드 이미지가 없다
            for c in sb["cards"]:
                self.assertTrue(c["keyline"] and c["body"] and c["imageRole"] and c["imagePlan"] and c["evidence"])
                self.assertEqual(sb_sentences(c["keyline"]), 1)
            self.assertEqual([c["role"] for c in sb["cards"]][0], "cover")
            self.assertEqual([c["role"] for c in sb["cards"]][-1], "summary")
            self.assertEqual(len(sb["style"]["palette"]), 5)
        self.assertEqual(db_count("select count(*) from runs where project_id=? and kind='images'", pid), 0)
        self.assertFalse(Path(db.DATA_DIR, "projects", pid, "cards").exists())

    def test_approve_generates_verified_png_files(self):
        pid, _ = self.to_proposal()
        self.make_storyboard(pid, "story")
        self.assertEqual(self.approve(pid).status_code, 202)
        s = self.wait(pid, lambda x: x["stage"] == "review", 60)
        self.assertEqual(len(s["cards"]), 7)
        self.assertTrue(s["storyboard"]["approved"])
        for c in s["cards"]:
            self.assertEqual(c["status"], "ready", c)
            self.assertEqual((c["width"], c["height"]), (1080, 1350))
            self.assertEqual(c["tool"], "mock")
            self.assertEqual({k: c["checks"][k] for k in ("file", "format", "size", "margin")}, {"file": "pass", "format": "pass", "size": "pass", "margin": "pass"})
            self.assertEqual(c["checks"]["textInImage"], "unchecked")
            r = self.c.get(c["imageUrl"])
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.content[:8], b"\x89PNG\r\n\x1a\n")
            path = Path(db.DATA_DIR, "projects", pid, "cards", c["id"], "v1", "image.png")
            with Image.open(path) as im:
                self.assertEqual(im.size, (1080, 1350))
            self.assertEqual(path.stat().st_size, c["bytes"])
            self.assertTrue((path.parent / "provenance.json").is_file())
        # 카드 사이 색감 통일: 같은 팔레트의 색만 쓴다(모의 도안)
        palette = {tuple(int(h[i:i + 2], 16) for i in (1, 3, 5)) for h in s["storyboard"]["style"]["palette"]}
        with Image.open(Path(db.DATA_DIR, "projects", pid, "cards", "card_3", "v1", "image.png")) as im:
            top_colors = {im.getpixel((x, 100)) for x in range(0, 1080, 90)}
        self.assertTrue(all(any(sum(abs(a - b) for a, b in zip(c, pal)) < 90 for pal in palette) for c in top_colors), top_colors)

    def test_approval_guards_and_idempotency(self):
        pid, _ = self.to_proposal()
        self.assertEqual(self.approve(pid).status_code, 409)  # 스토리보드가 아직 없다
        self.make_storyboard(pid, "list")
        s = self.state(pid)
        stale = self.c.post(f"/api/projects/{pid}/approve", json={"stage": "storyboard", "version": s["version"] - 1, "requestId": rid()})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["error"]["code"], "stale_version")
        req = rid()
        a = self.c.post(f"/api/projects/{pid}/approve", json={"stage": "storyboard", "version": s["version"], "requestId": req})
        b = self.c.post(f"/api/projects/{pid}/approve", json={"stage": "storyboard", "version": s["version"], "requestId": req})
        self.assertEqual((a.status_code, b.status_code), (202, 202))
        self.assertEqual(a.json(), b.json())
        self.wait(pid, lambda x: x["stage"] == "review", 60)
        self.assertEqual(db_count("select count(*) from runs where project_id=? and kind='images'", pid), 1)
        final = self.c.post(f"/api/projects/{pid}/approve", json={"stage": "final", "version": self.state(pid)["version"], "requestId": rid()})
        self.assertEqual(final.status_code, 409)  # 글자를 올리기 전에는 최종 승인할 수 없다(final_not_ready)
        self.assertEqual(final.json()["error"]["code"], "final_not_ready")

    def test_revision_creates_new_version_and_keeps_old(self):
        pid, _ = self.to_proposal()
        s1 = self.make_storyboard(pid, "story")
        s = self.state(pid)
        r = self.c.post(f"/api/projects/{pid}/revisions", json={"target": "storyboard", "instruction": "3장 제목을 더 짧게", "version": s["version"], "requestId": rid()})
        self.assertEqual(r.status_code, 202, r.text)
        s2 = self.wait(pid, lambda x: x["stage"] == "storyboard_ready" and x["storyboard"]["version"] == 2, 30)
        self.assertEqual(s2["storyboard"]["instruction"], "3장 제목을 더 짧게")
        self.assertEqual(db_count("select count(*) from storyboards where project_id=?", pid), 2)  # 이전 버전 보관
        self.assertEqual(self.c.post(f"/api/projects/{pid}/revisions", json={"target": "cards", "instruction": "x", "version": s2["version"], "requestId": rid()}).status_code, 400)  # 카드 수정은 카드를 골라야 하고, 스토리보드 단계에서는 할 수 없다

    def test_retry_makes_new_version_and_keeps_old_file(self):
        pid, _ = self.to_proposal()
        self.make_storyboard(pid, "list")
        self.approve(pid)
        s = self.wait(pid, lambda x: x["stage"] == "review", 60)
        r = self.c.post(f"/api/projects/{pid}/cards/retry", json={"cardIds": ["card_2"], "version": s["version"], "requestId": rid()})
        self.assertEqual(r.status_code, 202, r.text)
        s = self.wait(pid, lambda x: x["stage"] == "review" and next(c for c in x["cards"] if c["id"] == "card_2")["currentVersion"] == 2, 60)
        c2 = next(c for c in s["cards"] if c["id"] == "card_2")
        self.assertEqual([v["version"] for v in c2["versions"]], [1, 2])
        self.assertTrue(Path(db.DATA_DIR, "projects", pid, "cards", "card_2", "v1", "image.png").is_file())
        others = [c for c in s["cards"] if c["id"] != "card_2"]
        self.assertTrue(all(c["currentVersion"] == 1 for c in others))  # 다른 카드는 바뀌지 않는다
        self.assertEqual(self.c.post(f"/api/projects/{pid}/cards/retry", json={"cardIds": ["nope"], "version": s["version"], "requestId": rid()}).status_code, 400)

    def test_retry_of_chosen_cards_does_not_build_other_pending_cards(self):
        pid, _ = self.to_proposal()
        self.make_storyboard(pid, "list")
        self.approve(pid)
        s = self.wait(pid, lambda x: x["stage"] == "review", 60)
        with db.tx() as c:  # 앞선 작업을 취소해서 다른 카드가 대기 상태로 남은 상황을 만든다
            c.execute("UPDATE cards SET status='pending' WHERE project_id=? AND card_id IN ('card_1','card_3','card_4')", (pid,))
        s = self.state(pid)
        r = self.c.post(f"/api/projects/{pid}/cards/retry", json={"cardIds": ["card_2"], "version": s["version"], "requestId": rid()})
        self.assertEqual(r.status_code, 202, r.text)
        s = self.wait(pid, lambda x: x["stage"] == "review" and next(c for c in x["cards"] if c["id"] == "card_2")["currentVersion"] == 2, 60)
        by = {c["id"]: c for c in s["cards"]}
        self.assertEqual(by["card_2"]["currentVersion"], 2)
        for other in ("card_1", "card_3", "card_4"):
            self.assertEqual((by[other]["status"], by[other]["currentVersion"]), ("pending", 1), other)  # 만들지 않고 그대로 대기
        # 고르지 않고 '만들지 못한 카드 다시 만들기'를 누르면 대기 중인 카드를 모두 만든다
        r = self.c.post(f"/api/projects/{pid}/cards/retry", json={"version": s["version"], "requestId": rid()})
        self.assertEqual(r.status_code, 202)
        s = self.wait(pid, lambda x: x["stage"] == "review" and all(c["status"] == "ready" for c in x["cards"]), 60)
        self.assertTrue(all(c["currentVersion"] >= 2 for c in s["cards"] if c["id"] in ("card_1", "card_3", "card_4")))

    def test_files_endpoint_is_restricted(self):
        pid, _ = self.to_proposal()
        self.make_storyboard(pid, "list")
        self.approve(pid)
        self.wait(pid, lambda x: x["stage"] == "review", 60)
        for bad in ("../app.db", "../../app.db", "cards/../../app.db", "runs/x/stream.jsonl", "cards/card_1/v1/../../../../app.db"):
            self.assertEqual(self.c.get(f"/api/projects/{pid}/files/{bad}").status_code, 404, bad)
        self.assertEqual(self.c.get(f"/api/projects/{pid}/files/cards/card_1/v1/image.png").status_code, 200)
        self.assertEqual(self.c.get("/api/projects/prj_nope/files/cards/card_1/v1/image.png").status_code, 404)


def sb_sentences(text):
    from app import claude_parse as cp
    return cp.sentence_count(text)


def db_count(sql, *args):
    with db.read() as c:
        return c.execute(sql, args).fetchone()[0]


class ClaudeAgyTests(StoryBase):
    """agy를 켠 경우의 실제 호출 경로: 가짜 claude(스토리보드·도안)와 가짜 agy(이미지)."""
    engine_name = "claude-cli"
    agy_on = True

    def full(self, topic, agy_mode, fmt="story"):
        self.set_agy(agy_mode)
        pid, _ = self.to_proposal(topic)
        self.make_storyboard(pid, fmt)
        self.approve(pid)
        return pid, self.wait(pid, lambda x: x["stage"] == "review", 120)

    def test_storyboard_via_claude_has_no_tools_and_validates_sources(self):
        self.set_agy("success")
        pid, _ = self.to_proposal("스토리보드 Claude 시험")
        s = self.make_storyboard(pid, "story")
        call = [c for c in self.claude_calls() if "카드뉴스 편집자" in c["prompt"]][0]
        argv = call["argv"]
        self.assertEqual(argv[argv.index("--tools") + 1], "")  # 스토리보드는 도구 없이
        self.assertNotIn("--allowedTools", argv)
        self.assertIn("--restricted", argv)
        self.assertIn("스토리보드 Claude 시험", call["prompt"])
        self.assertFalse(any("스토리보드 Claude 시험" in a for a in argv))
        sb = s["storyboard"]
        self.assertEqual(len(sb["cards"]), 7)
        allowed = {"https://example.org/a", "https://example.org/b"}
        for c in sb["cards"]:
            self.assertTrue(c["evidence"])
            self.assertTrue(all(e["url"].rstrip("/") in allowed or "example.org/b" in e["url"] for e in c["evidence"]), c["evidence"])
        self.assertNotIn("https://invented.example.net/z", json.dumps(sb))  # 지어낸 출처는 걸러진다
        self.assertTrue(any(c["unsupportedNumbers"] for c in sb["cards"]))  # 자료에 없는 숫자는 표시된다

    def claude_calls(self):
        return [json.loads(x) for x in Path(self.claude_log).read_text(encoding="utf-8").splitlines() if x.strip()]

    def agy_calls(self):
        p = Path(self.agy_log)
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []

    def test_agy_success_image_found_in_brain_folder_and_normalized(self):
        pid, s = self.full("agy성공 시험", "success")
        self.assertEqual(len(self.agy_calls()), 7)
        for c in s["cards"]:
            self.assertEqual(c["status"], "ready", c)
            self.assertEqual(c["tool"], "agy")
            self.assertEqual((c["width"], c["height"]), (1080, 1350))
            self.assertEqual(c["checks"]["margin"], "pass")
            prov = c["provenance"]
            self.assertEqual(prov["originalSize"], [768, 1024])  # agy 원본은 3:4였고 1080×1350으로 맞췄다
            self.assertIn("확대·잘라냄", prov["normalization"])
            self.assertIn("팔레트", prov["prompt"])
            self.assertIn("글자", prov["prompt"])
        first = self.agy_calls()[0]
        self.assertIn("-p", first["argv"])
        self.assertEqual(first["argv"][first["argv"].index("--output-format") + 1], "json")
        self.assertEqual(images.STATE["agy"], "ok")
        # 같은 그림체·팔레트가 모든 카드의 프롬프트에 들어간다
        palette = s["storyboard"]["style"]["palette"]
        for call in self.agy_calls():
            self.assertTrue(all(col in call["prompt"] for col in palette))
            self.assertIn(s["storyboard"]["style"]["artStyle"], call["prompt"])
        # 이미지 도구를 못 쓰는 Claude 호출은 이 경우 없다(도안 요청 없음)
        self.assertFalse(any("도형 도안" in c["prompt"] or "SVG로 그리는" in c["prompt"] for c in self.claude_calls()))
        self.assertFalse(Path(self.browser_log).exists())  # agy가 만들었으니 브라우저도 쓰지 않았다

    def test_default_without_any_setting_tries_agy_first(self):
        os.environ.pop("CARDNEWS_USE_AGY", None)  # 설정 없음 = 기본(켜짐)
        pid, s = self.full("agy기본켜짐 시험", "success")
        self.assertEqual(len(self.agy_calls()), 7)
        self.assertTrue(all(c["tool"] == "agy" for c in s["cards"]))
        self.assertEqual(self.c.get("/api/health").json()["agyEnabled"], True)

    def test_agy_500_falls_back_to_claude_scene_with_reason(self):
        pid, s = self.full("agy실패 시험", "fail500")
        self.assertEqual(len(self.agy_calls()), 1)  # 첫 카드에서 실패한 뒤 나머지 카드에서는 agy를 다시 기다리지 않는다
        for c in s["cards"]:
            self.assertEqual(c["status"], "ready", c)
            self.assertEqual(c["tool"], "claude-cli SVG + 브라우저")
            self.assertEqual((c["width"], c["height"]), (1080, 1350))
            att = c["provenance"]["attempts"]
            self.assertEqual(att[0]["provider"], "agy")
            self.assertIn(att[0]["outcome"], ("failed", "skipped"))
            self.assertEqual(att[1], {"provider": "claude-svg", "outcome": "ok"})
        reason = s["cards"][0]["provenance"]["attempts"][0]["reason"]
        self.assertIn("500 Internal Server Error", reason)  # 대화 기록의 실제 오류를 원인으로 보여 준다
        self.assertIn("SUCCESS", reason)  # agy 응답 상태가 SUCCESS여도 이미지가 없었다는 점을 남긴다
        self.assertEqual(images.STATE["agy"], "image_failed")
        svg_calls = [c for c in self.claude_calls() if "SVG로 그리는" in c["prompt"]]
        self.assertEqual(len(svg_calls), 7)
        self.assertEqual(svg_calls[0]["argv"][svg_calls[0]["argv"].index("--tools") + 1], "")
        self.assertTrue(Path(db.DATA_DIR, "projects", pid, "cards", "card_1", "v1", "art.svg").is_file())
        health = self.c.get("/api/health").json()
        self.assertEqual(health["checks"]["agy"], "image_failed")

    def test_corrupt_agy_image_is_not_accepted(self):
        pid, s = self.full("agy손상 시험", "corrupt")
        self.assertTrue(all(c["tool"] == "claude-cli SVG + 브라우저" for c in s["cards"]))
        self.assertIn("agy", s["cards"][0]["provenance"]["attempts"][0]["provider"])

    def test_both_fail_card_fails_with_reason_and_retry_recovers(self):
        self.set_agy("fail500")
        pid, _ = self.to_proposal("도안실패 시험")
        self.make_storyboard(pid, "story")
        os.environ["FAKE_SCENE_FAIL"] = "1"  # 가짜 claude가 도안 요청에 일시 오류(529)를 낸다
        os.environ["FAKE_SVG_MODE"] = "fail"  # SVG 요청도 일시 오류 → agy·SVG·도형 도안 모두 실패
        self.approve(pid)
        s = self.wait(pid, lambda x: x["stage"] == "review", 120)
        failed = [c for c in s["cards"] if c["status"] == "failed"]
        self.assertEqual(len(failed), 7)
        self.assertIn("agy", failed[0]["error"]["message"])
        self.assertIn("claude-svg", failed[0]["error"]["message"])
        self.assertIn("claude-scene", failed[0]["error"]["message"])
        self.assertTrue(failed[0]["error"]["nextAction"])
        self.assertTrue(all(c["imageUrl"] is None for c in failed))
        # 확정한 스토리보드는 그대로이고, 다시 시도하면 새 작업이 만들어진다
        self.assertTrue(s["storyboard"]["approved"])
        self.assertEqual(s["storyboard"]["version"], 1)
        r = self.c.post(f"/api/projects/{pid}/cards/retry", json={"version": s["version"], "requestId": rid()})
        self.assertEqual(r.status_code, 202)

    def test_missing_agy_binary_falls_back(self):
        os.environ["CARDNEWS_AGY_CMD"] = json.dumps([str(Path(tempfile.gettempdir()) / "no_such_agy.exe")])
        pid, s = self.full("agy없음 시험", "success")
        self.assertTrue(all(c["tool"] == "claude-cli SVG + 브라우저" for c in s["cards"]))
        self.assertIn("실행하지 못했습니다", s["cards"][0]["provenance"]["attempts"][0]["reason"])

    def test_cancel_during_agy_kills_process_and_resets_cards(self):
        self.set_agy("slow")
        pid, _ = self.to_proposal("agy느림 시험")
        self.make_storyboard(pid, "story")
        self.approve(pid)
        for _ in range(100):
            if self.agy_calls():
                break
            time.sleep(0.1)
        child = self.agy_calls()[0]["pid"]
        run = self.state(pid)["currentRun"]["runId"]
        r = self.c.post(f"/api/runs/{run}/cancel")
        self.assertEqual(r.json()["cleanup"]["processesTerminated"], 1)
        for _ in range(50):
            if not pid_alive(child):
                break
            time.sleep(0.1)
        self.assertFalse(pid_alive(child))
        s = self.state(pid)
        self.assertEqual(s["stage"], "review")
        self.assertTrue(all(c["status"] == "pending" for c in s["cards"]))  # '만드는 중'으로 남지 않는다
        self.assertTrue(s["storyboard"]["approved"])


class ClaudeAgyOffTests(StoryBase):
    """agy를 끈 경우(--no-agy / CARDNEWS_USE_AGY=0): agy를 한 번도 부르지 않고 바로 Claude로 만들고, 그 사실을 기록·표시한다."""
    engine_name = "claude-cli"
    agy_on = False

    claude_calls = ClaudeAgyTests.claude_calls
    agy_calls = ClaudeAgyTests.agy_calls

    def run_flow(self, topic):
        self.set_agy("success")  # 혹시 불러도 성공하도록 해 두었기 때문에, 부르지 않았다는 것이 곧 꺼짐의 증거가 된다
        pid, _ = self.to_proposal(topic)
        self.make_storyboard(pid, "story")
        self.approve(pid)
        return pid, self.wait(pid, lambda x: x["stage"] == "review", 120)

    def test_off_setting_never_calls_agy_and_uses_claude_svg(self):
        self.assertFalse(images.agy_enabled())
        pid, s = self.run_flow("agy꺼짐 시험")
        self.assertEqual(self.agy_calls(), [])  # agy는 한 번도 실행되지 않았다
        self.assertFalse(Path(self.agy_log).exists())
        self.assertEqual(images.STATE["agy"], "unknown")
        self.assertEqual(len(s["cards"]), 7)
        for c in s["cards"]:
            self.assertEqual(c["status"], "ready", c)
            self.assertEqual(c["tool"], "claude-cli SVG + 브라우저")
            self.assertEqual((c["width"], c["height"]), (1080, 1350))
            att = c["provenance"]["attempts"]
            self.assertEqual(att[0]["provider"], "agy")
            self.assertEqual(att[0]["outcome"], "disabled")  # 화면이 '쓰지 않았다'를 보여 줄 근거
            self.assertIn("꺼져 있어", att[0]["reason"])
            self.assertIn("--no-agy", att[0]["reason"])  # 끈 설정과 다시 켜는 방법을 알려 준다
            self.assertIn("--agy", att[0]["reason"])
            self.assertEqual(att[1], {"provider": "claude-svg", "outcome": "ok"})
        self.assertEqual(len([c for c in self.claude_calls() if "SVG로 그리는" in c["prompt"]]), 7)
        self.assertEqual(len([c for c in self.claude_calls() if "도형 도안" in c["prompt"]]), 0)

    def test_health_and_state_report_disabled(self):
        os.environ["CARDNEWS_USE_AGY"] = "0"
        h = self.c.get("/api/health").json()
        self.assertEqual(h["checks"]["agy"], "disabled")
        self.assertIs(h["agyEnabled"], False)
        os.environ["CARDNEWS_USE_AGY"] = "1"
        h = self.c.get("/api/health").json()
        self.assertTrue(h["agyEnabled"])
        self.assertIn(h["checks"]["agy"], ("unknown", "ok", "image_failed"))  # 켜면 'disabled'가 아니다

    def test_error_message_does_not_blame_disabled_agy(self):
        self.set_agy("success")
        pid, _ = self.to_proposal("agy꺼짐 실패시험")
        self.make_storyboard(pid, "story")
        os.environ["FAKE_SCENE_FAIL"] = "1"  # Claude 도안도 실패
        os.environ["FAKE_SVG_MODE"] = "fail"  # SVG도 실패
        self.approve(pid)
        s = self.wait(pid, lambda x: x["stage"] == "review", 120)
        failed = [c for c in s["cards"] if c["status"] == "failed"]
        self.assertEqual(len(failed), 7)
        self.assertIn("claude-svg", failed[0]["error"]["message"])
        self.assertIn("claude-scene", failed[0]["error"]["message"])
        self.assertNotIn("agy", failed[0]["error"]["message"])  # 꺼진 agy를 실패 원인처럼 쓰지 않는다
        self.assertEqual(self.agy_calls(), [])

    def test_retry_also_skips_agy(self):
        pid, s = self.run_flow("agy꺼짐 재시도")
        r = self.c.post(f"/api/projects/{pid}/cards/retry", json={"cardIds": ["card_2"], "version": s["version"], "requestId": rid()})
        self.assertEqual(r.status_code, 202)
        s = self.wait(pid, lambda x: x["stage"] == "review" and next(c for c in x["cards"] if c["id"] == "card_2")["currentVersion"] == 2, 60)
        self.assertEqual(self.agy_calls(), [])
        self.assertEqual(next(c for c in s["cards"] if c["id"] == "card_2")["provenance"]["attempts"][0]["outcome"], "disabled")


class ClaudeSvgTests(StoryBase):
    """기본 경로: agy 꺼짐 → Claude SVG + 브라우저 캡처. 브라우저를 쓸 수 없거나 SVG가 검증을 통과하지 못하면 도형 도안으로 대체한다.
    브라우저는 진짜 Edge가 아니라 tests/fake_browser.py(가짜)다."""
    engine_name = "claude-cli"
    agy_on = False

    claude_calls = ClaudeAgyTests.claude_calls

    def browser_calls(self):
        p = Path(self.browser_log)
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []

    def flow(self, topic):
        pid, _ = self.to_proposal(topic)
        self.make_storyboard(pid, "story")
        self.approve(pid)
        return pid, self.wait(pid, lambda x: x["stage"] == "review", 180)

    def svg_calls(self):
        return [c for c in self.claude_calls() if "SVG로 그리는" in c["prompt"]]

    def scene_calls(self):
        return [c for c in self.claude_calls() if "도형 도안" in c["prompt"]]

    def test_default_pipeline_sanitizes_and_captures(self):
        pid, s = self.flow("SVG 기본 시험")
        for c in s["cards"]:
            self.assertEqual((c["status"], c["tool"]), ("ready", "claude-cli SVG + 브라우저"))
            self.assertEqual((c["width"], c["height"]), (1080, 1350))
            self.assertEqual(c["provenance"]["browser"], "fake_browser")
            self.assertEqual(c["checks"]["margin"], "pass")
            self.assertEqual(c["provenance"]["attempts"][-1], {"provider": "claude-svg", "outcome": "ok"})
        art = Path(db.DATA_DIR, "projects", pid, "cards", "card_1", "v1", "art.svg").read_text(encoding="utf-8")
        self.assertIn('clip-path="url(#below__)"', art)      # 위쪽 30%는 우리가 그린 배경만
        self.assertIn('y="405"', art)
        self.assertNotIn("#ff0000", art.lower())             # 팔레트에 없던 색은 팔레트 색으로 맞춰졌다
        self.assertNotIn("<script", art.lower())
        palette = {x.lower() for x in s["storyboard"]["style"]["palette"]}
        import re
        self.assertTrue({c.lower() for c in re.findall(r"#[0-9a-fA-F]{6}", art)} <= palette)
        call = self.browser_calls()[0]
        for flag in ("--headless=new", "--window-size=1080,1350", "--force-device-scale-factor=1"):
            self.assertIn(flag, call["args"])
        self.assertTrue(any(a.startswith("--user-data-dir=") for a in call["args"]))  # 사용자의 Edge 프로필과 섞이지 않는다
        self.assertEqual(len(self.browser_calls()), 7)
        self.assertEqual(len(self.svg_calls()), 7)
        self.assertEqual(self.scene_calls(), [])
        self.assertEqual(self.svg_calls()[0]["argv"][self.svg_calls()[0]["argv"].index("--tools") + 1], "")
        palette_text = ", ".join(s["storyboard"]["style"]["palette"])
        self.assertIn(palette_text, self.svg_calls()[0]["prompt"])  # 모든 카드가 같은 팔레트·그림체를 받는다
        self.assertEqual(self.c.get("/api/health").json()["checks"]["browser"], "found")

    def _falls_back(self, topic, svg_mode, reason_text):
        os.environ["FAKE_SVG_MODE"] = svg_mode
        pid, s = self.flow(topic)
        for c in s["cards"]:
            self.assertEqual((c["status"], c["tool"]), ("ready", "claude-cli 도안 + Pillow"), c)
            att = c["provenance"]["attempts"]
            self.assertEqual([a["provider"] for a in att], ["agy", "claude-svg", "claude-scene"])
            self.assertEqual(att[1]["outcome"], "failed")
            self.assertIn(reason_text, att[1]["reason"])
            self.assertEqual(att[2], {"provider": "claude-scene", "outcome": "ok"})
        self.assertEqual(len(self.scene_calls()), 7)
        self.assertEqual(self.browser_calls(), [])  # 검증에서 걸러진 SVG는 브라우저에 넣지 않는다
        return pid, s

    def test_script_in_svg_is_rejected_and_falls_back(self):
        self._falls_back("SVG 스크립트 시험", "script", "SVG 검증")

    def test_named_color_in_svg_is_rejected_and_falls_back(self):
        pid, s = self._falls_back("SVG 색이름 시험", "named", "SVG 검증")
        # 형식 고쳐 쓰기를 한 번 요청한 뒤에도 같은 오류면 포기한다(카드당 SVG 호출 2번)
        self.assertEqual(len(self.svg_calls()), 7)
        self.assertEqual(len([c for c in self.claude_calls() if "방금 답변은 약속한 형식이 아니다" in c["prompt"]]), 7)  # 카드마다 고쳐 쓰기 1번

    def test_text_in_svg_is_rejected_and_falls_back(self):
        self._falls_back("SVG 글자 시험", "text", "<text>")

    def test_malformed_svg_falls_back(self):
        self._falls_back("SVG 깨진XML 시험", "badxml", "XML")

    def test_claude_failure_in_svg_falls_back(self):
        self._falls_back("SVG 호출실패 시험", "fail", "Claude SVG 만들기 실패")

    def test_browser_missing_skips_svg_without_calling_claude(self):
        os.environ["CARDNEWS_BROWSER_CMD"] = json.dumps([str(Path(tempfile.gettempdir()) / "no_such_browser.exe")])
        self.assertEqual(self.c.get("/api/health").json()["checks"]["browser"], "missing")
        pid, s = self.flow("SVG 브라우저없음 시험")
        self.assertEqual(self.svg_calls(), [])  # 쓸 수 없는 것을 알면 Claude를 부르지 않는다
        for c in s["cards"]:
            self.assertEqual((c["status"], c["tool"]), ("ready", "claude-cli 도안 + Pillow"))
            att = c["provenance"]["attempts"]
            self.assertIn(att[1]["outcome"], ("failed", "skipped"))
            self.assertIn("Edge나 Chrome", att[1]["reason"])
        self.assertEqual(len(self.scene_calls()), 7)

    def test_blank_capture_falls_back_and_stops_trying_svg(self):
        os.environ["FAKE_BROWSER_MODE"] = "blank"
        pid, s = self.flow("SVG 빈캡처 시험")
        self.assertTrue(all(c["tool"] == "claude-cli 도안 + Pillow" for c in s["cards"]))
        self.assertIn("빈 이미지", s["cards"][0]["provenance"]["attempts"][1]["reason"])
        self.assertEqual(len(self.svg_calls()), 1)  # 브라우저가 못 그리면 나머지 카드에서는 SVG를 또 시도하지 않는다
        self.assertEqual([c["provenance"]["attempts"][1]["outcome"] for c in s["cards"]], ["failed"] + ["skipped"] * 6)

    def test_browser_crash_falls_back(self):
        os.environ["FAKE_BROWSER_MODE"] = "crash"
        pid, s = self.flow("SVG 브라우저종료 시험")
        self.assertTrue(all(c["tool"] == "claude-cli 도안 + Pillow" for c in s["cards"]))
        self.assertIn("캡처 파일을 만들지 못했습니다", s["cards"][0]["provenance"]["attempts"][1]["reason"])

    def test_wrong_size_capture_is_normalized_and_recorded(self):
        os.environ["FAKE_BROWSER_MODE"] = "wrongsize"
        pid, s = self.flow("SVG 크기다름 시험")
        for c in s["cards"]:
            self.assertEqual((c["tool"], c["width"], c["height"]), ("claude-cli SVG + 브라우저", 1080, 1350))
            self.assertEqual(c["provenance"]["originalSize"], [800, 600])

    def test_browser_timeout_kills_process_and_falls_back(self):
        os.environ["FAKE_BROWSER_MODE"] = "hang"
        os.environ["CARDNEWS_BROWSER_TIMEOUT"] = "2"
        pid, s = self.flow("SVG 브라우저멈춤 시험")
        self.assertTrue(all(c["tool"] == "claude-cli 도안 + Pillow" for c in s["cards"]))
        self.assertIn("초 안에", s["cards"][0]["provenance"]["attempts"][1]["reason"])
        child = self.browser_calls()[0]["pid"]
        for _ in range(50):
            if not pid_alive(child):
                break
            time.sleep(0.1)
        self.assertFalse(pid_alive(child))

    def test_cancel_kills_browser_process_and_resets_cards(self):
        os.environ["FAKE_BROWSER_MODE"] = "hang"
        pid, _ = self.to_proposal("SVG 취소 시험")
        self.make_storyboard(pid, "story")
        self.approve(pid)
        for _ in range(100):
            if self.browser_calls():
                break
            time.sleep(0.1)
        child = self.browser_calls()[0]["pid"]
        self.assertTrue(pid_alive(child))
        run = self.state(pid)["currentRun"]["runId"]
        r = self.c.post(f"/api/runs/{run}/cancel")
        self.assertGreaterEqual(r.json()["cleanup"]["processesTerminated"], 1)
        for _ in range(50):
            if not pid_alive(child):
                break
            time.sleep(0.1)
        self.assertFalse(pid_alive(child))
        s = self.state(pid)
        self.assertEqual(s["stage"], "review")
        self.assertTrue(all(c["status"] == "pending" for c in s["cards"]))


class AgyDiscoveryTests(unittest.TestCase):
    """서버를 켠 터미널의 PATH에 agy가 없어도(실제로 사용자의 PC에서 \"찾지 못했습니다\"가 났다) 기본 설치 위치에서 찾는지."""

    KEYS = ("CARDNEWS_AGY_CMD", "CARDNEWS_AGY", "PATH", "LOCALAPPDATA")

    def setUp(self):
        self.saved = {k: os.environ.get(k) for k in self.KEYS}
        os.environ.pop("CARDNEWS_AGY_CMD", None)  # 시험 전체의 \"즉시 실패하는 가짜\"를 잠시 치우고 탐색 함수만 본다(agy는 실행하지 않는다)
        os.environ.pop("CARDNEWS_AGY", None)
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["PATH"] = os.path.join(self.tmp.name, "empty_path")  # PATH에 agy가 없는 상황
        os.environ["LOCALAPPDATA"] = self.tmp.name

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def make_agy(self, *parts):
        path = Path(self.tmp.name, *parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fake", encoding="utf-8")
        return str(path)

    def test_not_found_anywhere_reports_missing_with_hint(self):
        self.assertIsNone(images.find_agy())
        self.assertEqual(images.agy_health(), "missing")
        with self.assertRaises(images.ImageError) as cm:
            images.agy_prefix()
        self.assertEqual(cm.exception.code, "agy_unavailable")
        self.assertIn("CARDNEWS_AGY", cm.exception.message)  # 지정하는 방법을 알려 준다
        self.assertIn(str(images.default_agy_path()), cm.exception.message)  # 어디를 찾아봤는지도 알려 준다

    def test_default_install_location_is_used_when_not_on_path(self):
        exe = self.make_agy("agy", "bin", "agy.exe" if os.name == "nt" else "agy")
        self.assertEqual(os.path.normcase(images.find_agy()), os.path.normcase(exe))
        self.assertEqual(images.agy_prefix(), [images.find_agy()])
        self.assertNotEqual(images.agy_health(), "missing")

    def test_explicit_path_wins_and_a_wrong_explicit_path_is_not_silently_replaced(self):
        self.make_agy("agy", "bin", "agy.exe" if os.name == "nt" else "agy")
        mine = self.make_agy("elsewhere", "my_agy.exe")
        os.environ["CARDNEWS_AGY"] = mine
        self.assertEqual(images.find_agy(), mine)
        os.environ["CARDNEWS_AGY"] = os.path.join(self.tmp.name, "no_such_agy.exe")
        self.assertIsNone(images.find_agy())  # 지정한 경로가 없으면 기본 위치로 몰래 바꿔 쓰지 않는다
        self.assertEqual(images.agy_health(), "missing")

    def test_path_is_searched_before_the_default_location(self):
        self.make_agy("agy", "bin", "agy.exe" if os.name == "nt" else "agy")
        on_path = self.make_agy("bin_on_path", "agy.exe" if os.name == "nt" else "agy")
        os.environ["PATH"] = os.path.dirname(on_path)
        self.assertEqual(os.path.normcase(images.find_agy()), os.path.normcase(on_path))


class AgySwitchTests(unittest.TestCase):
    def test_env_values(self):
        saved = os.environ.get("CARDNEWS_USE_AGY")
        try:
            for v, want in (("0", False), ("false", False), ("OFF", False), ("no", False), (" 0 ", False),
                            ("1", True), ("true", True), ("on", True), ("yes", True), ("", True), ("2", True), ("아무거나", True)):
                os.environ["CARDNEWS_USE_AGY"] = v
                self.assertEqual(images.agy_enabled(), want, repr(v))
                if not want:
                    self.assertEqual(images.agy_health(), "disabled")
            os.environ.pop("CARDNEWS_USE_AGY", None)
            self.assertTrue(images.agy_enabled())  # 설정이 없으면 켜짐(기본)
            self.assertNotEqual(images.agy_health(), "disabled")
        finally:
            if saved is None:
                os.environ.pop("CARDNEWS_USE_AGY", None)
            else:
                os.environ["CARDNEWS_USE_AGY"] = saved

    def test_providers_order(self):
        saved = os.environ.get("CARDNEWS_USE_AGY")
        try:
            os.environ["CARDNEWS_USE_AGY"] = "0"
            off = images.image_providers(object())
            self.assertTrue(getattr(off[0], "off", False))  # agy 끔 → 실행하지 않고 기록만
            self.assertEqual([getattr(x, "name", None) for x in off], ["agy", "claude-svg", "claude-scene"])  # SVG가 먼저, 도형 도안이 대체
            os.environ.pop("CARDNEWS_USE_AGY", None)  # 설정이 없으면 기본 켜짐
            on = images.image_providers(object())
            self.assertIsInstance(on[0], images.AgyProvider)
            self.assertEqual([x.name for x in on], ["agy", "claude-svg", "claude-scene"])
        finally:
            if saved is None:
                os.environ.pop("CARDNEWS_USE_AGY", None)
            else:
                os.environ["CARDNEWS_USE_AGY"] = saved

    def test_run_py_documents_the_option(self):
        import subprocess
        out = subprocess.run([sys.executable, str(HERE.parent / "run.py"), "--help"], capture_output=True, text=True, encoding="utf-8").stdout
        self.assertIn("--agy", out)
        self.assertIn("--no-agy", out)


def pid_alive(pid):
    import subprocess
    if os.name == "nt":
        return str(pid) in subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class ImageFileTests(unittest.TestCase):
    def test_finalize_and_inspect(self):
        d = Path(tempfile.mkdtemp())
        # 3:4 원본 → 1080×1350
        im = Image.new("RGB", (768, 1024), (234, 242, 255))
        extra = images.finalize_image(im, d / "a.png")
        self.assertEqual(extra["originalSize"], [768, 1024])
        r = images.inspect_png(d / "a.png")
        self.assertEqual((r["width"], r["height"]), (1080, 1350))
        self.assertEqual(r["checks"]["size"], "pass")
        self.assertEqual(r["checks"]["margin"], "pass")
        # 가로가 더 넓은 원본(16:9)도 1080×1350으로
        images.finalize_image(Image.new("RGB", (1600, 900), (10, 20, 30)), d / "b.png")
        self.assertEqual(images.inspect_png(d / "b.png")["checks"]["size"], "pass")

    def test_inspect_detects_bad_files(self):
        d = Path(tempfile.mkdtemp())
        (d / "bad.png").write_bytes(b"not a png at all")
        r = images.inspect_png(d / "bad.png")
        self.assertEqual(r["checks"]["file"], "pass")
        self.assertEqual(r["checks"]["format"], "fail")
        self.assertEqual(r["checks"]["size"], "fail")
        missing = images.inspect_png(d / "none.png")
        self.assertEqual(missing["checks"]["file"], "fail")
        # 위쪽 여백에 그림이 있으면 경고
        im = Image.new("RGB", (1080, 1350), (255, 255, 255))
        for x in range(0, 1080, 40):
            for y in range(100, 400):
                im.putpixel((x, y), (0, 0, 0))
        im.save(d / "busy.png")
        self.assertEqual(images.inspect_png(d / "busy.png")["checks"]["margin"], "warn")


if __name__ == "__main__":
    unittest.main()


class AgyBrainFolderTests(unittest.TestCase):
    """agy가 "SUCCESS"여도 그림을 작업 폴더가 아니라 brain 아래 가장 최근 폴더에 JPG로 저장한 경우(agy는 실행하지 않는다)."""

    def test_newest_jpg_in_recent_brain_folder_found_even_without_conversation_id(self):
        import time
        with tempfile.TemporaryDirectory() as t:
            home, work = Path(t, "home"), Path(t, "work")
            work.mkdir()
            old = home / "brain" / "old-conv"
            new = home / "brain" / "new-conv" / "artifacts"
            old.mkdir(parents=True)
            new.mkdir(parents=True)
            Image.new("RGB", (30, 40), "red").save(old / "before.jpg")
            old_t = time.time() - 600
            os.utime(old / "before.jpg", (old_t, old_t))
            os.utime(old, (old_t, old_t))
            started = time.time() - 1
            Image.new("RGB", (30, 40), "blue").save(new / "generated.jpg")
            found = images.find_agy_image(work, home, [], started, "")
            self.assertEqual(found, new / "generated.jpg")  # 이번 실행 전의 옛 그림은 고르지 않는다

    def test_nothing_new_means_no_image(self):
        import time
        with tempfile.TemporaryDirectory() as t:
            home, work = Path(t, "home"), Path(t, "work")
            work.mkdir()
            old = home / "brain" / "old-conv"
            old.mkdir(parents=True)
            Image.new("RGB", (30, 40), "red").save(old / "before.jpg")
            old_t = time.time() - 600
            os.utime(old / "before.jpg", (old_t, old_t))
            os.utime(old, (old_t, old_t))
            self.assertIsNone(images.find_agy_image(work, home, [], time.time(), ""))

    def test_conversation_id_folder_jpg_is_taken_and_older_pictures_ignored(self):
        import time
        with tempfile.TemporaryDirectory() as t:
            home, work = Path(t, "home"), Path(t, "work")
            work.mkdir()
            conv = home / "brain" / "conv-123" / "artifacts"
            conv.mkdir(parents=True)
            other = home / "brain" / "conv-999"
            other.mkdir(parents=True)
            Image.new("RGB", (30, 40), "green").save(conv / "old.jpg")
            old_t = time.time() - 900
            os.utime(conv / "old.jpg", (old_t, old_t))
            started = time.time() - 1
            Image.new("RGB", (60, 80), "blue").save(conv / "new.jpg")
            found = images.find_agy_image(work, home, ["conv-123"], started, "")
            self.assertEqual(found, conv / "new.jpg")
            with Image.open(found) as im:
                self.assertEqual(im.format, "JPEG")


class TypesetApiTests(StoryBase):
    """생성 이미지 위에 글자를 올리는 기능: 모의 엔진 이미지로 시험한다(agy 없음)."""

    def reviewed(self):
        pid, _ = self.to_proposal("글자 올리기 시험")
        self.make_storyboard(pid, "story")
        self.assertEqual(self.approve(pid).status_code, 202)
        return pid, self.wait(pid, lambda x: x["stage"] == "review", 60)

    def url(self, pid, cid, tail=""):
        return f"/api/projects/{pid}/cards/{cid}/typeset{tail}"

    def test_default_layout_comes_from_storyboard_and_preview_does_not_change_state(self):
        pid, s = self.reviewed()
        card = s["storyboard"]["cards"][2]
        before = (s["version"], [(c["status"], c["currentVersion"], c["finalUrl"]) for c in s["cards"]])
        r = self.c.get(self.url(pid, card["id"]))
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()
        self.assertFalse(d["saved"])
        self.assertEqual((d["layout"]["title"], d["layout"]["body"]), (card["title"], card["body"]))
        pr = self.c.post(self.url(pid, card["id"], "/preview"), json={"layout": d["layout"]})
        self.assertEqual(pr.status_code, 200, pr.text)
        self.assertTrue(pr.json()["ok"], pr.json()["checks"])
        self.assertEqual(self.c.get(pr.json()["imageUrl"]).content[:8], b"\x89PNG\r\n\x1a\n")
        s2 = self.state(pid)
        self.assertEqual(before, (s2["version"], [(c["status"], c["currentVersion"], c["finalUrl"]) for c in s2["cards"]]))
        self.assertFalse(Path(db.DATA_DIR, "projects", pid, "cards", card["id"], "v1", "final.png").exists())

    def test_save_makes_final_png_and_keeps_base_image_and_other_cards(self):
        pid, s = self.reviewed()
        cid = s["cards"][1]["id"]
        base = Path(db.DATA_DIR, "projects", pid, "cards", cid, "v1", "image.png")
        digest = base.read_bytes()
        lay = self.c.get(self.url(pid, cid)).json()["layout"]
        lay["title"] = "고친 제목"
        r = self.c.put(self.url(pid, cid), json={"layout": lay})
        self.assertEqual(r.status_code, 200, r.text)
        final = Path(db.DATA_DIR, "projects", pid, "cards", cid, "v1", "final.png")
        with Image.open(final) as im:
            self.assertEqual(im.size, (1080, 1350))
        self.assertEqual(base.read_bytes(), digest)  # 그림 파일은 바뀌지 않는다
        s2 = self.state(pid)
        by = {c["id"]: c for c in s2["cards"]}
        self.assertTrue(by[cid]["finalUrl"] and by[cid]["typeset"]["ok"])
        self.assertTrue(all(c["finalUrl"] is None for c in s2["cards"] if c["id"] != cid))
        self.assertEqual(self.c.get(by[cid]["finalUrl"]).status_code, 200)
        self.assertEqual(self.c.get(self.url(pid, cid)).json()["layout"]["title"], "고친 제목")
        self.assertEqual(s2["storyboard"]["cards"], s["storyboard"]["cards"])  # 승인한 기획은 그대로

    def test_overflow_is_reported_and_bad_input_rejected(self):
        pid, s = self.reviewed()
        cid = s["cards"][0]["id"]
        lay = {"title": "아주 긴 제목 " * 12, "body": "가" * 400, "titleSize": 100, "bodySize": 60}
        r = self.c.post(self.url(pid, cid, "/preview"), json={"layout": lay})
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()
        self.assertFalse(d["ok"])
        status = {k["key"]: k["status"] for k in d["checks"]}
        self.assertEqual((status["titleFit"], status["bodyFit"], status["overlap"]), ("fail", "fail", "fail"))
        bad = self.c.post(self.url(pid, cid, "/preview"), json={"layout": {"title": "x", "body": "y", "color": "red"}})
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self.c.get(self.url(pid, "card_99")).status_code, 404)

    def test_new_image_version_has_no_stale_final(self):
        pid, s = self.reviewed()
        cid = s["cards"][0]["id"]
        lay = self.c.get(self.url(pid, cid)).json()["layout"]
        self.assertEqual(self.c.put(self.url(pid, cid), json={"layout": lay}).status_code, 200)
        st = self.state(pid)
        r = self.c.post(f"/api/projects/{pid}/cards/retry", json={"cardIds": [cid], "version": st["version"], "requestId": rid()})
        self.assertEqual(r.status_code, 202, r.text)
        s3 = self.wait(pid, lambda x: x["stage"] == "review", 60)
        c = next(x for x in s3["cards"] if x["id"] == cid)
        self.assertEqual(c["currentVersion"], 2)
        self.assertIsNone(c["finalUrl"])  # 새 그림에는 글자를 다시 올려야 한다(예전 합성본을 새 그림으로 착각하지 않게)
        self.assertTrue(Path(db.DATA_DIR, "projects", pid, "cards", cid, "v1", "final.png").is_file())  # 이전 버전은 보관


class FinalFlowTests(StoryBase):
    """카드 하나만 고치기 → 최종 승인 → 내보내기 → 내보낸 파일 확인(모의 엔진, agy 없음)."""

    def setUp(self):
        super().setUp()
        self.pid, self.s = self.reviewed()

    def reviewed(self):
        pid, _ = self.to_proposal("최종 승인 시험")
        self.make_storyboard(pid, "story")
        self.assertEqual(self.approve(pid).status_code, 202)
        return pid, self.wait(pid, lambda x: x["stage"] == "review", 60)

    def put_all(self):
        for c in self.state(self.pid)["cards"]:
            lay = self.c.get(f"/api/projects/{self.pid}/cards/{c['id']}/typeset").json()["layout"]
            self.assertEqual(self.c.put(f"/api/projects/{self.pid}/cards/{c['id']}/typeset", json={"layout": lay}).status_code, 200)

    def approve_final(self):
        s = self.state(self.pid)
        return self.c.post(f"/api/projects/{self.pid}/approve", json={"stage": "final", "version": s["version"], "requestId": rid()})

    def revise(self, cid, text):
        s = self.state(self.pid)
        r = self.c.post(f"/api/projects/{self.pid}/revisions", json={"target": "cards", "cardIds": [cid], "instruction": text,
                                                                     "version": s["version"], "requestId": rid()})
        self.assertEqual(r.status_code, 202, r.text)
        return self.wait(self.pid, lambda x: x["stage"] == "review", 60)

    def finals(self, s):
        out = {}
        for c in s["cards"]:
            p = Path(db.DATA_DIR, "projects", self.pid, "cards", c["id"], f"v{c['currentVersion']}", "final.png")
            out[c["id"]] = (c["currentVersion"], p.read_bytes() if c["finalUrl"] else None)
        return out

    def test_final_approval_needs_text_on_every_card(self):
        r = self.approve_final()
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(r.json()["error"]["code"], "final_not_ready")
        self.assertEqual(len(r.json()["error"]["detail"]["blockers"]), 7)
        self.put_all()
        r = self.approve_final()
        self.assertEqual(r.status_code, 200, r.text)
        fa = self.state(self.pid)["finalApproval"]
        self.assertTrue(fa["approved"] and fa["approvedAt"])

    def test_text_revision_keeps_image_and_other_cards_and_resets_final_approval(self):
        self.put_all()
        self.assertEqual(self.approve_final().status_code, 200)
        before = self.finals(self.state(self.pid))
        cid = "card_3"
        old_body = next(c for c in self.state(self.pid)["storyboard"]["cards"] if c["id"] == cid)["body"]
        s = self.revise(cid, "3번 카드의 본문이 길어. 핵심 조건을 유지하며 한 문장으로 줄여 줘")
        after = self.finals(s)
        self.assertEqual(after[cid][0], before[cid][0])  # 그림 버전은 그대로(그림을 다시 만들지 않았다)
        for k in before:
            if k != cid:
                self.assertEqual(after[k], before[k], k)  # 고치지 않은 카드는 그대로
        self.assertNotEqual(after[cid][1], before[cid][1])  # 고친 카드의 합성본만 바뀌었다
        fa = s["finalApproval"]
        self.assertFalse(fa["approved"])
        self.assertTrue(fa["stale"])  # 고쳤으니 최종 승인을 다시 받아야 한다
        lay = self.c.get(f"/api/projects/{self.pid}/cards/{cid}/typeset").json()["layout"]
        self.assertLess(len(lay["body"]), len(old_body))
        card = next(c for c in s["cards"] if c["id"] == cid)
        self.assertEqual(card["revisions"][-1]["kind"], "text")
        self.assertEqual(s["storyboard"]["cards"], self.s["storyboard"]["cards"])  # 승인한 스토리보드는 그대로
        self.assertEqual(self.c.post(f"/api/projects/{self.pid}/exports", json={"version": s["version"], "requestId": rid()}).status_code, 409)
        self.assertEqual(self.approve_final().status_code, 200)  # 다시 승인하면 풀린다
        self.assertTrue(self.state(self.pid)["finalApproval"]["approved"])

    def test_image_revision_remakes_only_that_card_and_recomposes_text(self):
        self.put_all()
        before = self.finals(self.state(self.pid))
        cid = "card_2"
        s = self.revise(cid, "그림이 이상해. 배경을 더 단순하게 다시 그려 줘")
        after = self.finals(s)
        self.assertEqual(after[cid][0], 2)  # 새 그림 버전
        self.assertTrue(after[cid][1])  # 저장해 둔 글자 배치를 새 그림 위에 다시 올렸다
        for k in before:
            if k != cid:
                self.assertEqual(after[k], before[k], k)
        ov = json.loads(Path(db.DATA_DIR, "projects", self.pid, "cards", cid, "overrides.json").read_text(encoding="utf-8"))
        self.assertIn("imagePlan", ov)
        self.assertTrue(Path(db.DATA_DIR, "projects", self.pid, "cards", cid, "v1", "image.png").is_file())  # 이전 그림 보관

    def test_bad_revision_requests(self):
        s = self.state(self.pid)
        for body in ({"cardIds": [], "instruction": "x"}, {"cardIds": ["card_1", "card_2"], "instruction": "x"}, {"cardIds": ["card_1"], "instruction": "  "}):
            r = self.c.post(f"/api/projects/{self.pid}/revisions", json={"target": "cards", "version": s["version"], "requestId": rid(), **body})
            self.assertEqual(r.status_code, 400, (body, r.text))
        r = self.c.post(f"/api/projects/{self.pid}/revisions", json={"target": "cards", "cardIds": ["card_99"], "instruction": "x", "version": s["version"], "requestId": rid()})
        self.assertEqual(r.status_code, 400)

    def export(self):
        s = self.state(self.pid)
        r = self.c.post(f"/api/projects/{self.pid}/exports", json={"version": s["version"], "requestId": rid()})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def test_export_files_download_and_verification_never_passes_unchecked(self):
        import io
        import zipfile
        self.assertEqual(self.c.post(f"/api/projects/{self.pid}/exports", json={"version": self.state(self.pid)["version"], "requestId": rid()}).status_code, 409)
        self.put_all()
        self.assertEqual(self.approve_final().status_code, 200)
        res = self.export()
        names = {f["name"] for f in res["files"]}
        self.assertTrue({f"cards/card_{i:02d}.png" for i in range(1, 8)} <= names)
        self.assertTrue({"cardnews.zip", "manifest.json", "sources.json", "sources.md", "image_usage.json", "image_usage.md", "verification.json"} <= names)
        base = f"/api/projects/{self.pid}/exports/{res['exportId']}/"
        z = self.c.get(base + "cardnews.zip")
        self.assertEqual(z.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(z.content)) as zf:
            self.assertIn("cards/card_03.png", zf.namelist())
            self.assertIn("sources.md", zf.namelist())
        md = self.c.get(base + "sources.md").text
        self.assertIn("후보 조사 단계의 메모", md)  # 후보 단계의 옛 메모는 그렇게 표시한다(심층 조사 뒤 결과와 혼동되지 않게)
        self.assertNotIn("불확실한 점:", md)
        from app import export as _ex
        shown = _ex._sources_md({"topic": "t", "researchBasis": "b", "windowFrom": "f", "windowTo": "t", "windowDays": 7, "limits": [],
                                 "selectedNews": [{"title": "N", "sources": [{"url": "https://a.example/x"}], "candidateStageNote": "원문을 못 읽었다"}],
                                 "readOriginals": ["https://github.com/o/r"], "cards": []})
        self.assertIn("## 심층 조사에서 직접 읽어 확인한 원문", shown)
        self.assertIn("- https://github.com/o/r", shown)
        png = self.c.get(base + "cards/card_01.png")
        self.assertEqual(png.content[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(self.c.get(base + "../app.db").status_code, 404)
        self.assertEqual(self.c.get(base + "nothing.txt").status_code, 404)
        v = res["verification"]
        by = {(i["category"], i["key"]): i for i in v["items"]}
        self.assertEqual(by[("누락", "cardFiles")]["status"], "pass")
        self.assertEqual(by[("누락", "zip")]["status"], "pass")
        self.assertEqual(by[("글자 잘림", "reproduce")]["status"], "pass")
        self.assertEqual(by[("출처", "contentMatch")]["status"], "unchecked")  # 출처 내용 대조는 못 했으니 통과가 아니다
        self.assertEqual(by[("글자 잘림", "eyes")]["status"], "unchecked")
        self.assertTrue(by[("출처", "contentMatch")]["userAction"])
        self.assertEqual(by[("날짜", "pageDate")]["status"], "unchecked")
        self.assertFalse(v["allPassed"])
        self.assertEqual(v["summary"]["fail"], 0, [i for i in v["items"] if i["status"] == "fail"])
        # 내보낸 파일을 망가뜨리면 다시 확인할 때 실패로 잡는다
        from app import export as export_mod
        target = Path(db.DATA_DIR, "projects", self.pid, "exports", res["exportId"], "cards", "card_04.png")
        target.write_bytes(b"broken")
        v2 = export_mod.verify_export(self.pid, res["exportId"])
        self.assertEqual({(i["category"], i["key"]): i["status"] for i in v2["items"]}[("누락", "cardFiles")], "fail")
        target.unlink()
        v3 = export_mod.verify_export(self.pid, res["exportId"])
        self.assertIn("card_04.png 없음", {(i["category"], i["key"]): i for i in v3["items"]}[("누락", "cardFiles")]["message"])


class TypesetLayoutTests(unittest.TestCase):
    """글자 줄바꿈과 띠 겹침 검사(Pillow만 쓴다. agy·Claude 호출 없음)."""

    def fonts(self, size, bold=False):
        from PIL import ImageFont
        from app import typeset
        reg, b = typeset.find_fonts()
        return ImageFont.truetype(b if bold else reg, size)

    def test_number_and_unit_are_not_split_across_lines(self):
        from app import typeset
        f = self.fonts(46)
        text = "글을 넣으면 그림을 만드는 '텍스트-이미지' 모델을 묶은 것이 모델군이다. 앞부분을 바탕으로 다음 조각을 이어 가는 자기회귀 방식이며, 1억 쌍 데이터셋 GPIC로 학습했다."
        lines = typeset.wrap(text, f, 864)
        self.assertFalse(any(l.endswith("1억") for l in lines), lines)
        self.assertTrue(any("1억 쌍" in l for l in lines), lines)
        self.assertEqual(" ".join(lines).replace("  ", " "), text)  # 글자가 사라지거나 바뀌지 않는다

    def test_title_breaks_at_colon_when_both_parts_fit(self):
        from app import typeset
        f = self.fonts(86, bold=True)
        self.assertEqual(typeset.balance_title("최신 AI: 언어 학습 없는 Halcyon", f, 936), ["최신 AI:", "언어 학습 없는 Halcyon"])
        # 콜론 앞뒤가 한 줄에 안 들어가면 고르게 나눈다
        lines = typeset.balance_title("테스트 제목이 아주 길어서 두 줄로 나뉘는 경우입니다 정말로", f, 936)
        self.assertEqual(" ".join(lines), "테스트 제목이 아주 길어서 두 줄로 나뉘는 경우입니다 정말로")

    def test_band_over_picture_is_warned_but_not_failed(self):
        import time
        from app import typeset
        with tempfile.TemporaryDirectory() as t:
            base = Path(t, "b.png")
            im = Image.new("RGB", (1080, 1350), (230, 240, 235))
            for y in range(900, 1100):  # 그림이 띠 자리까지 내려와 글자를 줄여도 피할 수 없는 경우
                for x in range(200, 880):
                    im.putpixel((x, y), (20, 40, 60) if (x // 20) % 2 else (200, 120, 50))
            im.save(base)
            r = typeset.compose(base, {"title": "제목", "body": "본문 " * 40}, Path(t, "o.png"))
            by = {c["key"]: c["status"] for c in r["checks"]}
            self.assertEqual(by["bandOverlap"], "warn")
            self.assertTrue(r["ok"])  # 경고일 뿐 저장·승인을 막지 않는다
            clean = Image.new("RGB", (1080, 1350), (230, 240, 235))
            clean.save(base)
            r2 = typeset.compose(base, {"title": "제목", "body": "본문 " * 40}, Path(t, "o2.png"))
            self.assertEqual({c["key"]: c["status"] for c in r2["checks"]}["bandOverlap"], "pass")

    def test_auto_body_size_shrinks_to_clear_the_picture(self):
        from app import typeset
        with tempfile.TemporaryDirectory() as tdir:
            base = Path(tdir, "b.png")
            im = Image.new("RGB", (1080, 1350), (230, 240, 235))
            for y in range(500, 930):  # 그림이 y 930까지: 큰 글자 5줄이면 띠가 그림을 가린다
                for x in range(300, 780):
                    im.putpixel((x, y), (20, 40, 60) if (x // 20) % 2 else (200, 120, 50))
            im.save(base)
            body = "본문 글자가 다섯 줄이 되도록 조금 길게 써 보는 문장입니다. " * 3
            r = typeset.compose(base, {"title": "제목", "body": body}, Path(tdir, "o.png"))
            by = {c["key"]: c["status"] for c in r["checks"]}
            self.assertEqual(by["bandOverlap"], "pass")
            self.assertEqual(by.get("bodyAutoSize"), "pass")
            self.assertLess(r["used"]["bodySize"], 46)
            self.assertGreaterEqual(r["used"]["bodySize"], 36)
            # 크기를 사용자가 정하면 그 크기를 존중한다(자동 조정하지 않음)
            r2 = typeset.compose(base, {"title": "제목", "body": body, "bodySize": 46}, Path(tdir, "o2.png"))
            self.assertEqual(r2["used"]["bodySize"], 46)


class RestoreImageTests(FinalFlowTests):
    """이전 그림으로 되돌리기(새로 만들지 않는다). FinalFlowTests의 준비 단계를 재사용한다."""

    def restore(self, cid, ver):
        s = self.state(self.pid)
        return self.c.post(f"/api/projects/{self.pid}/cards/{cid}/restore", json={"imageVersion": ver, "version": s["version"], "requestId": rid()})

    def test_restore_previous_image_keeps_others_and_resets_approval(self):
        self.put_all()
        cid = "card_2"
        s = self.revise(cid, "그림이 이상해. 배경을 더 단순하게 다시 그려 줘")  # v2가 생긴다
        card = next(c for c in s["cards"] if c["id"] == cid)
        self.assertEqual([v["version"] for v in card["versions"]], [1, 2])
        self.assertTrue(all(v["imageUrl"] for v in card["versions"]))
        self.assertEqual(self.approve_final().status_code, 200)
        before = self.finals(self.state(self.pid))
        runs_before = db_count("select count(*) from runs where project_id=?", self.pid)
        r = self.restore(cid, 1)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(db_count("select count(*) from runs where project_id=?", self.pid), runs_before)  # 새 작업(엔진 호출)이 없다
        s2 = self.state(self.pid)
        after = self.finals(s2)
        self.assertEqual(after[cid][0], 1)
        self.assertTrue(after[cid][1])  # v1 그림 위에 저장해 둔 글자를 다시 올렸다(모의 그림은 버전이 달라도 같아서 내용 비교는 하지 않는다)
        for k in before:
            if k != cid:
                self.assertEqual(after[k], before[k], k)
        self.assertFalse(s2["finalApproval"]["approved"])  # 합성본이 바뀌어 최종 승인이 풀린다
        card2 = next(c for c in s2["cards"] if c["id"] == cid)
        self.assertEqual(card2["revisions"][-1]["kind"], "restore")
        self.assertEqual([v["current"] for v in card2["versions"]], [True, False])
        # 되돌린 뒤 다시 만들면 기존 번호와 겹치지 않는 새 번호(v3)가 된다
        s3 = self.revise(cid, "그림이 이상해. 색을 더 밝게 다시 그려 줘")
        card3 = next(c for c in s3["cards"] if c["id"] == cid)
        self.assertEqual((card3["currentVersion"], [v["version"] for v in card3["versions"]]), (3, [1, 2, 3]))

    def test_restore_rejects_bad_requests(self):
        self.put_all()
        cid = "card_2"
        self.assertEqual(self.restore(cid, 1).status_code, 400)  # 이미 쓰고 있는 그림
        self.assertEqual(self.restore(cid, 9).status_code, 400)  # 없는 버전
        self.assertEqual(self.restore("card_99", 1).status_code, 404)


class ClaudeEditTests(StoryBase):
    """그림 수정 요청: 기존 SVG를 보여 주고 요청한 부분만 고치게 한다(처음부터 새로 그리지 않는다)."""
    engine_name = "claude-cli"
    agy_on = False
    claude_calls = ClaudeAgyTests.claude_calls
    flow = ClaudeSvgTests.flow
    svg_calls = ClaudeSvgTests.svg_calls

    def revise(self, pid, cid, text):
        s = self.state(pid)
        r = self.c.post(f"/api/projects/{pid}/revisions", json={"target": "cards", "cardIds": [cid], "instruction": text,
                                                               "version": s["version"], "requestId": rid()})
        self.assertEqual(r.status_code, 202, r.text)
        return self.wait(pid, lambda x: x["stage"] == "review", 120)

    def test_image_revision_shows_previous_svg_and_edits_it(self):
        pid, s = self.flow("SVG 수정 시험")
        before_svg = len(self.svg_calls())
        v1 = Path(db.DATA_DIR, "projects", pid, "cards", "card_3", "v1", "art.svg").read_text(encoding="utf-8")
        s2 = self.revise(pid, "card_3", "그림이 이상해, 상자 모서리만 정리해 줘. 나머지는 그대로 둬.")
        calls = self.svg_calls()
        self.assertEqual(len(calls), before_svg + 1)  # 이 카드 하나만 다시 그렸다
        p = calls[-1]["prompt"]
        self.assertIn("이미 있는 그림을 사용자의 요청만큼만 고치는 것", p)
        self.assertIn("상자 모서리만 정리해 줘", p)
        self.assertIn('<rect x="200" y="620"', p)  # 지금 그림의 SVG 조각이 프롬프트에 들어 있다
        card = next(c for c in s2["cards"] if c["id"] == "card_3")
        self.assertEqual((card["currentVersion"], card["status"]), (2, "ready"))
        self.assertTrue(Path(db.DATA_DIR, "projects", pid, "cards", "card_3", "v2", "art.svg").is_file())
        self.assertTrue(Path(db.DATA_DIR, "projects", pid, "cards", "card_3", "v1", "art.svg").read_text(encoding="utf-8") == v1)  # 이전 그림 보관
        # 수정 방식은 한 번만 쓴다: 그냥 다시 만들기는 처음부터 그린다
        st = self.state(pid)
        r = self.c.post(f"/api/projects/{pid}/cards/retry", json={"cardIds": ["card_3"], "version": st["version"], "requestId": rid()})
        self.assertEqual(r.status_code, 202, r.text)
        self.wait(pid, lambda x: x["stage"] == "review", 120)
        self.assertNotIn("이미 있는 그림을 사용자의 요청만큼만 고치는 것", self.svg_calls()[-1]["prompt"])
        self.assertNotIn("edit", json.loads(Path(db.DATA_DIR, "projects", pid, "cards", "card_3", "overrides.json").read_text(encoding="utf-8")))


class FreshnessTests(unittest.TestCase):
    """조사 뒤 지난 시간은 달력 날짜가 아니라 실제 시간으로 본다(자정을 넘겨도 몇 시간이면 문제없다)."""

    def age_status(self, created_at):
        from app import export as ex
        m = {"projectId": "prj_none", "exportId": "ex_none", "createdAt": created_at, "freshness": {"titleMayUseLatest": True},
             "research": {"basis": "2026-10-23T21:17:04+09:00", "from": "2026-10-16", "to": "2026-10-23"}}
        return {i["key"]: i["status"] for i in ex._check_dates(m, [])}["age"]

    def test_crossing_midnight_within_a_day_is_fresh(self):
        self.assertEqual(self.age_status("2026-10-24T00:17:29+09:00"), "pass")  # 3시간 뒤, 날짜만 바뀜
        self.assertEqual(self.age_status("2026-10-24T21:00:00+09:00"), "pass")  # 24시간 이내

    def test_more_than_a_day_needs_a_recheck(self):
        self.assertEqual(self.age_status("2026-10-24T22:00:00+09:00"), "unchecked")
        self.assertEqual(self.age_status("2026-10-26T10:00:00+09:00"), "unchecked")
