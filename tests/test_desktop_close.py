"""Close one verified mini-program HWND without accessing the live desktop."""
import sys
from types import SimpleNamespace

import pytest

from yunkaoxing.desktop import DesktopUIA
from yunkaoxing.page import PageError
from yunkaoxing.profile import SiteProfile


class Function:
    def __init__(self, function):
        self.function = function

    def __call__(self, *args):
        return self.function(*args)


def connected_port(monkeypatch, *, pattern=True, close_works=True):
    profile = SiteProfile()
    state = {"alive": True, "pid": 4321, "process": "WeChatAppEx.exe", "events": []}
    text = SimpleNamespace(
        element_info=SimpleNamespace(control_type="Text"),
        is_visible=lambda: True, is_enabled=lambda: True,
        window_text=lambda: "选择区县",
        parent=lambda: SimpleNamespace(element_info=SimpleNamespace(control_type="Document")),
    )
    document = SimpleNamespace(
        element_info=SimpleNamespace(rich_text=""),
        iface_value=SimpleNamespace(CurrentValue=f"https://servicewechat.com/{profile.app_id}/5/page-frame.html"),
        window_text=lambda: "Page-Frame", is_visible=lambda: True, descendants=lambda: [text],
    )
    window = SimpleNamespace(
        handle=1234, process_id=lambda: state["pid"],
        is_minimized=lambda: False, is_visible=lambda: True,
        descendants=lambda **kwargs: [document],
    )

    def close_pattern():
        state["events"].append(("pattern-close", window.handle))
        if close_works:
            state["alive"] = False

    if pattern:
        window.iface_window = SimpleNamespace(Close=close_pattern)

    def is_window(handle):
        assert handle == window.handle
        return state["alive"]

    def get_pid(handle, pointer):
        assert handle == window.handle
        pointer._obj.value = state["pid"]
        return 2468

    def post_message(handle, message, wparam, lparam):
        assert (handle, message, wparam, lparam) == (1234, 0x0010, 0, 0)
        state["events"].append(("wm-close", handle))
        if close_works:
            state["alive"] = False
        return 1

    user32 = SimpleNamespace(IsWindow=Function(is_window), GetWindowThreadProcessId=Function(get_pid),
                             PostMessageW=Function(post_message))
    kernel = SimpleNamespace(CreateMutexW=Function(lambda *args: 99),
                             ReleaseMutex=Function(lambda *args: state["events"].append(("release-lock",))),
                             CloseHandle=Function(lambda *args: None))
    desktop = SimpleNamespace(windows=lambda **kwargs: [window])
    monkeypatch.setitem(sys.modules, "pywinauto", SimpleNamespace(Desktop=lambda **kwargs: desktop))
    monkeypatch.setitem(sys.modules, "pythoncom", SimpleNamespace(CoInitialize=lambda: None, CoUninitialize=lambda: None))
    monkeypatch.setattr("yunkaoxing.desktop.ctypes.WinDLL", lambda name, **kwargs: kernel if name == "kernel32" else user32)
    monkeypatch.setattr("yunkaoxing.desktop.ctypes.get_last_error", lambda: 0)
    port = DesktopUIA(profile, background=True)
    monkeypatch.setattr(port, "_process_name", lambda pid: state["process"])
    port.connect()
    port.snapshot()
    return port, state, document


@pytest.mark.parametrize("pattern, action", [(True, "pattern-close"), (False, "wm-close")])
def test_close_targets_connected_mini_window_and_confirms_native_disappearance(monkeypatch, pattern, action):
    port, state, _ = connected_port(monkeypatch, pattern=pattern)
    port.close_mini_program()
    assert state["events"] == [(action, 1234)]
    assert not state["alive"] and port.window is None
    # Releasing COM/the lock is a separate operation and cannot close a second
    # window or invoke the mini-program's close action again.
    assert port._mutex == 99
    port.close()
    assert state["events"] == [(action, 1234), ("release-lock",)]


def test_resource_cleanup_alone_leaves_mini_program_running(monkeypatch):
    port, state, _ = connected_port(monkeypatch)
    port.close()
    assert state["alive"]
    assert state["events"] == [("release-lock",)]


@pytest.mark.parametrize("changed", ["appid", "process"])
def test_close_refuses_a_target_whose_verified_identity_has_changed(monkeypatch, changed):
    port, state, document = connected_port(monkeypatch)
    if changed == "appid":
        document.iface_value.CurrentValue = "https://servicewechat.com/another-app/5/page-frame.html"
    else:
        state["process"] = "Weixin.exe"
    with pytest.raises(PageError, match="应用身份|进程已变化"):
        port.close_mini_program()
    assert state["alive"] and state["events"] == []


def test_close_is_not_reported_successful_while_native_window_remains(monkeypatch):
    port, state, _ = connected_port(monkeypatch, close_works=False)
    with pytest.raises(PageError, match="关闭超时"):
        port.close_mini_program(timeout=0)
    assert state["alive"] and state["events"] == [("pattern-close", 1234)]
    assert port.window is not None


def test_uncertain_windowpattern_error_does_not_send_an_extra_wm_close(monkeypatch):
    port, state, _ = connected_port(monkeypatch)

    def uncertain():
        state["events"].append(("pattern-close", 1234))
        raise RuntimeError("provider disconnected")

    port.window.iface_window.Close = uncertain
    with pytest.raises(PageError, match="关闭结果无法确认"):
        port.close_mini_program(timeout=0)
    assert state["events"] == [("pattern-close", 1234)]


def test_close_does_not_discover_or_open_a_window_when_disconnected(monkeypatch):
    port = DesktopUIA(SiteProfile())
    monkeypatch.setattr(port, "connect", lambda: pytest.fail("closing must never reopen the app"))
    with pytest.raises(PageError, match="没有已连接"):
        port.close_mini_program()
