"""The MCP browser tool over the hypruse-browser bridge: what each action
asks the extension for, what it refuses, and what it says, with the
extension replaced by a fake client and the desktop stubbed."""

import asyncio
import importlib
import json

import pytest

from hypruse import cli
from hypruse import server as srv
from hypruse.browser import BrowserError, install

SNAP = {
    "snapshot_id": "s1-abc", "tab_id": 12, "url": "https://example.com/", "title": "Example",
    "headings": "Example Domain", "viewport": {"scroll_y": 0, "scroll_height": 900},
    "elements": [
        {"id": "e1", "role": "link", "name": "More information", "kind": "link",
         "rect": [1, 2, 3, 4], "in_view": True},
        {"id": "e2", "role": "searchbox", "name": "Search", "kind": "search",
         "rect": [1, 2, 3, 4], "in_view": False, "list": {"group": "g1", "index": 2, "size": 5}},
    ],
}


class FakeClient:
    def __init__(self, state):
        self.s = state

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def tabs(self):
        return [dict(t) for t in self.s["tabs"]]

    def snapshot(self, tab_id=None, *, max=120, settle_ms=0):
        self.s["calls"].append(("snapshot", tab_id, max, settle_ms))
        return dict(SNAP, tab_id=tab_id or 12)

    def click(self, snapshot_id, element_id):
        if self.s.get("fail"):
            raise self.s["fail"]
        self.s["calls"].append(("click", snapshot_id, element_id))

    def type(self, snapshot_id, element_id, text, *, submit=False):
        if self.s.get("fail"):
            raise self.s["fail"]
        self.s["calls"].append(("type", snapshot_id, element_id, text, submit))

    def scroll(self, direction, tab_id=None):
        self.s["calls"].append(("scroll", direction, tab_id))
        return {"scroll_y": 720, "scroll_height": 900}

    def open(self, url, where="new"):
        if where == "background" and self.s.get("old_extension"):
            raise BrowserError("bad_request", "where: invalid enum value")
        self.s["calls"].append(("open", url, where))
        return 99

    def activate(self, tab_id):
        self.s["calls"].append(("activate", tab_id))

    def close_tab(self, tab_id):
        self.s["calls"].append(("close", tab_id))

    def history(self, tab_id, direction):
        self.s["calls"].append(("history", tab_id, direction))


@pytest.fixture
def bridge(monkeypatch):
    s = {"calls": [],
         "tabs": [{"id": 12, "window_id": 1, "active": True, "title": "Example",
                   "url": "https://example.com/"},
                  {"id": 13, "window_id": 1, "active": False, "title": "Other",
                   "url": "https://other.example/"}],
         # the browser window, on workspace 1, which is on screen
         "clients": [{"address": "0xb", "class": "chromium", "title": "Example - Chromium",
                      "workspace": {"id": 1}}],
         "monitors": [{"activeWorkspace": {"id": 1}, "specialWorkspace": {"id": 0}}]}
    monkeypatch.delenv("HYPRUSE_BACKGROUND", raising=False)
    monkeypatch.delenv("HYPRUSE_CONFINE", raising=False)
    monkeypatch.setattr(srv.safety, "touch", lambda *a: None)
    monkeypatch.setattr(srv, "_browser_client", lambda profile: FakeClient(s))
    monkeypatch.setattr(srv, "_browser_snaps", {})
    monkeypatch.setattr(srv.hyprctl, "query",
                        lambda what: s["clients"] if what == "clients" else s["monitors"])
    # the human's seat, read before and after each acting call
    s["seats"] = []
    monkeypatch.setattr(srv, "_seat_now", lambda: s["seats"].pop(0) if s["seats"] else ("0xh", 1))
    monkeypatch.setattr(srv.time, "sleep", lambda t: None)
    return s


def test_tabs_say_which_one_the_human_can_see(bridge):
    tabs = json.loads(srv.browser("tabs"))
    assert [(t["id"], t["on_screen"]) for t in tabs] == [(12, True), (13, False)]


def test_a_tab_whose_window_is_on_a_hidden_workspace_is_not_on_screen(bridge):
    bridge["clients"][0]["workspace"] = {"id": 99}
    assert json.loads(srv.browser("tabs"))[0]["on_screen"] is False


def test_an_active_tab_no_window_title_names_is_unknown(bridge):
    bridge["clients"] = []
    assert json.loads(srv.browser("tabs"))[0]["on_screen"] is None


