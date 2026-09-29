"""Tests for the interface shell and the user guide.

The interface is plain files served from ``app/web``, so most of it can be
checked without a browser: the screens the navigation offers have to match the
routes the router handles, the guide has to keep up with the product tabs, the
assets the shell references have to exist and load in order, and the written
guide may only quote commands the launcher actually has.

One live server start then confirms the files really are served - the static
path never opens the database, so this stays cheap.
"""
from __future__ import annotations

import re
import threading
import unittest
from pathlib import Path
from urllib.request import urlopen

from app import server

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "app" / "web"
ASSETS = ["index.html", "styles.css", "charts.js", "guide.js", "app.js"]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class ShellTests(unittest.TestCase):
    """The static page: what it references, and what it must not reach for."""

    def test_the_shell_references_assets_that_exist(self) -> None:
        index = read(WEB / "index.html")
        for ref in re.findall(r'(?:src|href)="([^"]+)"', index):
            if ref.startswith(("#", "data:", "http", "mailto:")):
                continue
            self.assertTrue((WEB / ref).is_file(), f"{ref} is referenced but missing")

    def test_the_guide_loads_before_the_application(self) -> None:
        index = read(WEB / "index.html")
        order = re.findall(r'<script src="([^"]+)"></script>', index)
        self.assertEqual(order, ["charts.js", "guide.js", "app.js"])

    def test_the_interface_makes_no_requests_beyond_its_own_origin(self) -> None:
        # The whole point of the project is that it runs offline: an absolute
        # URL may only be the SVG namespace in the favicon, or the loopback
        # address the guide tells the user to open.
        allowed = ("http://www.w3.org/", "http://127.0.0.1", "http://localhost")
        for name in ASSETS:
            for url in re.findall(r'https?://[^\s"\'<>)]+', read(WEB / name)):
                self.assertTrue(url.startswith(allowed), f"{name} reaches out to {url}")

    def test_the_help_link_points_at_the_guide(self) -> None:
        index = read(WEB / "index.html")
        self.assertIn('data-nav="guide"', index)
        self.assertIn('id="help-button"', index)
        self.assertIn('href="#/guide"', index)


class RouteTests(unittest.TestCase):
    """Navigation and routing have to agree, or a link silently does nothing."""

    def setUp(self) -> None:
        self.index = read(WEB / "index.html")
        self.app = read(WEB / "app.js")

    def test_every_navigable_screen_has_a_route(self) -> None:
        navigable = set(re.findall(r'data-nav="([a-z]+)"', self.index))
        handled = set(re.findall(r'head === "([a-z]+)"', self.app))
        self.assertTrue(navigable)
        self.assertLessEqual(navigable - {"products"}, handled, "a nav link has no route")

    def test_the_router_also_handles_products_and_guide_aliases(self) -> None:
        handled = set(re.findall(r'head === "([a-z]+)"', self.app))
        self.assertLessEqual({"product", "guide", "help", "how"}, handled)

    def test_the_guide_view_is_wired_to_the_guide_module(self) -> None:
        self.assertIn("function renderGuide", self.app)
        self.assertIn("window.Guide", read(WEB / "guide.js"))
        self.assertIn("Guide.render(", self.app)


class GuideContentTests(unittest.TestCase):
    """The guide is documentation, so it can fall behind. These lock it in place."""

    def setUp(self) -> None:
        self.app = read(WEB / "app.js")
        self.guide = read(WEB / "guide.js")
        self.document = read(ROOT / "docs" / "USER_GUIDE.md")

    def _tabs(self) -> list[str]:
        match = re.search(r"var TABS = \[([^\]]*)\]", self.app)
        self.assertIsNotNone(match, "TABS moved; the guide-coverage test needs updating")
        return re.findall(r'"([a-z]+)"', match.group(1))

    def test_the_product_tabs_are_still_the_ones_the_guide_documents(self) -> None:
        tabs = self._tabs()
        self.assertEqual(
            tabs,
            ["overview", "prediction", "populations", "trials", "plan", "process", "report", "ledger"],
        )

    def test_the_in_app_guide_covers_every_product_tab(self) -> None:
        guide = self.guide.lower()
        for tab in self._tabs():
            self.assertIn(tab, guide, f"the guide does not explain the {tab} tab")

    def test_the_document_guide_covers_every_product_tab(self) -> None:
        document = self.document.lower()
        for tab in self._tabs():
            self.assertIn(tab, document, f"USER_GUIDE.md does not explain the {tab} tab")

    def test_the_in_app_guide_covers_every_navigable_screen(self) -> None:
        index = read(WEB / "index.html")
        guide = self.guide.lower()
        for label in re.findall(r'data-nav="[a-z]+">([^<]+)</a>', index):
            self.assertIn(label.strip().lower(), guide, f"the guide does not explain {label!r}")

    def test_the_document_guide_only_quotes_commands_the_launcher_has(self) -> None:
        run = read(ROOT / "run.py")
        available = set(re.findall(r'"(--[a-z][a-z0-9-]*)"', run))
        self.assertTrue(available, "no flags parsed out of run.py")
        quoted = set(re.findall(r"--[a-z][a-z0-9-]*", self.document))
        self.assertTrue(quoted)
        self.assertLessEqual(quoted, available, "USER_GUIDE.md quotes a flag run.py does not have")

    def test_the_document_guide_is_linked_from_the_readme(self) -> None:
        self.assertIn("docs/USER_GUIDE.md", read(ROOT / "README.md"))

    def test_the_guide_renders_from_state_rather_than_typed_numbers(self) -> None:
        # The guide must take its figures from the caller, not carry its own copy.
        self.assertIn("function facts(state)", self.guide)
        self.assertIn("Guide.render({", self.app)
        for key in ("health", "cases", "benchmark", "products"):
            self.assertIn(key + ":", self.app)


