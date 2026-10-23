"""검수 흐름 시험: 가짜 claude(tests/fake_claude.py)로 분석 → 선택 → 수정 → 재검수 → 산출물을 확인한다. 실제 모델을 부르지 않는다."""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
_TMP = tempfile.TemporaryDirectory()
os.environ["RV_DATA_DIR"] = _TMP.name  # 사용자 data 폴더를 건드리지 않는다
os.environ["RV_CLAUDE_CMD"] = json.dumps([sys.executable, str(HERE / "fake_claude.py")])

from fastapi.testclient import TestClient  # noqa: E402

from app import db, doc  # noqa: E402
from app.main import ENGINE, app  # noqa: E402

BODY = "서론입니다. 이 글은 청년 문제를 다룹니다.\n\n청년 실업은 계속 늘고 있다. 그래서 문제가 심각하다.\n\n그러므로 정부가 책임져야 한다. 끝."


class Base(unittest.TestCase):
    def setUp(self):
        self.log = os.path.join(_TMP.name, "fake.jsonl")
        Path(self.log).unlink(missing_ok=True)
        os.environ["FAKE_LOG"] = self.log
        os.environ.pop("FAKE_MODE", None)
        self.client = TestClient(app, base_url="http://127.0.0.1")
        self.client.__enter__()
        self.c = self.client

    def tearDown(self):
        ENGINE.shutdown()
        self.client.__exit__(None, None, None)

    def calls(self):
        p = Path(self.log)
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []

    def state(self, pid):
        return self.c.get(f"/api/projects/{pid}").json()

    def wait(self, pid, pred, timeout=30):
        t0 = time.time()
        while time.time() - t0 < timeout:
            s = self.state(pid)
            if pred(s):
                return s
            time.sleep(0.2)
        self.fail(f"시간 안에 조건에 도달하지 못했다: {self.state(pid)['stage']} {self.state(pid)['currentRun']}")

    def create(self, body=BODY, title="청년 실업", cond="1000자, 보고서"):
        r = self.c.post("/api/projects", json={"title": title, "conditions": cond, "body": body})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["projectId"]

    def analyzed(self, **kw):
        pid = self.create(**kw)
        return pid, self.wait(pid, lambda s: s["stage"] in ("findings_ready", "created"))

    def revise(self, pid, choices):
        self.assertEqual(self.c.put(f"/api/projects/{pid}/choices", json={"choices": choices}).status_code, 200)
        v = self.state(pid)["version"]
        r = self.c.post(f"/api/projects/{pid}/revise", json={"version": v})
        self.assertEqual(r.status_code, 202, r.text)
        return self.wait(pid, lambda s: s["stage"] in ("done", "findings_ready"))


