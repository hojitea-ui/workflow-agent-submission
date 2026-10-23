"""모델 답변 검증(claude_parse) 단위 테스트."""
import unittest
from datetime import date

from app import claude_parse as cp

W_FROM, W_TO = date(2026, 10, 16), date(2026, 10, 23)
SEEN = {cp.normalize_url("https://example.org/a"), cp.normalize_url("https://news.example.com/x?id=3")}


class ExtractTests(unittest.TestCase):
    def test_variants(self):
        self.assertEqual(cp.extract_json('{"a": 1}'), {"a": 1})
        self.assertEqual(cp.extract_json('설명\n```json\n{"a": 2}\n```\n끝'), {"a": 2})
        self.assertEqual(cp.extract_json('앞말 {"a": 3} 뒷말'), {"a": 3})
        self.assertIsNone(cp.extract_json("JSON 없음"))
        self.assertIsNone(cp.extract_json('{"a": '))
        self.assertIsNone(cp.extract_json(""))

    def test_url_normalization(self):
        n = cp.normalize_url
        self.assertEqual(n("http://www.Example.org/a/"), n("https://example.org/a"))
        self.assertEqual(n("https://example.org/a?utm_source=x&id=1"), n("https://example.org/a?id=1"))
        self.assertNotEqual(n("https://example.org/a?id=1"), n("https://example.org/a?id=2"))
        self.assertIsNone(n("javascript:alert(1)"))
        self.assertIsNone(n("ftp://example.org/a"))
        self.assertEqual(cp.find_urls('Links: [{"url":"https://example.org/a"}, "see https://x.org/p."]'),
                         {n("https://example.org/a"), n("https://x.org/p")})


def cand(**kw):
    base = {"title": "T", "summary": "s", "publishedAt": "2026-10-20", "category": "model", "whySelect": "w", "uncertainty": "u",
            "sources": [{"title": "t", "url": "https://example.org/a", "publisher": "p"}]}
    base.update(kw)
    return base


class CandidateTests(unittest.TestCase):
    def run_v(self, items):
        return cp.validate_candidates({"candidates": items}, W_FROM, W_TO, SEEN)

    def test_filters(self):
        out, dropped = self.run_v([
            cand(title="ok"),
            cand(title="invented", sources=[{"title": "t", "url": "https://fake.example.net/q"}]),
            cand(title="old", publishedAt="2026-09-01"),
            cand(title="future", publishedAt="2026-12-31"),
            cand(title="bad date", publishedAt="어제"),
            cand(title="OK"),  # 대소문자만 다른 중복
            cand(title="weird", category="nonsense", groupedCount="x"),
            "문자열 항목", {"summary": "제목 없음"},
        ])
        titles = [c["title"] for c in out]
        self.assertEqual(titles, ["ok", "future", "bad date", "weird"])
        self.assertEqual(len(dropped), 2)  # invented, old
        by = {c["title"]: c for c in out}
        self.assertEqual(by["future"]["dateStatus"], "unconfirmed")  # 기준일 이후 날짜는 믿지 않는다
        self.assertEqual(by["bad date"]["publishedAt"], None)
        self.assertEqual(by["weird"]["category"], "other")
        self.assertEqual(by["weird"]["groupedCount"], 1)
        self.assertEqual([c["id"] for c in out], ["cand_1", "cand_2", "cand_3", "cand_4"])

    def test_query_string_must_match(self):
        out, dropped = self.run_v([cand(sources=[{"title": "t", "url": "https://news.example.com/x?id=4"}])])
        self.assertEqual(out, [])
        self.assertEqual(len(dropped), 1)

    def test_cap_and_shape(self):
        items = [cand(title=f"t{i}") for i in range(20)]
        self.assertEqual(len(self.run_v(items)[0]), 12)
        with self.assertRaises(cp.FormatError):
            cp.validate_candidates({"nope": 1}, W_FROM, W_TO, SEEN)


