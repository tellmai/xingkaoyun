"""Exercise transport policy with fake UIA providers, without touching live Windows."""
import json
import sys
from types import SimpleNamespace

import pytest

from yunkaoxing.desktop import DesktopUIA
from yunkaoxing.page import Node, PageError, Snapshot
from yunkaoxing.profile import SiteProfile


class FakeWindow:
    def __init__(self, *, minimized=False, visible=True, active=False, focus_works=True, docs=()):
        self.minimized, self.visible, self.active = minimized, visible, active
        self.focus_works, self.docs = focus_works, docs
        self.restores = self.focuses = 0

    def is_minimized(self):
        return self.minimized

    def restore(self):
        self.restores += 1
        self.minimized, self.visible = False, True

    def is_visible(self):
        return self.visible

    def is_active(self):
        return self.active

    def set_focus(self):
        self.focuses += 1
        if self.focus_works:
            self.active = True

    def process_id(self):
        return 123

    def descendants(self, **kwargs):
        return self.docs


class FakeValue:
    def __init__(self, value=""):
        self.CurrentValue = value
        self.writes = []

    def SetValue(self, value):
        self.writes.append(value)
        self.CurrentValue = value


class FakeControl:
    def __init__(self, kind="Button", text="安全登录", value="", *, enabled=True, visible=True):
        self.element_info = SimpleNamespace(control_type=kind, rich_text="")
        self.text, self.enabled, self.visible = text, enabled, visible
        self.iface_value = FakeValue(value)
        self.invokes = self.toggles = self.selections = 0

    def is_visible(self):
        return self.visible

    def is_enabled(self):
        return self.enabled

    def window_text(self):
        return self.text

    def parent(self):
        return SimpleNamespace(element_info=SimpleNamespace(control_type="Group"))

    def get_value(self):
        return self.iface_value.CurrentValue

    def invoke(self):
        self.invokes += 1

    def select(self):
        self.selections += 1

    def toggle(self):
        self.toggles += 1


class FakeDocument:
    def __init__(self, controls, app_id=SiteProfile().app_id):
        self.controls = controls
        self.element_info = SimpleNamespace(rich_text="")
        self.iface_value = FakeValue(f"https://servicewechat.com/{app_id}/5/page-frame.html")

    def window_text(self):
        return "Page-Frame"

    def is_visible(self):
        return True

    def descendants(self):
        return self.controls


def port_with_control(background=False, *, kind="Button", text="安全登录", selected=None):
    port = DesktopUIA(SiteProfile(window_title="自定义考试小程序"), background=background)
    port.window = FakeWindow(active=False)
    control = FakeControl(kind, text, "旧字段内容")
    port._wrappers = {"0": control}
    port._token = 5
    node = Node("0", kind, text, selected=selected)
    return port, control, Snapshot((node,), 5), node


def test_visible_mode_restores_and_activates_selected_window():
    port = DesktopUIA(SiteProfile())
    port.window = FakeWindow(minimized=True, visible=False)
    port._prepare_window(activate=True)
    assert port.window.restores == 1 and port.window.focuses == 1
    assert port.window.is_visible() and port.window.is_active()


def test_background_mode_never_activates_or_restores():
    port = DesktopUIA(SiteProfile(window_title="自定义考试小程序"), background=True)
    port.window = FakeWindow(active=False)
    port._prepare_window(activate=True)
    assert port.window.focuses == 0 and port.window.restores == 0
    port.window.minimized = True
    with pytest.raises(PageError, match="自定义考试小程序.*已最小化"):
        port._prepare_window(activate=True)
    assert port.window.focuses == 0 and port.window.restores == 0


def test_background_cannot_use_an_invisible_provider():
    port = DesktopUIA(SiteProfile(window_title="改名后的窗口"), background=True)
    port.window = FakeWindow(visible=False)
    with pytest.raises(PageError, match="改名后的窗口.*不可见"):
        port.snapshot()


def test_foreground_failure_does_not_send_input():
    port, control, snapshot, node = port_with_control()
    port.window.focus_works = False
    with pytest.raises(PageError, match="无法将.*切到前台"):
        port.click(snapshot, node)
    assert control.invokes == 0