class DocTests(unittest.TestCase):
    def test_paragraphs_and_quote_validation(self):
        ps = doc.split_paragraphs("가나다.\r\n\r\n\n라마바.\n\n  \n사아자.")
        self.assertEqual([p["id"] for p in ps], ["P1", "P2", "P3"])
        good = {"findings": [{"paragraphId": "P1", "category": "논리", "quote": "가나다", "problem": "p", "why": "w", "recommended": "b",
                              "directions": [{"label": "A", "detail": "a", "needsSearch": False}, {"label": "B", "detail": "b", "needsSearch": True}]}]}
        out = doc.validate_findings(good, ps)
        self.assertEqual((out[0]["id"], out[0]["recommended"], [d["id"] for d in out[0]["directions"]]), ("f1", "b", ["a", "b"]))
        bad = json.loads(json.dumps(good))
        bad["findings"][0]["quote"] = "글에 없는 말"
        with self.assertRaises(doc.FormatError):
            doc.validate_findings(bad, ps)
        bad["findings"][0].update(quote="", paragraphId="P9")
        with self.assertRaises(doc.FormatError):
            doc.validate_findings(bad, ps)

    def test_revision_validation_and_untouched(self):
        norm = lambda u: u  # noqa: E731
        ok = doc.validate_revision({"status": "revised", "newText": "비율은 6.1%[1]", "gap": "",
                                    "sources": [{"url": "https://a.example/x", "title": "t", "quote": "6.1%"}]}, "원문", {"https://a.example/x"}, True, norm)
        self.assertEqual((ok["status"], ok["warnings"]), ("revised", []))
        fresh = doc.validate_revision({"status": "revised", "newText": "비율은 9.9%[1]", "gap": "",
                                       "sources": [{"url": "https://a.example/x", "title": "t", "quote": "6.1%"}]}, "원문", {"https://a.example/x"}, True, norm)
        self.assertTrue(any("9.9" in w for w in fresh["warnings"]))
        with self.assertRaises(doc.FormatError):
            doc.validate_revision({"status": "revised", "newText": "x[2]", "sources": [{"url": "https://a.example/x"}]}, "원문", {"https://a.example/x"}, True, norm)
        with self.assertRaises(doc.FormatError):
            doc.validate_revision({"status": "revised", "newText": "x[1]", "sources": [{"url": "https://made.up/x"}]}, "원문", {"https://a.example/x"}, True, norm)
        ps = [{"id": "P1", "text": "a"}, {"id": "P2", "text": "b"}]
        after = doc.apply_revisions(ps, {"P2": {"text": "b2", "gap": "자료 없음"}})
        self.assertEqual(after[0]["text"], "a")
        self.assertIn("⚠[자료 보강 필요: 자료 없음]", after[1]["text"])
        self.assertEqual(doc.untouched_ok(ps, after, {"P2"}), [])
        self.assertEqual(doc.untouched_ok(ps, [{"id": "P1", "text": "X"}, after[1]], {"P2"}), ["P1"])


class DocDisplayTests(unittest.TestCase):
    """긴 글 처리와 변경 표시(마크다운)."""

    def test_no_blank_lines_falls_back_to_line_split_and_long_text_is_not_cut_mid_sentence(self):
        self.assertEqual([p["text"] for p in doc.split_paragraphs("가\n나\n다")], ["가", "나", "다"])
        self.assertEqual(len(doc.split_paragraphs("가\n\n나\n다")), 2)  # 빈 줄이 있으면 그 기준을 따른다
        long = "첫째 문장은 근거가 필요하다. " * 10
        out = doc._s(long, 80)
        self.assertLessEqual(len(out), 82)
        self.assertTrue(out.endswith("…"))
        self.assertTrue(out.startswith("첫째 문장은 근거가 필요하다."))
        self.assertEqual(doc._s("짧은 글", 80), "짧은 글")
        self.assertEqual(doc._s("가" * 50, 20, smart=False), "가" * 20)  # 인용문은 …를 붙이지 않는다

    def test_hard_wrapped_text_with_indented_paragraphs_keeps_sentences_together(self):
        text = "\t첫 문단의 시작이다. 첫 문단 끝이다.\n\t둘째 문단은 이렇게 시작하는데 (링크)\n라는 표현으로 이어지는 같은 문장이다.\n\t셋째 문단이다."
        ps = [p["text"] for p in doc.split_paragraphs(text)]
        self.assertEqual(len(ps), 3)
        self.assertIn("(링크) 라는 표현으로 이어지는 같은 문장이다.", ps[1])  # 문장 중간에서 끊긴 줄은 앞 문단에 붙는다
        self.assertEqual(len(doc.split_paragraphs("가\n나\n다")), 3)  # 들여쓰기가 없으면 줄마다 나눈다(이전 동작)

    def test_heavy_rewrite_diff_is_shown_as_replacement_not_interleaved(self):
        before = "라는 표현으로 세속과 유리된 느낌을 주는 반면 홍매도에서는 매화를 표현하지 않았다는 것이다."
        after = "라는 표현으로 세속과 유리된 느낌을 주는 반면 홍매도는 위에서 아래로 힘차게 뻗은 줄기와 촘촘한 꽃잎으로 매화를 눈앞에 보여 주고 제발을 적어 상황을 남겼다. 이 차이가 두 작품의 성격을 가른다. " + "그림 왼편 여백에는 제작 경위를 밝힌 제발이 있어 작품이 만들어진 상황을 화면 안에 남겼다. " * 4
        ops = doc.diff_ops(before, after)
        kinds = [k for k, _ in ops]
        self.assertEqual(kinds.count("del"), 1)  # 뒤섞인 여러 조각이 아니라 한 덩어리 교체
        self.assertEqual(kinds.count("ins"), 1)
        self.assertEqual("".join(t for k, t in ops if k != "del"), after)
        self.assertEqual("".join(t for k, t in ops if k != "ins"), before)

    def test_each_held_item_gets_its_own_line_even_in_older_joined_markers(self):
        ps = [{"id": "P1", "text": "본문. ⚠[자료 보강 필요: 첫째 내용 (필요한 자료: 자료 하나); 둘째 내용 (필요한 자료: 자료 둘; 셋)]"},
              {"id": "P2", "text": "다른 문단. ⚠[자료 보강 필요: 셋째 내용 (필요한 자료: 자료 셋)]"}]
        gaps = doc.gaps_in(ps)
        self.assertEqual(len(gaps), 3)
        self.assertEqual(gaps[0], "P1: 첫째 내용 (필요한 자료: 자료 하나)")
        self.assertTrue(gaps[1].startswith("P1: 둘째 내용") and "자료 둘; 셋" in gaps[1])  # 필요한 자료 안의 세미콜론은 나누지 않는다
        self.assertEqual(gaps[2][:3], "P2:")

    def test_markdown_diff_marks_deletions_and_additions_and_collapses_long_equal_text(self):
        ops = doc.collapse_ops(doc.diff_ops("청년 실업은 늘고 있다", "청년 실업률은 6.1%였다"))
        md = doc.ops_to_markdown(ops)
        self.assertIn("~~실업은~~", md)
        self.assertIn("**실업률은**", md)
        long_before = "같은 문장입니다. " * 60 + "끝"
        ops2 = doc.collapse_ops(doc.diff_ops(long_before, long_before + " ⚠[자료 보강 필요: 대조]"))
        self.assertTrue(any(k == "skip" for k, _ in ops2))
        self.assertLess(sum(len(t) for k, t in ops2 if k != "skip"), 300)  # 긴 글도 바뀐 곳 위주로 짧게
        self.assertIn("**⚠[자료 보강 필요: 대조]**", doc.ops_to_markdown(ops2))


