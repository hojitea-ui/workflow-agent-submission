"""SVG 검증·문서 만들기 단위 시험과, 실제로 설치된 Edge/Chrome으로 SVG를 PNG로 바꾸는 시험(브라우저가 있을 때만)."""
import os
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from test_flow import rid  # noqa: F401,E402  (임시 DATA_DIR와 환경 변수를 먼저 설정한다)

from app import db, runs, svg_render  # noqa: E402

PAL = ["#14213D", "#3A6EA5", "#F4A259", "#F7F9FC", "#5BC0BE"]


def good(n=6, extra=""):
    return "<g>" + "".join(f'<rect x="{100 + i * 70}" y="600" width="60" height="200" fill="#3A6EA5"/>' for i in range(n)) + extra + "</g>"


class SanitizeTests(unittest.TestCase):
    def ok(self, fragment):
        return svg_render.sanitize_fragment(fragment, PAL)

    def bad(self, fragment, word=""):
        with self.assertRaises(svg_render.SvgError) as cm:
            svg_render.sanitize_fragment(fragment, PAL)
        self.assertIn("SVG 검증 실패", str(cm.exception))
        if word:
            self.assertIn(word, str(cm.exception))

    def test_valid_fragment_is_kept_and_has_no_namespace_noise(self):
        out = self.ok(good(extra='<circle cx="500" cy="700" r="40" fill="#3A6EA5" opacity="0.5"/>'
                                 '<path d="M0 0 C10 10 20 20 30 40 Q 5 5 9 9 Z" stroke="#14213D" stroke-width="8" fill="none"/>'
                                 '<g transform="translate(10 20) rotate(15)"><polygon points="1,2 30,40 50,10" fill="#F4A259"/></g>'))
        self.assertNotIn("ns0", out)
        self.assertNotIn("xmlns", out)
        self.assertIn("<circle", out)

    def test_colors_are_snapped_to_palette(self):
        out = self.ok(good(extra='<circle cx="500" cy="700" r="40" fill="#ff0000"/><circle cx="500" cy="700" r="40" stroke="#00FF00" fill="none"/>'))
        import re
        self.assertTrue({c.upper() for c in re.findall(r"#[0-9a-fA-F]{6}", out)} <= set(PAL))
        self.assertNotIn("ff0000", out.lower())

    def test_gradients_with_defined_ids_are_allowed(self):
        grad = ('<defs><linearGradient id="g1" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#F7F9FC"/>'
                '<stop offset="1" stop-color="#ff00ff"/></linearGradient></defs>')
        out = self.ok("<g>" + grad + good(5).replace("<g>", "").replace("</g>", "").replace('fill="#3A6EA5"', 'fill="url(#g1)"', 1) + "</g>")
        self.assertIn("url(#g1)", out)
        self.bad("<g>" + good(5).replace("<g>", "").replace("</g>", "").replace('fill="#3A6EA5"', 'fill="url(#nope)"', 1) + "</g>", "fill")

    def test_forbidden_elements(self):
        for tag in ("<script>alert(1)</script>", '<text x="1" y="2">글자</text>', '<image href="x.png"/>', '<use href="#a"/>',
                    "<style>rect{fill:red}</style>", '<foreignObject><div/></foreignObject>', '<svg><rect/></svg>', '<clipPath id="c"><rect/></clipPath>'):
            self.bad(good(extra=tag), "허용되지 않는")

    def test_forbidden_attributes_and_values(self):
        self.bad(good(extra='<rect x="1" y="1" width="9" height="9" onclick="x()"/>'), "onclick")
        self.bad(good(extra='<rect x="1" y="1" width="9" height="9" style="fill:red"/>'), "style")
        self.bad(good(extra='<rect x="1" y="1" width="9" height="9" fill="red"/>'), "fill")
        self.bad(good(extra='<rect x="1" y="1" width="9" height="9" fill="rgb(1,2,3)"/>'), "fill")
        self.bad(good(extra='<rect x="1" y="1" width="9" height="9" fill="url(http://evil/x)"/>'), "fill")
        self.bad(good(extra='<rect x="1" y="1" width="9px" height="9"/>'), "숫자")
        self.bad(good(extra='<path d="M0 0 L 10 10 javascript:alert(1)"/>'), "path")
        self.bad(good(extra='<rect x="1" y="1" width="9" height="9" transform="translate(1) foo(2)"/>'), "transform")
        self.bad(good(extra='<rect xlink:href="x" x="1" y="1" width="9" height="9"/>'), "")

    def test_structure_limits(self):
        self.bad("", "비어")
        self.bad("<g><rect x='1'", "XML")
        self.bad("<g></g>", "너무 적습니다")
        self.bad(good(300), "너무 많습니다")
        self.bad("<g>" + "x" * 70000 + "</g>", "너무 깁니다")
        self.bad(good(extra='<rect x="99999" y="1" width="9" height="9"/>'), "너무 큽니다")
        self.bad(good(extra="<!-- 주석 -->"), "주석")
        self.bad('<!DOCTYPE x [<!ENTITY a "b">]>' + good(), "주석")
        self.bad(good(extra="<rect/>").replace("<g>", "<g>글자가 섞임"), "글자")
        self.bad(good(extra='<rect id="bg__" x="1" y="1" width="9" height="9"/>'), "id")
        self.bad(good(extra='<rect id="a" x="1" y="1" width="9" height="9"/><rect id="a" x="1" y="1" width="9" height="9"/>'), "id")

    def test_document_wraps_fragment_with_background_and_top_clip(self):
        doc = svg_render.svg_document(self.ok(good()), PAL)
        self.assertTrue(doc.startswith("<svg"))
        self.assertIn('viewBox="0 0 1080 1350"', doc)
        self.assertIn('<clipPath id="below__"><rect x="0" y="405" width="1080" height="553"/>', doc)
        self.assertIn('clip-path="url(#below__)"', doc)
        self.assertLess(doc.index("fill=\"url(#bg__)\""), doc.index("clip-path"))  # 배경을 먼저, 그림은 클립 안에
        import xml.etree.ElementTree as ET
        ET.fromstring(doc)  # 완전한 XML


class BrowserDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.saved = {k: os.environ.get(k) for k in ("CARDNEWS_BROWSER_CMD", "CARDNEWS_BROWSER")}

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_overrides(self):
        os.environ.pop("CARDNEWS_BROWSER_CMD", None)
        os.environ["CARDNEWS_BROWSER"] = str(Path(tempfile.gettempdir()) / "nope_browser.exe")
        self.assertIsNone(svg_render.find_browser())  # 지정한 경로가 없으면 다른 걸 몰래 쓰지 않는다
        os.environ["CARDNEWS_BROWSER"] = __file__
        self.assertEqual(svg_render.find_browser(), [__file__])
        os.environ["CARDNEWS_BROWSER_CMD"] = '["__no_such_cmd__"]'
        self.assertIsNone(svg_render.find_browser())


@unittest.skipUnless(
    (lambda: (os.environ.pop("CARDNEWS_BROWSER_CMD", None), os.environ.pop("CARDNEWS_BROWSER", None), svg_render.find_browser())[2])(),
    "이 PC에 Edge/Chrome이 없어 실제 브라우저 시험을 건너뜁니다")
class RealBrowserTests(unittest.TestCase):
    """실제 Edge/Chrome(헤드리스)으로 SVG를 PNG로 바꾼다. 모델 호출도 agy도 쓰지 않는다."""

    def setUp(self):
        self.saved = {k: os.environ.get(k) for k in ("CARDNEWS_BROWSER_CMD", "CARDNEWS_BROWSER")}
        os.environ.pop("CARDNEWS_BROWSER_CMD", None)
        os.environ.pop("CARDNEWS_BROWSER", None)
        self.tmp = tempfile.TemporaryDirectory()
        db.init()
        with db.tx() as c:  # render_png가 작업 상태를 확인하므로 진행 중인 작업 하나가 필요하다
            now = db.now_iso()
            c.execute("INSERT INTO projects(id, topic, stage, version, engine, mock, window_days, window_basis, created_at, updated_at) "
                      "VALUES ('prj_t', 't', 'producing', 1, 'claude-cli', 0, 7, ?, ?, ?)", (now, now, now))
            c.execute("INSERT INTO runs(id, project_id, kind, status, created_at, updated_at) VALUES ('run_svg_t', 'prj_t', 'images', 'running', ?, ?)", (now, now))

    def tearDown(self):
        self.tmp.cleanup()
        with db.tx() as c:
            c.execute("DELETE FROM runs WHERE id='run_svg_t'")
            c.execute("DELETE FROM projects WHERE id='prj_t'")
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_renders_1080x1350_png_with_clean_top_margin(self):
        art = svg_render.sanitize_fragment(
            '<g><path d="M300 900 C300 700 420 640 540 640 C660 640 780 700 780 900 Z" fill="#3A6EA5"/>'
            '<circle cx="540" cy="760" r="70" fill="#F4A259"/>'
            '<path d="M120 1120 Q540 980 960 1120" stroke="#14213D" stroke-width="22" fill="none" stroke-linecap="round"/>'
            '<ellipse cx="540" cy="1200" rx="400" ry="50" fill="#5BC0BE" opacity="0.5"/>'
            '<rect x="100" y="100" width="880" height="200" fill="#14213D"/></g>',  # 위쪽 여백에 들어온 도형: 잘려 나가야 한다
            PAL)
        img = svg_render.render_png(svg_render.svg_document(art, PAL), Path(self.tmp.name) / "w", "run_svg_t")
        self.assertEqual(img.size, (1080, 1350))
        out = Path(self.tmp.name) / "a.png"
        from app import images
        images.finalize_image(img, out)
        r = images.inspect_png(out)
        self.assertEqual({k: r["checks"][k] for k in ("file", "format", "size", "margin")}, {"file": "pass", "format": "pass", "size": "pass", "margin": "pass"})
        # 위쪽 28%에는 모델이 그린 어두운 사각형(#14213D)이 없고 배경 그라데이션만 있다
        dark = sum(1 for y in range(20, 370, 30) for x in range(0, 1080, 90) if sum(img.getpixel((x, y))) < 200)
        self.assertEqual(dark, 0)
        self.assertFalse((Path(self.tmp.name) / "w" / "profile").exists())  # 임시 브라우저 프로필은 지운다


if __name__ == "__main__":
    unittest.main()