@pytest.mark.parametrize("background, expected_focus, expected_sleeps", [(False, 1, [.05]), (True, 0, [])])
def test_inputs_respect_foreground_and_pacing(background, expected_focus, expected_sleeps, monkeypatch):
    sleeps = []
    monkeypatch.setattr("yunkaoxing.desktop.time.sleep", sleeps.append)
    port, control, snapshot, node = port_with_control(background)
    port.click(snapshot, node)
    assert control.invokes == 1 and port.window.focuses == expected_focus
    assert sleeps == expected_sleeps


def test_reading_background_page_never_changes_focus():
    control = FakeControl("Edit", "请输入真实姓名", "旧姓名")
    port = DesktopUIA(SiteProfile(), background=True)
    port.window = FakeWindow(docs=[FakeDocument([control])])
    snapshot = port.snapshot()
    assert snapshot.nodes[0].value == "旧姓名"
    assert port.window.focuses == port.window.restores == 0


def test_app_identity_is_checked_even_after_window_rename():
    port = DesktopUIA(SiteProfile(window_title="改名后的窗口"), background=True)
    port.window = FakeWindow(docs=[FakeDocument([FakeControl()], app_id="another-app")])
    with pytest.raises(PageError, match="改名后的窗口.*应用身份"):
        port.snapshot()


def test_edit_uses_full_value_replacement_without_focus_or_text_splicing():
    port, control, snapshot, node = port_with_control(True, kind="Edit", text="姓名")
    port.edit(snapshot, node, "新姓名")
    assert control.iface_value.CurrentValue == "新姓名"
    assert control.iface_value.writes == ["新姓名"]
    assert port.window.focuses == 0


def test_stale_or_changed_controls_receive_no_input():
    port, control, snapshot, node = port_with_control(True)
    control.text = "其他操作"
    with pytest.raises(PageError, match="控件文字"):
        port.click(snapshot, node)
    assert control.invokes == 0
    control.text = node.text
    port._token += 1
    with pytest.raises(PageError, match="旧控件"):
        port.click(snapshot, node)
    assert control.invokes == 0


def test_stop_signal_is_not_reclassified_as_a_window_error():
    class StopSignal(Exception):
        pass
    def stopped():
        raise StopSignal()
    port = DesktopUIA(SiteProfile(), before_input=stopped)
    port.window = FakeWindow()
    with pytest.raises(StopSignal):
        port._prepare_window(activate=True)


def test_profile_accepts_custom_name_but_keeps_process_and_app_identity(tmp_path):
    path = tmp_path / "profile.json"
    path.write_text(json.dumps({"window_title": "本地改名的考试入口"}), encoding="utf-8")
    profile = SiteProfile.load(path)
    assert profile.window_title == "本地改名的考试入口"
    assert profile.home_buttons and profile.login_error_close
    path.write_text('{"window_title": "   "}', encoding="utf-8")
    with pytest.raises(ValueError, match="window_title"):
        SiteProfile.load(path)
    path.write_text('{"window_title":"任意名字","app_id":"another-app"}', encoding="utf-8")
    with pytest.raises(ValueError, match="应用身份"):
        SiteProfile.load(path)


def test_connect_queries_the_configured_exact_name_and_prepares_visible_window(monkeypatch):
    # Test connect's matching policy without loading pywinauto, COM or kernel32.
    window = FakeWindow(minimized=True)
    queries = []
    fake_desktop = SimpleNamespace(windows=lambda **kwargs: queries.append(kwargs) or [window])
    monkeypatch.setitem(sys.modules, "pywinauto", SimpleNamespace(Desktop=lambda **kwargs: fake_desktop))
    monkeypatch.setitem(sys.modules, "pythoncom", SimpleNamespace(CoInitialize=lambda: None, CoUninitialize=lambda: None))
    class FakeFunction:
        def __init__(self, value): self.value = value
        def __call__(self, *args): return self.value
    kernel = SimpleNamespace(CreateMutexW=FakeFunction(1), CloseHandle=FakeFunction(None))
    monkeypatch.setattr("yunkaoxing.desktop.ctypes.WinDLL", lambda *args, **kwargs: kernel)
    monkeypatch.setattr("yunkaoxing.desktop.ctypes.get_last_error", lambda: 0)
    port = DesktopUIA(SiteProfile(window_title="重命名入口"))
    monkeypatch.setattr(port, "_process_name", lambda pid: "WeChatAppEx.exe")
    port.connect()
    assert queries == [{"title": "重命名入口", "visible_only": False}]
    assert port.window is window and window.restores == 1 and window.focuses == 1
