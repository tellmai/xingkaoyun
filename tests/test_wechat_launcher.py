"""Launcher regressions use fake UIA providers and never operate real WeChat."""
from types import SimpleNamespace

import pytest

from yunkaoxing import wechat_launcher as launcher
from yunkaoxing.page import PageError


class FakeElement:
    def __init__(self, text, kind="Text", *, value="", nodes=(), pid=1, rect=(0, 0, 1000, 800), visible=True, action=None):
        self.text, self.nodes, self.pid, self.bounds = text, list(nodes), pid, rect
        self.visible, self.action = visible, action
        self.element_info = SimpleNamespace(control_type=kind, rich_text=value)
        self.iface_value = SimpleNamespace(CurrentValue=value)
        self.clicked = self.focused = self.restored = 0
        self.inputs = []

    def window_text(self): return self.text
    def is_visible(self): return self.visible
    def is_enabled(self): return True
    def is_minimized(self): return False
    def set_focus(self): self.focused += 1
    def restore(self): self.restored += 1
    def process_id(self): return self.pid
    def rectangle(self): return SimpleNamespace(**dict(zip(("left", "top", "right", "bottom"), self.bounds)))
    def descendants(self, control_type=None):
        return [n for n in self.nodes if not control_type or n.element_info.control_type == control_type]
    def click_input(self, **kwargs):
        self.clicked += 1
        self.inputs.append(kwargs)
        if self.action: self.action()


class FakeDesktop:
    def __init__(self, windows): self.items = windows
    def windows(self, title=None, **kwargs):
        return [w for w in self.items if title is None or w.text == title]


class Clock:
    def __init__(self): self.now = 0
    def clock(self): return self.now
    def sleep(self, value): self.now += value


@pytest.fixture(autouse=True)
def process_names(monkeypatch):
    monkeypatch.setattr(launcher, "_process_name", lambda pid: {1: "Weixin.exe", 2: "WeChatAppEx.exe", 3: "other.exe"}[pid])
    monkeypatch.setattr(launcher, "_native_foreground", lambda _: True)


def make_runner(windows, background=False, title="云考星"):
    clock = Clock()
    return launcher._Launcher(FakeDesktop(windows), title, lambda: None, background, 4, clock=clock.clock, sleep=clock.sleep)


def make_hub(nodes):
    return FakeElement("小程序", "Document", value="weixin://resourceid/AppletDiscover/app.html#/home", nodes=nodes)


def test_existing_window_does_not_touch_main():
    main, mini = FakeElement("微信"), FakeElement("云考星", pid=2)
    assert make_runner([main, mini], background=True).run() is mini
    assert not main.clicked and not main.focused


def test_duplicate_mini_window_is_not_guessed():
    with pytest.raises(PageError, match="多个"):
        make_runner([FakeElement("云考星", pid=2), FakeElement("云考星", pid=2)]).run()


def test_wrong_process_with_same_title_is_not_returned():
    with pytest.raises(PageError, match="已登录微信"):
        make_runner([FakeElement("云考星", pid=3)]).run()


def test_background_can_launch_once_then_return_window_for_background_driver():
    windows = []
    mini = FakeElement("云考星", pid=2)
    tile = FakeElement("云考星", action=lambda: windows.append(mini))
    main = FakeElement("微信", nodes=[make_hub([tile])])
    windows.append(main)
    assert make_runner(windows, background=True).run() is mini
    assert main.focused == 1 and tile.clicked == 1


def test_launches_exact_visible_recent_entry_when_common_has_duplicate():
    windows = []
    mini = FakeElement("云考星", pid=2)
    recent = FakeElement("云考星", action=lambda: windows.append(mini))
    common = FakeElement("云考星")
    hub = make_hub([FakeElement("最近使用"), recent, FakeElement("我的常用"), common])
    main = FakeElement("微信", nodes=[hub])
    windows.append(main)
    assert make_runner(windows).run() is mini
    assert recent.clicked == 1 and common.clicked == 0


def test_navigates_accessible_miniprogram_button_then_observes_hub():
    windows = []
    mini = FakeElement("云考星", pid=2)
    tile = FakeElement("云考星", action=lambda: windows.append(mini))
    hub = make_hub([tile])
    main = FakeElement("微信")
    button = FakeElement("小程序", "Button", action=lambda: main.nodes.append(hub))
    main.nodes = [button]
    windows.append(main)
    assert make_runner(windows).run() is mini
    assert button.clicked == 1 and tile.clicked == 1


def test_unknown_name_does_not_click_other_tiles():
    other = FakeElement("其他小程序")
    main = FakeElement("微信", nodes=[make_hub([other])])
    with pytest.raises(PageError, match="未找到「自定义名称」"):
        make_runner([main], title="自定义名称").run()
    assert other.clicked == 0


def test_small_login_dialog_is_never_used_as_main():
    login = FakeElement("微信", rect=(0, 0, 360, 480))
    with pytest.raises(PageError, match="已登录微信"):
        make_runner([login]).run()
    assert login.clicked == 0


