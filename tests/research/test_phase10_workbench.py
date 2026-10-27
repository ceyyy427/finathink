"""The reference shell keeps real navigation and existing research boundaries."""

from html.parser import HTMLParser

import pytest

from finahinking.local_app import LocalAppConfig, LocalApplication


class PageElements(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.elements = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


@pytest.mark.parametrize("path", ["/", "/research/jobs", "/research/experiments", "/research/learning", "/research/compare", "/settings/data-connections", "/knowledge"])
def test_existing_routes_get_one_accessible_workbench_shell(tmp_path, path):
    app = LocalApplication(LocalAppConfig(db_path=str(tmp_path / "state.sqlite3")))
    try:
        status, _, source = app.route("GET", path)
        assert status == 200
        elements = PageElements(source).elements
        assert len([1 for tag, attrs in elements if tag == "main" and attrs.get("id") == "main"]) == 1
        assert len([1 for tag, attrs in elements if tag == "script" and attrs.get("src") == "/assets/finathink-research.js"]) == 1
        assert any("data-finathink-shell" in attrs for _, attrs in elements)
        assert any(tag == "button" and attrs.get("aria-controls") == "shell-navigation" for tag, attrs in elements)
        assert any(tag == "dialog" and attrs.get("id") == "shell-search" for tag, attrs in elements)
        links = {attrs["href"] for tag, attrs in elements if tag == "a" and "nav-link" in attrs.get("class", "").split()}
        assert set(app._page_names()) <= links
        current = [attrs["href"] for tag, attrs in elements if tag == "a" and attrs.get("aria-current") == "page"]
        assert current == [path]
        assert not app.research_service.snapshots.list_snapshots()
        assert app.research_service.list_jobs()["total"] == 0
    finally:
        app.close()
