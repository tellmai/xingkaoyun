"""Open a named mini-program through the logged-in desktop WeChat UI.

The launcher never constructs a private WeChat URL or reads account/session files.
The returned window is still checked against its application document by DesktopUIA.
"""
import ctypes
import os
import sys
import time
from ctypes import wintypes

from .engine import Stopped
from .page import PageError


def _process_name(pid):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return ""
        return os.path.basename(buffer.value)
    finally:
        kernel.CloseHandle(handle)


def _rect(wrapper):
    r = wrapper.rectangle()
    return r.left, r.top, r.right, r.bottom


def _window_dpi(wrapper):
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetDpiForWindow.argtypes = [wintypes.HWND]
    user32.GetDpiForWindow.restype = wintypes.UINT
    return user32.GetDpiForWindow(wrapper.handle) or 96


def _native_foreground(wrapper):
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.IsChild.argtypes = [wintypes.HWND, wintypes.HWND]
    user32.IsChild.restype = wintypes.BOOL
    current = user32.GetForegroundWindow()
    handle = wrapper.handle
    return bool(current and (current == handle or user32.GetAncestor(current, 2) == handle or user32.IsChild(handle, current)))


def _set_native_foreground(wrapper):
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    return bool(user32.SetForegroundWindow(wrapper.handle))


def _main_rect(wrapper):
    """Use the restore bounds to distinguish a minimized main from sign-in."""
    if not wrapper.is_minimized():
        return _rect(wrapper)

    class Placement(ctypes.Structure):
        _fields_ = [("length", wintypes.UINT), ("flags", wintypes.UINT),
                    ("showCmd", wintypes.UINT), ("ptMinPosition", wintypes.POINT),
                    ("ptMaxPosition", wintypes.POINT), ("rcNormalPosition", wintypes.RECT)]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetWindowPlacement.argtypes = [wintypes.HWND, ctypes.POINTER(Placement)]
    user32.GetWindowPlacement.restype = wintypes.BOOL
    placement = Placement()
    placement.length = ctypes.sizeof(placement)
    if not user32.GetWindowPlacement(wrapper.handle, ctypes.byref(placement)):
        return _rect(wrapper)
    r = placement.rcNormalPosition
    return r.left, r.top, r.right, r.bottom


def _visible(wrapper):
    try:
        l, t, r, b = _rect(wrapper)
        return wrapper.is_visible() and wrapper.is_enabled() and r > l and b > t
    except Exception:
        return False


def _value(wrapper):
    try:
        return wrapper.iface_value.CurrentValue or ""
    except Exception:
        return getattr(wrapper.element_info, "rich_text", "") or ""