class ScriptHealthTests(unittest.TestCase):
    """Cheap guards against the ways this UI has actually broken.

    There is no build step, so nothing else catches a stray quote or a grid that
    refuses to shrink. Both of those have already cost a broken interface once.
    """

    def test_every_javascript_line_has_balanced_quotes(self) -> None:
        # The interface never uses a multi-line string literal, so every line has
        # to carry an even number of double quotes. An odd count is a stray quote,
        # and one stray quote stops the whole page rendering.
        for name in ("app.js", "guide.js", "charts.js"):
            for number, line in enumerate(read(WEB / name).splitlines(), 1):
                self.assertEqual(
                    line.count('"') % 2,
                    0,
                    f"{name}:{number} has an odd number of double quotes: {line.strip()[:80]}",
                )

    def test_the_guide_contents_links_scroll_instead_of_routing(self) -> None:
        # "#g-numbers" is not a route; without this the router would render the
        # products list over the guide when a contents link was clicked.
        app = read(WEB / "app.js")
        self.assertIn("a[href^='#g-']", app)
        self.assertIn("preventDefault", app)

    def test_the_guide_can_shrink_on_a_narrow_screen(self) -> None:
        # A wide table inside a guide section widened the whole page instead of
        # scrolling in place: a grid item's automatic minimum size is its
        # min-content size, so it has to be told it may shrink.
        css = read(WEB / "styles.css")
        self.assertIn(".guide-body { min-width: 0; }", css)
        self.assertIn("grid-template-columns: minmax(0, 1fr);", css)


class ResponsiveTests(unittest.TestCase):
    """The interface has to work on a phone, not just survive on one.

    Measured at a 320 px viewport before this was written: the case-study grid
    overflowed by 30 px because a half-grid track was pinned at 320 px, and the
    product Prediction tab overflowed by 67 px because its desirability table
    was the one table in the UI not wrapped in a scroll container.
    """

    def setUp(self) -> None:
        self.index = read(WEB / "index.html")
        self.app = read(WEB / "app.js")
        self.css = read(WEB / "styles.css")

    def test_the_viewport_allows_safe_area_insets(self) -> None:
        self.assertIn("width=device-width", self.index)
        self.assertIn("viewport-fit=cover", self.index)

    def test_the_brand_has_a_short_form_for_phones(self) -> None:
        self.assertIn('class="only-wide"', self.index)
        self.assertIn(".only-wide { display: none; }", self.css)

    def test_no_table_is_left_unable_to_scroll_sideways(self) -> None:
        for number, line in enumerate(self.app.splitlines(), 1):
            if "<table" in line:
                self.assertIn(
                    "class='scroll'",
                    line[: line.index("<table")],
                    f"app.js:{number} builds a table outside a .scroll container",
                )
        # charts.js returns a bare table, so its caller has to wrap it.
        self.assertIn("<div class='scroll'>\" + Charts.gaugeBars(", self.app)

    def test_the_half_grid_can_shrink_below_its_column_minimum(self) -> None:
        self.assertIn("minmax(min(320px, 100%), 1fr)", self.css)

    def test_there_is_a_phones_breakpoint_that_fixes_the_known_issues(self) -> None:
        self.assertIn("@media (max-width: 560px)", self.css)
        phones = self.css[self.css.index("@media (max-width: 560px)") :]
        # Thumbs-sized controls, no iOS zoom on focus, one scrollable tab strip.
        self.assertIn("min-height: 42px", phones)
        self.assertIn("font-size: 16px", phones)
        self.assertIn("flex-wrap: nowrap", phones)


class StaticServingTests(unittest.TestCase):
    """Start the real server once and confirm the interface is actually served."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)

    def get(self, path: str):
        with urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=5) as response:
            return response.status, response.headers.get("Content-Type", ""), response.read().decode("utf-8", "replace")

    def test_the_shell_and_every_script_are_served(self) -> None:
        expected = {
            "/": ("text/html", "FormuSense"),
            "/app.js": ("javascript", "renderGuide"),
            "/guide.js": ("javascript", "window.Guide"),
            "/charts.js": ("javascript", "Charts"),
            "/styles.css": ("text/css", ".guide-layout"),
        }
        for path, (kind, needle) in expected.items():
            status, content_type, body = self.get(path)
            self.assertEqual(status, 200, path)
            self.assertIn(kind, content_type, path)
            self.assertIn(needle, body, path)

    def test_the_static_folder_is_the_ui_and_nothing_above_it(self) -> None:
        # run.py lives in the repository root; it must not be reachable as an asset.
        status, _, body = self.get("/run.py")
        self.assertEqual(status, 200)  # the single-page shell takes over
        self.assertTrue(body.lstrip().startswith("<!DOCTYPE html>"))
        self.assertNotIn("argparse", body)

    def test_an_unknown_path_falls_back_to_the_shell(self) -> None:
        status, _, body = self.get("/product/12/overview")
        self.assertEqual(status, 200)
        self.assertTrue(body.lstrip().startswith("<!DOCTYPE html>"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