def test_a_snapshot_lists_elements_without_viewport_rectangles(bridge):
    view = json.loads(srv.browser("snapshot", tab=13, max_elements=5000))
    assert bridge["calls"] == [("snapshot", 13, 1000, 0)]
    assert view["snapshot"] == "s1-abc" and view["scroll"] == "0 of 900"
    assert view["elements"][0] == {"id": "e1", "kind": "link", "name": "More information"}
    assert view["elements"][1]["item"] == "2 of 5 in g1" and view["elements"][1]["below"]
    assert "rect" not in json.dumps(view)


def test_click_uses_the_latest_snapshot_of_its_tab_and_names_the_element(bridge):
    srv.browser("snapshot", tab=13)
    out = srv.browser("click", element="e1")
    assert bridge["calls"][-1] == ("click", "s1-abc", "e1")
    assert out == ("clicked e1 (link 'More information') in tab 13 ('Other') "
                   "(the human's focus untouched)")


def test_click_without_a_snapshot_is_refused_before_anything_runs(bridge):
    with pytest.raises(ValueError, match="no snapshot to act on"):
        srv.browser("click", element="e1")
    assert bridge["calls"] == []


def test_type_never_repeats_the_text(bridge):
    srv.browser("snapshot", tab=13)
    out = srv.browser("type", element="e2", text="hunter2 is not a secret", submit=True)
    assert bridge["calls"][-1] == ("type", "s1-abc", "e2", "hunter2 is not a secret", True)
    assert "hunter2" not in out and "23 characters" in out and "pressed Enter" in out


def test_acting_on_the_tab_the_human_sees_says_so(bridge):
    srv.browser("snapshot", tab=12)
    assert "on the human's screen" in srv.browser("click", element="e1")


def test_the_guarantee_refuses_the_human_tab_open_and_activate(bridge, monkeypatch):
    monkeypatch.setenv("HYPRUSE_BACKGROUND", "1")
    srv.browser("snapshot", tab=12)
    with pytest.raises(srv.trust.TrustError, match="on the human's screen"):
        srv.browser("click", element="e1")
    with pytest.raises(srv.trust.TrustError, match="background=false is refused"):
        srv.browser("open", url="https://example.com/", background=False)
    with pytest.raises(srv.trust.TrustError, match="background=false is refused"):
        srv.browser("activate", tab=13, background=False)
    # a tab it cannot place is refused too: it may be the human's
    bridge["clients"] = []
    with pytest.raises(srv.trust.TrustError, match="cannot be told"):
        srv.browser("scroll", tab=12)
    srv.browser("snapshot", tab=13)
    assert srv.browser("click", element="e1").startswith("clicked")  # a hidden tab is fine
    assert not any(c[0] == "activate" for c in bridge["calls"])
    # a tab opened behind the active one takes nothing from the human
    assert "not brought forward" in srv.browser("open", url="https://example.com/")


def test_confinement_refuses_acting_but_not_reading(bridge, monkeypatch):
    monkeypatch.setenv("HYPRUSE_CONFINE", "launched")
    assert json.loads(srv.browser("tabs"))
    with pytest.raises(srv.trust.TrustError, match="HYPRUSE_CONFINE"):
        srv.browser("scroll", tab=13)


def test_a_locked_session_refuses_acting(bridge, monkeypatch):
    monkeypatch.setattr(srv.trust, "session_locked", lambda: "hyprlock")
    with pytest.raises(srv.trust.TrustError):
        srv.browser("close", tab=13)


def test_a_dry_run_asks_the_extension_for_nothing_it_would_do(bridge, monkeypatch):
    monkeypatch.setattr(srv.journal, "dry_run", lambda: True)
    assert srv.browser("open", url="https://example.com/", background=False).startswith(
        "DRY RUN")
    assert srv.browser("scroll", tab=13, direction="bottom").startswith("DRY RUN")
    assert bridge["calls"] == []


def test_arguments_are_checked_before_the_browser_is_asked(bridge):
    for kwargs, match in (({"action": "explode"}, "unknown action"),
                          ({"action": "close"}, "needs `tab`"),
                          ({"action": "open"}, "needs `url`"),
                          ({"action": "scroll", "direction": "left"}, "direction"),
                          ({"action": "click"}, "needs `element`"),
                          ({"action": "tabs", "then": "ui"}, "then")):
        with pytest.raises(ValueError, match=match):
            srv.browser(**kwargs)
    assert bridge["calls"] == []