class QuestionTests(unittest.TestCase):
    def test_valid_and_invalid(self):
        q = cp.validate_question({"question": "독자?", "options": [{"id": "a b!", "label": "A"}, "B"],
                                  "recommendation": {"option_id": "opt2", "reason": "r"}})
        self.assertEqual([o["id"] for o in q["options"]], ["ab", "opt2"])
        self.assertEqual(q["recommendation"]["optionId"], "opt2")
        self.assertTrue(q["allowFreeText"])
        self.assertIsNone(cp.validate_question({"question": "q", "options": ["A", "B"],
                                                "recommendation": {"option_id": "zzz", "reason": "r"}})["recommendation"])
        for bad in ({"question": "", "options": ["A", "B"]}, {"question": "q", "options": ["A"]}, {"question": "q"},
                    {"question": "q", "options": [{"id": "x"}, "B"]}):
            with self.assertRaises(cp.FormatError):
                cp.validate_question(bad)


class NotesProposalTests(unittest.TestCase):
    ALLOWED = {cp.normalize_url("https://example.org/a")}

    def test_notes_downgrade_without_evidence(self):
        notes = cp.validate_notes({"facts": [
            {"text": "확인", "sourceUrl": "https://example.org/a", "status": "confirmed"},
            {"text": "주소 없음", "status": "confirmed"},
            {"text": "엉뚱한 상태", "sourceUrl": "https://example.org/a", "status": "great"},
            "문자열 사실"], "sources": [{"url": "https://example.org/a", "title": "t"}, {"url": "https://bad.net/z"}]},
            self.ALLOWED, fetched_ok=True)
        self.assertEqual([f["status"] for f in notes["facts"]], ["confirmed", "unverified", "unverified", "unverified"])
        self.assertEqual(len(notes["sources"]), 1)
        self.assertEqual(notes["limits"], [])

    def test_no_fetch_means_nothing_confirmed(self):
        notes = cp.validate_notes({"facts": [{"text": "f", "sourceUrl": "https://example.org/a", "status": "confirmed"}]},
                                  self.ALLOWED, fetched_ok=False)
        self.assertEqual(notes["facts"][0]["status"], "unverified")
        self.assertIn("열어 보지 못해", notes["limits"][0])
        with self.assertRaises(cp.FormatError):
            cp.validate_notes({"facts": []}, self.ALLOWED, True)

    def test_proposal_rules(self):
        base = {"audience": "a", "whyUseful": "w", "format": "list",
                "formatComparison": [{"format": "list", "fit": "f", "cards": 6, "evidenceDifficulty": "e"},
                                     {"format": "story", "fit": "f", "cards": 20, "evidenceDifficulty": "e"}],
                "formatRecommendation": {"format": "list", "reason": "r"}, "hooks": ["최신 소식 모음", "확인된 변화"]}
        no = cp.validate_proposal(base, {"titleMayUseLatest": False})
        self.assertEqual(no["hooks"], ["확인된 변화"])
        self.assertEqual(no["formatComparison"][1]["cards"], 12)
        yes = cp.validate_proposal(base, {"titleMayUseLatest": True})
        self.assertEqual(len(yes["hooks"]), 2)
        only_latest = dict(base, hooks=["최신 소식"])
        with self.assertRaises(cp.FormatError):
            cp.validate_proposal(only_latest, {"titleMayUseLatest": False})
        for broken in (dict(base, format="grid"), dict(base, formatComparison=base["formatComparison"][:1]),
                       dict(base, formatRecommendation={"format": "list"}), dict(base, audience="")):
            with self.assertRaises(cp.FormatError):
                cp.validate_proposal(broken, {"titleMayUseLatest": True})

    def test_freshness(self):
        c = [{"dateStatus": "confirmed", "publishedAt": "2026-10-20"}]
        self.assertTrue(cp.freshness_basis(c, date(2026, 10, 23), 7)["titleMayUseLatest"])
        self.assertFalse(cp.freshness_basis(c, date(2026, 11, 20), 7)["titleMayUseLatest"])
        self.assertFalse(cp.freshness_basis([{"dateStatus": "unconfirmed", "publishedAt": None}], date(2026, 10, 23), 7)["titleMayUseLatest"])
        self.assertFalse(cp.freshness_basis([], date(2026, 10, 23), 7)["titleMayUseLatest"])


if __name__ == "__main__":
    unittest.main()