def test_outside_entry_is_not_clicked():
    tile = FakeElement("云考星", rect=(2000, 2000, 2200, 2100))
    main = FakeElement("微信", nodes=[make_hub([tile])])
    with pytest.raises(PageError, match="窗口内"):
        make_runner([main]).run()
    assert tile.clicked == 0


def test_ambiguous_recent_entries_are_rejected():
    hub = make_hub([FakeElement("最近使用"), FakeElement("云考星"), FakeElement("云考星"), FakeElement("我的常用")])
    with pytest.raises(PageError, match="入口不唯一"):
        make_runner([FakeElement("微信", nodes=[hub])]).run()


@pytest.mark.parametrize("dpi", [96, 120])
def test_weixin4_weak_native_navigation_uses_internal_zoom_then_requires_verified_hub(monkeypatch, dpi):
    monkeypatch.setattr(launcher, "_window_dpi", lambda _: dpi)
    windows = []
    mini = FakeElement("云考星", pid=2)
    tile = FakeElement("云考星", action=lambda: windows.append(mini))
    hub = make_hub([tile])
    main = FakeElement("微信", rect=(100, 100, 1422, 1187))
    main.action = lambda: setattr(main, "nodes", [hub]) if main.clicked == 2 else None
    windows.append(main)
    assert make_runner(windows).run() is mini
    assert main.inputs == [{"coords": (31, 255)}, {"coords": (130, 295)}]
    assert tile.clicked == 1


def test_native_navigation_stops_after_two_clicks_if_hub_cannot_be_verified(monkeypatch):
    monkeypatch.setattr(launcher, "_window_dpi", lambda _: 120)
    main = FakeElement("微信", rect=(0, 0, 1322, 1087))
    with pytest.raises(PageError, match="未能打开"):
        make_runner([main]).run()
    assert main.clicked == 2


def test_second_native_navigation_is_disabled_when_a_web_document_is_exposed(monkeypatch):
    monkeypatch.setattr(launcher, "_window_dpi", lambda _: 120)
    unrelated = FakeElement("其他网页", "Document", value="https://example.com")
    main = FakeElement("微信", nodes=[unrelated], rect=(0, 0, 1322, 1087))
    with pytest.raises(PageError, match="未能打开"):
        make_runner([main]).run()
    assert main.clicked == 1


def test_minimized_main_is_selected_using_restore_bounds(monkeypatch):
    windows = []
    mini = FakeElement("云考星", pid=2)
    tile = FakeElement("云考星", action=lambda: windows.append(mini))
    main = FakeElement("微信", nodes=[make_hub([tile])], rect=(-32000, -32000, -31840, -31970))
    main.is_minimized = lambda: True
    main.restore = lambda: (setattr(main, "bounds", (0, 0, 1000, 800)), setattr(main, "restored", main.restored + 1))
    monkeypatch.setattr(launcher, "_main_rect", lambda w: (0, 0, 1000, 800) if w is main else launcher._rect(w))
    windows.append(main)
    assert make_runner(windows).run() is mini
    assert main.restored == 1


def test_activation_checks_native_hwnd_when_uia_focus_does_not_activate(monkeypatch):
    main = FakeElement("微信")
    state = {"active": False, "requests": 0}
    monkeypatch.setattr(launcher, "_native_foreground", lambda _: state["active"])
    def native_activate(_):
        state["requests"] += 1
        state["active"] = True
    monkeypatch.setattr(launcher, "_set_native_foreground", native_activate)
    make_runner([main])._activate(main)
    assert state["requests"] == 1


def test_activation_reports_failure_if_native_window_stays_background(monkeypatch):
    main = FakeElement("微信")
    monkeypatch.setattr(launcher, "_native_foreground", lambda _: False)
    monkeypatch.setattr(launcher, "_set_native_foreground", lambda _: False)
    with pytest.raises(PageError, match="切到前台"):
        make_runner([main])._activate(main)


def test_hub_document_before_async_tiles_waits_without_extra_clicks():
    windows = []
    mini = FakeElement("云考星", pid=2)
    tile = FakeElement("云考星", action=lambda: windows.append(mini))
    initial = make_hub([])
    ready = make_hub([tile])
    main = FakeElement("微信", nodes=[initial])
    windows.append(main)
    runner = make_runner(windows)
    clock = Clock()
    def sleep(value):
        clock.sleep(value)
        if clock.now >= .45:
            main.nodes = [ready]
    runner.clock, runner.sleep = clock.clock, sleep
    assert runner.run() is mini
    assert main.clicked == 0 and tile.clicked == 1
    assert clock.now >= .45


def test_missing_async_tile_fails_only_at_deadline_without_clicking_anything():
    main = FakeElement("微信", nodes=[make_hub([])])
    runner = make_runner([main], title="不存在的目标")
    clock = Clock()
    runner.clock, runner.sleep = clock.clock, clock.sleep
    with pytest.raises(PageError, match="加载超时.*未找到「不存在的目标」"):
        runner.run()
    assert clock.now >= runner.deadline
    assert main.clicked == 0