class FlowTests(Base):
    def test_analyze_gives_prioritized_findings_with_real_quotes(self):
        pid, s = self.analyzed()
        self.assertEqual(s["stage"], "findings_ready")
        f = s["findings"]
        self.assertEqual([x["priority"] for x in f], [1, 2])
        for x in f:
            para = next(p["text"] for p in s["paragraphs"] if p["id"] == x["paragraphId"])
            self.assertIn(x["quote"], para)  # 지적의 인용문은 글에 실제로 있다
            self.assertEqual(len(x["directions"]), 2)
        self.assertEqual(self.calls()[0]["tools"], "")  # 분석은 도구 없이
        self.assertIn("1000자, 보고서", self.calls()[0]["prompt"])  # 입력한 조건이 모델에 전달됐다

    def test_bad_quote_is_repaired_once_then_fails_with_reason(self):
        os.environ["FAKE_MODE"] = "quote_bad"
        pid, s = self.analyzed()
        self.assertEqual(s["stage"], "created")  # 모델이 같은 잘못을 반복하면 완료로 치지 않는다
        self.assertEqual(s["currentRun"]["status"], "failed")
        self.assertEqual(s["currentRun"]["error"]["code"], "engine_failed")
        self.assertEqual(len(self.calls()), 2)  # 고쳐 쓰기는 한 번만

    def test_revision_with_search_changes_only_chosen_paragraph_and_writes_files(self):
        pid, s = self.analyzed()
        before = {p["id"]: p["text"] for p in s["paragraphs"]}
        done = self.revise(pid, {"f1": "a", "f2": "skip"})
        self.assertEqual(done["stage"], "done")
        r = done["result"]
        by = {i["finding"]["id"]: i for i in r["items"]}
        self.assertEqual((by["f1"]["verdict"], by["f2"]["verdict"]), ("resolved", "skipped"))
        self.assertEqual(r["checks"], {"untouchedChanged": [], "changed": ["P2"]})
        self.assertIn("6.1%", by["f1"]["after"])
        self.assertIn("[1]", by["f1"]["after"])
        self.assertEqual(r["sources"][0]["url"], "https://kostat.example.org/stat/youth")
        base = db.DATA_DIR / "projects" / pid / "outputs"
        final = (base / "final.md").read_text(encoding="utf-8")
        for pid_, text in before.items():
            if pid_ != "P2":
                self.assertIn(text, final)  # 고르지 않은 문단은 그대로
        self.assertIn("## 참고 자료", final)
        report = (base / "review_report.md").read_text(encoding="utf-8")
        self.assertIn("**변경 표시**", report)
        self.assertIn("~~", report)  # 삭제 표시(취소선)
        self.assertIn("**실업률은**", report)  # 추가 표시(굵게)
        self.assertTrue(done["result"]["items"][0]["diff"])  # 화면용 변경 표시도 상태에 들어 있다
        self.assertIn("해결", report)
        self.assertIn("건너뜀", report)
        self.assertEqual(self.c.get(f"/api/projects/{pid}/outputs/final.md").status_code, 200)
        self.assertEqual(self.c.get(f"/api/projects/{pid}/outputs/../app.db").status_code, 404)
        tools = [c["tools"] for c in self.calls()]
        self.assertEqual(tools, ["", "WebSearch,WebFetch", ""])  # 분석(도구 없음) → 수정(검색) → 재검수(도구 없음)

    def test_no_search_direction_does_not_use_tools_and_custom_direction_searches(self):
        pid, s = self.analyzed()
        done = self.revise(pid, {"f2": "a"})
        self.assertEqual([c["tools"] for c in self.calls()], ["", "", ""])
        self.assertEqual(done["result"]["checks"]["changed"], ["P3"])
        pid2, _ = self.analyzed()
        done2 = self.revise(pid2, {"f1": {"custom": "통계청 자료로 뒷받침"}})
        self.assertEqual(done2["result"]["items"][0]["verdict"], "resolved")

    def test_insufficient_material_is_marked_not_completed(self):
        os.environ["FAKE_MODE"] = "insufficient"
        pid, _ = self.analyzed()
        done = self.revise(pid, {"f1": "a"})
        it = done["result"]["items"][0]
        self.assertEqual(it["verdict"], "insufficient")
        self.assertIn("⚠[자료 보강 필요: 청년 실업이 늘고 있다는 공식 수치 (필요한 자료: 통계청 경제활동인구조사의 청년층(15~29세) 실업률 최근 5년 추이)]", it["after"])
        self.assertEqual(it["held"][0]["needed"][:3], "통계청")  # 추가로 필요한 자료가 구체적으로 남는다
        self.assertFalse(done["result"]["remaining"]["usable"])
        self.assertEqual(done["result"]["remaining"]["held"], 1)
        self.assertTrue(done["result"]["gaps"])
        self.assertIn("자료 보강이 필요한 부분", (db.DATA_DIR / "projects" / pid / "outputs" / "final.md").read_text(encoding="utf-8"))
        self.assertEqual(len(self.calls()), 2)  # 재검수 호출 없음(고친 게 없으니)

    def test_invented_source_is_rejected(self):
        os.environ["FAKE_MODE"] = "unknown_url"
        pid, _ = self.analyzed()
        s = self.revise(pid, {"f1": "a"})
        self.assertEqual(s["stage"], "findings_ready")  # 완료로 표시하지 않고 선택 단계로 돌아온다
        self.assertEqual(s["currentRun"]["status"], "failed")
        self.assertIn("검색·열람 기록", s["currentRun"]["error"]["message"])

    def test_search_unavailable_and_login_required_show_reason_and_retry(self):
        pid, _ = self.analyzed()
        os.environ["FAKE_MODE"] = "no_search"
        s = self.revise(pid, {"f1": "a"})
        self.assertEqual((s["stage"], s["currentRun"]["error"]["code"]), ("findings_ready", "search_unavailable"))
        os.environ["FAKE_MODE"] = "ok"  # 재시도: 같은 선택이 남아 있고 다시 시작할 수 있다
        self.assertEqual(s["choices"], {"f1": "a"})
        r = self.c.post(f"/api/projects/{pid}/revise", json={"version": s["version"]})
        self.assertEqual(r.status_code, 202)
        self.assertEqual(self.wait(pid, lambda x: x["stage"] == "done")["stage"], "done")
        os.environ["FAKE_MODE"] = "login"
        pid2, s2 = self.analyzed()
        self.assertEqual(s2["currentRun"]["error"]["code"], "auth_required")
        os.environ["FAKE_MODE"] = "ok"
        self.assertEqual(self.c.post(f"/api/projects/{pid2}/analyze", json={"version": s2["version"]}).status_code, 202)
        self.assertEqual(self.wait(pid2, lambda x: x["stage"] == "findings_ready")["stage"], "findings_ready")

    def test_verify_partial_is_reported_honestly(self):
        os.environ["FAKE_MODE"] = "verify_partial"
        pid, _ = self.analyzed()
        done = self.revise(pid, {"f2": "a"})
        self.assertEqual(done["result"]["items"][1]["verdict"], "partial")
        self.assertIn("일부 해결", (db.DATA_DIR / "projects" / pid / "outputs" / "review_report.md").read_text(encoding="utf-8"))

    def test_guards_and_persistence(self):
        pid, s = self.analyzed()
        r = self.c.post(f"/api/projects/{pid}/revise", json={"version": s["version"]})  # 아무것도 안 골랐다
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.c.put(f"/api/projects/{pid}/choices", json={"choices": {"f1": "a", "f9": "a", "f2": "zzz"}}).json()["choices"], {"f1": "a", "f2": "skip"})
        self.assertEqual(self.c.post(f"/api/projects/{pid}/revise", json={"version": 999}).status_code, 409)
        self.assertEqual(self.c.post("/api/projects", json={"title": "x", "body": "  \n\n "}).status_code, 400)
        again = self.state(pid)  # 새로고침해도 입력·지적·선택이 그대로
        self.assertEqual((again["findings"], again["choices"]), (s["findings"], {"f1": "a", "f2": "skip"}))
        self.assertEqual(self.c.get(f"/api/projects/{pid}/outputs/final.md").status_code, 404)  # 끝나기 전에는 파일이 없다

    # --- 재검수 개선(A)과 라운드(B) ---
    def next_round(self, pid, refs):
        return self.c.post(f"/api/projects/{pid}/next-round", json={"version": self.state(pid)["version"], "issues": refs})

    def test_verify_sees_sources_and_new_issues_have_severity_and_directions(self):
        pid, _ = self.analyzed()
        done = self.revise(pid, {"f1": "a"})
        verify = [c for c in self.calls() if "다시 검수하는 편집자" in c["prompt"]][0]["prompt"]
        self.assertIn("kostat.example.org", verify)  # 재검수가 쓴 출처를 본다(출처 표기 없다는 오탐을 막는다)
        self.assertIn("기계 검사의 확인 필요", verify)
        self.assertIn("severity", verify)
        self.assertIn("일부러 남긴 것이다", verify)  # 보류 표시(⚠)를 문제로 삼지 않게 알려 준다
        issues = done["result"]["items"][0]["newIssues"]
        self.assertEqual([(i["severity"], "directions" in i) for i in issues], [("high", True), ("low", False)])  # 낮음은 방향 없이 참고용
        self.assertEqual(len(issues[0]["directions"]), 2)
        rem = done["result"]["remaining"]
        self.assertEqual((rem["newIssues"], rem["usable"]), ({"high": 1, "medium": 0, "low": 1}, False))
        self.assertTrue(done["result"]["finalParagraphs"])

    def test_next_round_continues_from_revised_text_and_keeps_numbering(self):
        pid, s1 = self.analyzed()
        done = self.revise(pid, {"f1": "a"})
        self.assertEqual(done["round"], 1)
        # 낮음·없는 번호는 이을 수 없다
        self.assertEqual(self.next_round(pid, ["f1.2"]).status_code, 400)
        self.assertEqual(self.next_round(pid, ["f9.1"]).status_code, 400)
        self.assertEqual(self.next_round(pid, []).status_code, 400)
        self.assertEqual(self.c.post(f"/api/projects/{pid}/next-round", json={"version": 1, "issues": ["f1.1"]}).status_code, 409)  # 오래된 화면
        r = self.next_round(pid, ["f1.1"])
        self.assertEqual(r.status_code, 200, r.text)
        s2 = self.state(pid)
        self.assertEqual((s2["stage"], s2["round"], len(s2["history"])), ("findings_ready", 2, 1))
        f = s2["findings"][0]
        self.assertEqual((f["id"], f["category"], f["paragraphId"], f["severity"]), ("f1", "새 문제", "P2", "high"))
        self.assertIn("추세 자료 추가", f["directions"][0]["label"])
        self.assertIn("6.1%", next(p["text"] for p in s2["paragraphs"] if p["id"] == "P2"))  # 고친 글에서 이어 간다
        self.assertEqual(s2["history"][0]["result"]["items"][0]["verdict"], "resolved")
        # 라운드 2: 자료를 또 찾는 방향(a). 같은 자료면 번호를 다시 쓰고 글 속 [1]은 그대로
        self.c.put(f"/api/projects/{pid}/choices", json={"choices": {"f1": "a"}})
        done2 = self.revise(pid, {"f1": "a"})
        self.assertEqual((done2["stage"], done2["round"]), ("done", 2))
        self.assertEqual(len(done2["result"]["sources"]), 1)
        final = (db.DATA_DIR / "projects" / pid / "outputs" / "final.md").read_text(encoding="utf-8")
        self.assertIn("[1]", final)
        self.assertEqual(final.count("## 참고 자료"), 1)
        self.assertEqual(done2["result"]["items"][0]["newIssues"], [])  # 이어서 고친 뒤에는 새 문제가 사라진다
        self.assertEqual((done2["result"]["remaining"]["held"], done2["result"]["remaining"]["insufficient"]), (0, 0))  # 보류가 풀렸다
        report = (db.DATA_DIR / "projects" / pid / "outputs" / "review_report.md").read_text(encoding="utf-8")
        self.assertIn("## 라운드 1", report)
        self.assertIn("## 라운드 2", report)
        self.assertIn("새로 생긴 문제(중요도 높음)", report)
        self.assertEqual(self.c.get(f"/api/projects/{pid}/outputs/review_report.md").status_code, 200)

    def test_new_sources_continue_numbering_across_rounds(self):
        pid, _ = self.analyzed()
        self.revise(pid, {"f1": "a"})
        self.assertEqual(self.next_round(pid, ["f1.1"]).status_code, 200)
        with db.tx() as c:  # 라운드 2에서는 다른 주소의 자료를 쓰는 상황을 만든다(번호가 [2]로 이어지는지)
            sources = json.loads(c.execute("SELECT sources_json FROM projects WHERE id=?", (pid,)).fetchone()[0])
            self.assertEqual([s["n"] for s in sources], [1])
        norm = lambda u: u  # noqa: E731
        ok = doc.validate_revision({"status": "revised", "newText": "앞 [1] 그리고 새 근거[2]", "gap": "",
                                    "sources": [{"url": "https://b.example/y", "title": "t", "quote": "q"}]},
                                   "앞 [1]", {"https://b.example/y"}, True, norm, 1)
        self.assertEqual(ok["status"], "revised")
        with self.assertRaises(doc.FormatError):  # offset 뒤 번호가 아니거나 없는 번호
            doc.validate_revision({"status": "revised", "newText": "새 근거[1] 그리고 [4]", "sources": [{"url": "https://b.example/y"}]},
                                  "앞", {"https://b.example/y"}, True, norm, 1)

    def test_round_limit_and_wrong_stage(self):
        pid, _ = self.analyzed()
        self.assertEqual(self.next_round(pid, ["f1.1"]).status_code, 409)  # 아직 끝나지 않음
        self.revise(pid, {"f1": "a"})
        with db.tx() as c:
            c.execute("UPDATE projects SET round=? WHERE id=?", (doc.MAX_ROUNDS, pid))
        r = self.next_round(pid, ["f1.1"])
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (409, "round_limit"))

    def test_paragraphs_saved_at_analysis_keep_finding_ids_stable(self):
        pid, s = self.analyzed()
        saved = [p["text"] for p in s["paragraphs"]]
        with db.tx() as c:  # 나중에 문단 나누는 규칙이 달라져도(본문을 다르게 읽어도) 분석 때의 문단을 쓴다
            c.execute("UPDATE projects SET body=? WHERE id=?", ("전혀 다른 한 문단", pid))
        self.assertEqual([p["text"] for p in self.state(pid)["paragraphs"]], saved)
        done = self.revise(pid, {"f2": "a"})
        self.assertEqual(done["result"]["checks"]["changed"], ["P3"])

    # --- 근거가 없으면 추측하지 않고 보류 + 필요한 자료 + 사용자 자료로 이어서 고치기 ---
    def test_partly_supported_edit_keeps_unconfirmed_part_on_hold_with_needed_material(self):
        os.environ["FAKE_MODE"] = "partial_hold"
        pid, _ = self.analyzed()
        done = self.revise(pid, {"f1": "a"})
        it = done["result"]["items"][0]
        self.assertEqual(it["verdict"], "resolved")
        self.assertIn("6.1%", it["after"])  # 근거가 있는 부분은 고쳤고
        self.assertIn("⚠[자료 보강 필요:", it["after"])  # 근거가 없는 부분은 쓰지 않고 표시만 남겼다
        self.assertIn("필요한 자료: 통계청", it["after"])
        report = (db.DATA_DIR / "projects" / pid / "outputs" / "review_report.md").read_text(encoding="utf-8")
        self.assertIn("확인하지 못해 보류한 내용: 청년 실업이 늘고 있다는 공식 수치 → 필요한 추가 자료: 통계청", report)
        final = (db.DATA_DIR / "projects" / pid / "outputs" / "final.md").read_text(encoding="utf-8")
        self.assertIn("자료 보강이 필요한 부분", final)
        prompt = [c for c in self.calls() if "한 문단을 고치는 편집자" in c["prompt"]][0]["prompt"]
        self.assertIn("추측하거나 그럴듯하게 만들어 채우지 않는다", prompt)
        self.assertIn("held", prompt)

    def test_user_material_continues_a_held_item_and_is_cited_as_user_source(self):
        os.environ["FAKE_MODE"] = "insufficient"
        pid, _ = self.analyzed()
        done = self.revise(pid, {"f1": "a"})
        self.assertEqual(done["result"]["items"][0]["verdict"], "insufficient")
        mat = "통계청 경제활동인구조사에 따르면 청년층 실업률은 2023년 6.1%였다."
        v = self.state(pid)["version"]
        self.assertEqual(self.c.post(f"/api/projects/{pid}/materials", json={"version": v, "materials": {"f2": mat}}).status_code, 400)  # 보류 항목이 아니다
        self.assertEqual(self.c.post(f"/api/projects/{pid}/materials", json={"version": v, "materials": {"f1": "  "}}).status_code, 400)  # 빈 자료
        r = self.c.post(f"/api/projects/{pid}/materials", json={"version": v, "materials": {"f1": mat}})
        self.assertEqual(r.status_code, 200, r.text)
        s2 = self.state(pid)
        f = s2["findings"][0]
        self.assertEqual((s2["round"], s2["stage"], f["fromFinding"], f["category"]), (2, "findings_ready", "f1", "근거 부족"))
        self.assertIn("통계청", f["material"])
        self.assertIn("필요했던 자료: 통계청", f["why"])
        os.environ["FAKE_MODE"] = "ok"
        done2 = self.revise(pid, {"f1": "a"})  # 제공한 자료로 보강
        it = done2["result"]["items"][0]
        self.assertEqual(it["verdict"], "resolved")
        self.assertEqual([x["url"] for x in it["sources"]], ["(사용자 제공 자료)"])
        self.assertEqual(it["held"], [])
        self.assertEqual((done2["result"]["remaining"]["held"], done2["result"]["remaining"]["insufficient"]), (0, 0))  # 보류가 풀렸다
        prompt = [c for c in self.calls() if "한 문단을 고치는 편집자" in c["prompt"]][-1]["prompt"]
        self.assertIn("2023년 6.1%였다", prompt)  # 붙여 넣은 자료가 데이터로 전달됐다
        self.assertEqual(self.calls()[-2]["tools"], "")  # 자료만 쓰는 방향은 웹 검색 도구 없이
        final = (db.DATA_DIR / "projects" / pid / "outputs" / "final.md").read_text(encoding="utf-8")
        self.assertIn("(사용자 제공 자료)", final)
        self.assertNotIn("⚠[자료 보강 필요", final)  # 해결했으니 보류 표시도 사라진다

    def test_quote_not_in_user_material_is_warned_and_round_limit_blocks_materials(self):
        os.environ["FAKE_MODE"] = "insufficient"
        pid, _ = self.analyzed()
        self.revise(pid, {"f1": "a"})
        v = self.state(pid)["version"]
        self.c.post(f"/api/projects/{pid}/materials", json={"version": v, "materials": {"f1": "제공한 자료의 본문입니다. 청년 실업률 설명."}})
        os.environ["FAKE_MODE"] = "bad_quote"
        done = self.revise(pid, {"f1": "a"})
        self.assertTrue(any("제공한 자료에 그대로 없는 인용문" in w for w in done["result"]["items"][0]["warnings"]))  # 지어낸 인용 가능성을 알린다
        with db.tx() as c:
            c.execute("UPDATE projects SET round=? WHERE id=?", (doc.MAX_ROUNDS, pid))
        r = self.c.post(f"/api/projects/{pid}/materials", json={"version": self.state(pid)["version"], "materials": {"f1": "x"}})
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (409, "round_limit"))

    def test_restart_marks_running_work_interrupted_and_allows_retry(self):
        pid, s = self.analyzed()
        self.c.put(f"/api/projects/{pid}/choices", json={"choices": {"f2": "a"}})
        with db.tx() as c:  # 서버가 작업 도중 꺼진 상황을 만든다
            c.execute("UPDATE projects SET stage='revising' WHERE id=?", (pid,))
            c.execute("INSERT INTO runs(id, project_id, kind, status, step, message, created_at, updated_at) VALUES ('run_cut', ?, 'revise', 'running', 'x', 'm', ?, ?)",
                      (pid, db.now_iso(), db.now_iso()))
        self.client.__exit__(None, None, None)
        self.client.__enter__()  # 서버 재시작(lifespan 다시 실행)
        s = self.state(pid)
        self.assertEqual((s["stage"], s["currentRun"]["status"], s["choices"]), ("findings_ready", "interrupted", {"f2": "a"}))
        r = self.c.post(f"/api/projects/{pid}/revise", json={"version": s["version"]})  # 같은 선택으로 다시 시작
        self.assertEqual(r.status_code, 202, r.text)
        self.assertEqual(self.wait(pid, lambda x: x["stage"] == "done")["stage"], "done")

    def test_cancel_kills_child_and_returns_to_selection(self):
        pid, s = self.analyzed()
        os.environ["FAKE_MODE"] = "hang"
        self.c.put(f"/api/projects/{pid}/choices", json={"choices": {"f2": "a"}})
        v = self.state(pid)["version"]
        self.assertEqual(self.c.post(f"/api/projects/{pid}/revise", json={"version": v}).status_code, 202)
        run_id = self.wait(pid, lambda x: x["currentRun"]["status"] in ("running", "queued"))["currentRun"]["runId"]
        t0 = time.time()
        while not ENGINE._procs and time.time() - t0 < 10:
            time.sleep(0.1)
        r = self.c.post(f"/api/runs/{run_id}/cancel", json={})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["cleanup"]["processesTerminated"], 1)
        s = self.state(pid)
        self.assertEqual((s["stage"], s["currentRun"]["status"]), ("findings_ready", "cancelled"))


if __name__ == "__main__":
    unittest.main()
