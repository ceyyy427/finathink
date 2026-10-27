"""The reference shell keeps real navigation and existing research boundaries."""

from html.parser import HTMLParser
from urllib.parse import urlencode

import pytest

from finahinking.local_app import LocalAppConfig, LocalApplication
from finahinking.p7 import PersonalNode


class PageElements(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.elements = []
        self.textareas = {}
        self.metrics = {}
        self.active_textarea = None
        self.active_metric = None
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.elements.append((tag, attrs))
        if tag == "textarea":
            self.active_textarea = attrs.get("name")
            self.textareas[self.active_textarea] = ""
        if "data-overview-metric" in attrs:
            self.active_metric = attrs["data-overview-metric"]
            self.metrics[self.active_metric] = ""

    def handle_data(self, data):
        if self.active_textarea is not None:
            self.textareas[self.active_textarea] += data
        if self.active_metric is not None:
            self.metrics[self.active_metric] += data

    def handle_endtag(self, tag):
        if tag == "textarea":
            self.active_textarea = None
        if tag == "span":
            self.active_metric = None


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


@pytest.mark.parametrize("question", ["低换手是否有独立增量？", '比较 A&B、"低波"与动量\n保留反例', '</textarea><script>alert("private")</script>'])
def test_home_question_prefills_existing_form_without_submitting(tmp_path, question):
    app = LocalApplication(LocalAppConfig(db_path=str(tmp_path / "state.sqlite3")))
    try:
        home = PageElements(app.route("GET", "/")[2])
        assert any(tag == "form" and attrs.get("action") == "/research/jobs" and attrs.get("method") == "get" for tag, attrs in home.elements)
        status, _, source = app.route("GET", "/research/jobs?" + urlencode({"question": question}))
        assert status == 200
        parsed = PageElements(source)
        assert parsed.textareas["question"] == question
        assert sum(tag == "script" for tag, _ in parsed.elements) == 1
        assert not app.research_service.snapshots.list_snapshots()
        assert app.research_service.list_jobs()["total"] == 0
    finally:
        app.close()


def test_oversized_home_question_is_bounded_with_visible_notice(tmp_path):
    app = LocalApplication(LocalAppConfig(db_path=str(tmp_path / "state.sqlite3")))
    try:
        status, _, source = app.route("GET", "/research/jobs?" + urlencode({"question": "问" * 2500}))
        assert status == 200
        assert PageElements(source).textareas["question"] == "问" * 1000
        assert 'data-question-truncated' in source
        assert app.research_service.list_jobs()["total"] == 0
    finally:
        app.close()


def test_overview_counts_are_real_local_records_not_prototype_examples(tmp_path):
    app = LocalApplication(LocalAppConfig(db_path=str(tmp_path / "state.sqlite3")))
    try:
        empty = PageElements(app.route("GET", "/")[2])
        assert empty.metrics == {"sample_events": "1", "saved_items": "0", "learning_items": "0", "research_runs": "0"}
        app.repository.save_node(app.config.session_id, PersonalNode("local-knowledge", "concept", "自己的知识记录", {}))
        populated = PageElements(app.route("GET", "/")[2])
        assert populated.metrics == {"sample_events": "1", "saved_items": "1", "learning_items": "0", "research_runs": "0"}
        assert not app.research_service.snapshots.list_snapshots()
    finally:
        app.close()