def test_a_stale_snapshot_says_to_take_a_new_one(bridge):
    srv.browser("snapshot", tab=13)
    bridge["fail"] = BrowserError("stale_snapshot", "the page navigated")
    with pytest.raises(ValueError, match="take a new snapshot"):
        srv.browser("click", element="e1")


def test_a_refused_field_is_a_trust_refusal(bridge):
    srv.browser("snapshot", tab=13)
    bridge["fail"] = BrowserError("refused", "a password field")
    with pytest.raises(srv.trust.TrustError, match="password field"):
        srv.browser("type", element="e2", text="x")


def test_then_snapshot_appends_the_page_after_the_action(bridge):
    srv.browser("snapshot", tab=13)
    out = srv.browser("scroll", tab=13, then="snapshot")
    assert out[0].text.startswith("scrolled tab 13") and "now at 720 of 900" in out[0].text
    assert json.loads(out[1].text)["snapshot"] == "s1-abc"


def test_activate_takes_the_human_screen_so_it_needs_consent(bridge):
    with pytest.raises(srv.trust.TrustError, match="takes the human's focus"):
        srv.browser("activate", tab=13)
    assert bridge["calls"] == []


def test_open_puts_the_tab_behind_unless_told_to_bring_it_forward(bridge):
    out = srv.browser("open", url="https://example.com/")
    assert bridge["calls"][-1] == ("open", "https://example.com/", "background")
    assert out.startswith("opened tab 99 behind the active tab") and "not brought forward" in out
    out = srv.browser("open", url="https://example.com/", background=False)
    assert bridge["calls"][-1] == ("open", "https://example.com/", "new")
    assert "in the foreground" in out


def test_an_extension_without_background_open_says_to_update(bridge):
    bridge["old_extension"] = True
    with pytest.raises(ValueError, match="update it, or pass background=false"):
        srv.browser("open", url="https://example.com/")


def test_a_result_warns_when_the_human_focus_moved(bridge):
    bridge["seats"] = [("0xh", 1), ("0xb", 97)]  # before, after: the browser took focus
    out = srv.browser("open", url="https://example.com/", background=False)
    assert "WARNING: the human's focused window changed" in out
    bridge["seats"] = [("0xh", 1), ("0xh", 1)]
    assert "(the human's focus untouched)" in srv.browser("close", tab=13)


def test_open_and_tab_actions_reach_the_extension(bridge):
    assert srv.browser("open", url="https://example.com/", background=False).startswith(
        "opened tab 99")
    srv.browser("back", tab=13)
    srv.browser("close", tab=13)
    assert bridge["calls"] == [("open", "https://example.com/", "new"),
                               ("history", 13, "back"), ("close", 13)]


def test_no_connected_browser_is_a_plain_error(monkeypatch):
    monkeypatch.setattr("hypruse.browser.socket_paths", lambda first=None: [])
    with pytest.raises(ValueError, match="no browser is connected"):
        srv._browser_client(1)


def test_the_tool_is_offered_only_once_the_host_is_registered(monkeypatch, tmp_path):
    monkeypatch.undo()  # the real check, against a home of its own
    assert install.registered(home=tmp_path) is False
    (tmp_path / ".config/chromium/NativeMessagingHosts").mkdir(parents=True)
    (tmp_path / ".config/chromium/NativeMessagingHosts/dev.hypruse.browser.json").write_text("{}")
    assert install.registered(home=tmp_path) is True
    try:
        monkeypatch.setattr(srv, "_browser_enabled", lambda: True)
        monkeypatch.setattr(srv, "_app", None)
        names = {t.name for t in asyncio.run(srv.app().list_tools())}
        assert "browser" in names
    finally:
        monkeypatch.setattr(srv, "_app", None)


def test_browser_is_not_offered_read_only(monkeypatch):
    monkeypatch.setenv("HYPRUSE_READONLY", "1")
    try:
        module = importlib.reload(srv)
        module._browser_enabled = lambda: True
        assert "browser" not in {t.name for t in asyncio.run(module.app().list_tools())}
    finally:
        monkeypatch.delenv("HYPRUSE_READONLY")
        importlib.reload(srv)


def test_replay_refuses_browser_entries_with_the_reason():
    entry = {"tool": "browser", "seq": 4, "args": {"action": "click", "element": "e1"}}
    monkey = cli._unreplayable([entry])
    assert monkey and "snapshot that does not outlive the page" in monkey[0]