class _Launcher:
    def __init__(self, desktop, title, before_input, background, timeout, *, clock=time.monotonic, sleep=time.sleep):
        self.desktop, self.title = desktop, title
        self.before_input, self.background = before_input, background
        self.clock, self.sleep = clock, sleep
        self.deadline = clock() + max(1, timeout)

    def _mini_windows(self):
        return [w for w in self.desktop.windows(title=self.title, visible_only=False)
                if _process_name(w.process_id()).casefold() == "wechatappex.exe"]

    def _existing(self):
        windows = self._mini_windows()
        if len(windows) > 1:
            raise PageError(f"检测到多个「{self.title}」小程序窗口，请关闭重复窗口后重试")
        return windows[0] if windows else None

    @staticmethod
    def _hub(main):
        docs = main.descendants(control_type="Document")
        matches = [d for d in docs if d.window_text().strip() == "小程序" and _visible(d)
                   and "weixin://resourceid/AppletDiscover/" in _value(d)]
        if len(matches) > 1:
            raise PageError("微信存在多个可见小程序列表，无法确认启动入口")
        return matches[0] if matches else None

    def _click(self, main, target):
        if not _visible(target):
            raise PageError("微信启动入口已变化，请重新打开小程序列表后重试")
        l, t, r, b = _rect(main)
        tl, tt, tr, tb = _rect(target)
        x, y = (tl + tr) // 2, (tt + tb) // 2
        if not (l <= x < r and t <= y < b):
            raise PageError("微信启动入口不在当前窗口内，未发送点击")
        self.before_input()
        # Text tiles in the WeChat hub do not consistently expose Invoke.
        # This click is bounded to the freshly observed, exact named tile.
        target.click_input()

    def _named(self, main, name):
        return [n for n in main.descendants() if _visible(n)
                and n.element_info.control_type in {"Button", "Text", "ListItem", "Hyperlink"}
                and n.window_text().strip() == name]

    def _wait_hub(self, main, seconds=3):
        until = min(self.deadline, self.clock() + seconds)
        while self.clock() < until:
            self.before_input()
            hub = self._hub(main)
            if hub is not None:
                return hub
            self.sleep(.15)
        return None

    def _activate(self, main):
        self.before_input()
        # UIA focus and OS foreground are different in Weixin's hosted webviews.
        # Request UIA activation, but confirm with the actual top-level HWND.
        try:
            main.set_focus()
        except Exception:
            pass
        if _native_foreground(main):
            return
        self.before_input()
        _set_native_foreground(main)
        until = min(self.deadline, self.clock() + 1)
        while self.clock() < until:
            self.before_input()
            if _native_foreground(main):
                return
            self.sleep(.05)
        raise PageError("无法将微信主窗口切到前台，请恢复微信主窗口后重试")

    @staticmethod
    def _native_scale(main):
        if _process_name(main.process_id()).casefold() != "weixin.exe":
            return None
        l, t, r, b = _rect(main)
        dpi = _window_dpi(main)
        if not 96 <= dpi <= 384:
            return None
        # The observed Weixin 4 UI has an internal 125% layout even though its
        # native HWND reports 96 DPI. Honor that layout floor independently of
        # OS DPI; at 96/120 DPI the observed nav centers are (31,255)/(130,295).
        scale = max(1.25, dpi / 96)
        return scale if r - l >= 600 * scale and b - t >= 450 * scale else None

    def _open_hub(self, main):
        hub = self._hub(main)
        if hub is not None:
            return hub
        direct = self._named(main, "小程序")
        if len(direct) == 1:
            self._click(main, direct[0])
            hub = self._wait_hub(main)
            if hub is not None:
                return hub
        discovery = self._named(main, "发现")
        native_scale = None
        if len(discovery) == 1:
            self._click(main, discovery[0])
        elif not discovery and _process_name(main.process_id()).casefold() == "weixin.exe":
            # Weixin 4's native sidebar has no accessible children in some builds.
            # Use its observed fixed layout only inside a full-size main window;
            # never apply it to the separate WeChat sign-in dialog.
            native_scale = self._native_scale(main)
            if native_scale is None:
                raise PageError("微信当前不是已登录的主窗口，请先登录微信后重试")
            self.before_input()
            main.click_input(coords=(round(25 * native_scale), round(204 * native_scale)))
        else:
            raise PageError("未找到微信的小程序入口，请将微信切到「发现 → 小程序」后重试")
        # Re-observe the destination before acting on the second navigation item.
        until = min(self.deadline, self.clock() + 3)
        while self.clock() < until:
            self.before_input()
            direct = self._named(main, "小程序")
            if len(direct) == 1:
                self._click(main, direct[0])
                hub = self._wait_hub(main)
                if hub is not None:
                    return hub
                break
            self.sleep(.15)
        # In the observed Weixin 4 native discovery page, both its sidebar and
        # menu lack UIA children. Its internal 125% layout places 小程序 at
        # (130,295), even with the HWND reporting 96 DPI. Only perform this
        # second, reversible navigation after the bounded first navigation,
        # with no exposed web document. Require the hub's official local URL
        # immediately afterwards before selecting any named mini-program.
        if native_scale is not None and not any(_visible(d) for d in main.descendants(control_type="Document")):
            if self._native_scale(main) == native_scale and self.clock() < self.deadline:
                self.before_input()
                main.click_input(coords=(round(104 * native_scale), round(236 * native_scale)))
                hub = self._wait_hub(main, seconds=5)
                if hub is not None:
                    return hub
        raise PageError("未能打开微信的小程序列表，请检查「发现 → 小程序」菜单后重试；未点击其他小程序")

    def _entry(self, hub):
        nodes = [n for n in hub.descendants() if _visible(n)]
        matches = [(i, n) for i, n in enumerate(nodes)
                   if n.element_info.control_type in {"Text", "Button", "ListItem", "Hyperlink"}
                   and n.window_text().strip() == self.title]
        if not matches:
            return None
        if len(matches) == 1:
            return matches[0][1]
        start = next((i for i, n in enumerate(nodes) if n.window_text().strip() == "最近使用"), None)
        end = next((i for i, n in enumerate(nodes) if n.window_text().strip() == "我的常用"), len(nodes))
        recent = [n for i, n in matches if start is not None and start < i < end]
        if len(recent) == 1:
            return recent[0]
        raise PageError(f"微信列表中「{self.title}」入口不唯一，请保留明确的最近使用入口后重试")

    def _wait_entry(self, main):
        # The hub's URL/document appears before its asynchronously loaded tiles.
        # Refresh both the main's current document and that document's children
        # on every poll; never click/re-open navigation while waiting for a tile.
        while self.clock() < self.deadline:
            self.before_input()
            try:
                hub = self._hub(main)
                entry = self._entry(hub) if hub is not None else None
            except PageError:
                # Ambiguous documents/entries remain a hard failure.
                raise
            except Exception:
                # A replaced document can invalidate its prior UIA wrapper.
                entry = None
            if entry is not None:
                return entry
            self.sleep(.15)
        raise PageError(f"等待小程序列表加载超时，仍未找到「{self.title}」；请先在微信使用一次该小程序，并核对名称设置")

    def run(self):
        existing = self._existing()
        if existing is not None:
            return existing
        mains = [w for w in self.desktop.windows(visible_only=False)
                 if w.window_text().strip() in {"微信", "Weixin", "WeChat"}
                 and _process_name(w.process_id()).casefold() in {"weixin.exe", "wechat.exe"}]
        # Exclude small, standalone sign-in windows from a logged-in main session.
        mains = [w for w in mains if _main_rect(w)[2] - _main_rect(w)[0] >= 600 and _main_rect(w)[3] - _main_rect(w)[1] >= 450]
        if len(mains) != 1:
            raise PageError("未找到唯一的已登录微信主窗口，请先登录微信并打开主窗口")
        main = mains[0]
        # Even a background batch needs one visible WeChat navigation when the
        # mini-program has not been started. DesktopUIA resumes background mode
        # after this upfront launch; existing mini windows are never raised here.
        self.before_input()
        if main.is_minimized():
            main.restore()
        self._activate(main)
        self._open_hub(main)
        self._click(main, self._wait_entry(main))
        while self.clock() < self.deadline:
            self.before_input()
            result = self._existing()
            if result is not None:
                return result
            self.sleep(.2)
        raise PageError(f"已点击微信中的「{self.title}」，但小程序启动超时，请检查微信提示后重试")


def launch_mini_program(title, before_input=lambda: None, background=False, timeout=20):
    """Return an existing or newly opened, process-verified UIA mini window."""
    if sys.platform != "win32":
        raise PageError("微信小程序自动启动需要 Windows 10/11")
    if not title or not title.strip():
        raise PageError("请填写微信小程序名称")
    from pywinauto import Desktop
    try:
        return _Launcher(Desktop(backend="uia"), title.strip(), before_input, background, timeout).run()
    except (PageError, Stopped):
        raise
    except Exception as exc:
        raise PageError(f"无法从微信启动「{title}」，请检查微信主窗口及小程序列表后重试") from exc
