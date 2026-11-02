"""Windows UI Automation transport. No private WeChat protocol or session extraction."""
import ctypes
import os
import sys
import time
from ctypes import wintypes

from .page import Node, PageError, Snapshot


class _AmbiguousDocument(PageError):
    """No cached business document has reliable evidence of being current."""


class DesktopUIA:
    def __init__(self, profile, before_input=lambda: None, background=False):
        self.profile, self.before_input = profile, before_input
        self.background = background
        self.window = None
        self._wrappers = {}
        self._labels = {}
        self._root = None
        self._token = 0
        self._mutex = None
        self._com = None
        self._connected_window = None
        self._connected_identity = None

    def connect(self):
        self.profile.validate()
        if sys.platform != "win32":
            raise PageError("小程序控制需要 Windows 10/11")
        import pythoncom
        pythoncom.CoInitialize()
        self._com = pythoncom
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.CreateMutexW.restype = wintypes.HANDLE
        self._mutex = kernel.CreateMutexW(None, True, "Local\\YunKaoXingDesktopAutomation")
        if not self._mutex:
            raise PageError("无法建立小程序控制锁")
        if ctypes.get_last_error() == 183:
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle(self._mutex)
            self._mutex = None
            raise PageError("另一个云考星助手正在控制小程序，请先停止该任务")
        from pywinauto import Desktop
        # Include minimized windows so the visible mode can restore the selected
        # mini-program, rather than silently declaring that it is not running.
        windows = Desktop(backend="uia").windows(title=self.profile.window_title, visible_only=False)
        windows = [w for w in windows if self._process_name(w.process_id()).casefold() == self.profile.process_name.casefold()]
        if not windows:
            from .wechat_launcher import launch_mini_program
            windows = [launch_mini_program(self.profile.window_title, self.before_input, self.background)]
        if len(windows) != 1:
            raise PageError(f"请在已登录的微信中打开且只保留一个「{self.profile.window_title}」小程序窗口")
        self.window = windows[0]
        self._connected_window = self.window
        self._connected_identity = (getattr(self.window, "handle", None), self.window.process_id())
        self._prepare_window(activate=not self.background)

    def _is_foreground(self):
        """UIA focus can remain in a child document after native activation."""
        handle = getattr(self.window, "handle", None)
        if not handle:
            return self.window.is_active()  # Also permits fake providers in tests.
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.GetForegroundWindow.restype = wintypes.HWND
        user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user.GetAncestor.restype = wintypes.HWND
        foreground = user.GetForegroundWindow()
        return bool(foreground and user.GetAncestor(foreground, 2) == user.GetAncestor(handle, 2))

    def _activate(self):
        if self._is_foreground():
            return
        self.window.set_focus()
        deadline = time.monotonic() + .6
        while not self._is_foreground() and time.monotonic() < deadline:
            time.sleep(.03)
        if not self._is_foreground():
            raise PageError(f"无法将「{self.profile.window_title}」切到前台，请手动切到小程序后重试")

    def _is_minimized(self):
        try:
            return bool(self.window.is_minimized())
        except Exception:
            # WeChat versions can omit the UIA Window pattern while retaining a
            # native top-level HWND. IsIconic is a read-only state check.
            handle = getattr(self.window, "handle", None)
            if not handle:
                raise PageError(f"无法确认「{self.profile.window_title}」窗口状态，请恢复窗口后重试")
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.IsIconic.argtypes = [wintypes.HWND]
            user32.IsIconic.restype = wintypes.BOOL
            return bool(user32.IsIconic(handle))

    def _prepare_window(self, *, activate=False):
        """Visible execution activates; background execution never steals focus.

        Background UIA may work while another window covers the mini-program.
        A hidden/minimized provider is not treated as a reliable background page.
        """
        title = self.profile.window_title
        if self.window is None:
            raise PageError(f"尚未连接「{title}」窗口")
        if activate and not self.background:
            self.before_input()
        try:
            if self._is_minimized():
                if self.background or not activate:
                    raise PageError(f"「{title}」窗口已最小化，无法可靠读取控件；请恢复窗口后重试，后台运行可被其他窗口遮挡")
                self.window.restore()
            if not self.window.is_visible():
                raise PageError(f"「{title}」窗口不可见，请恢复小程序窗口后重试；后台运行不会自动隐藏真实微信窗口")
            if activate and not self.background:
                self._activate()
        except PageError:
            raise
        except Exception as exc:
            raise PageError(f"无法准备「{title}」窗口，请恢复窗口后重试") from exc

    def _after_input(self):
        if not self.background:
            time.sleep(.05)

    @staticmethod
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

    def snapshot(self):
        self._prepare_window(activate=not self.background)
        try:
            docs = self.window.descendants(control_type="Document")
            roots = [d for d in docs if d.window_text() == self.profile.document_name and d.is_visible()]
            if not roots:
                raise PageError(f"「{self.profile.window_title}」控件树尚未就绪，请恢复小程序窗口后重试")
            try:
                root = self._current_document(roots)
            except _AmbiguousDocument:
                modal = self._known_native_modal(roots)
                if modal is None:
                    raise
                owner, controls = modal
                url, root = self._document_url(owner), None
            else:
                url = self._document_url(root)
                if not url or url == self.profile.document_name:
                    # Some versions expose Value only on AppIndex while a new
                    # Page-Frame is becoming ready. Verify that owning document.
                    try:
                        owner = root.parent()
                        if owner.window_text() == "AppIndex":
                            url = self._document_url(owner) or url
                    except Exception:
                        pass
                controls = self._page_controls(root)
            if self.profile.app_id not in url:
                raise PageError(f"无法核对「{self.profile.window_title}」文档的应用身份，已停止输入")
            wrappers, labels, nodes = {}, {}, []
            for wrapper in controls:
                if not wrapper.is_visible():
                    continue
                kind = wrapper.element_info.control_type
                if kind not in {"Text", "Button", "Edit", "CheckBox", "RadioButton", "ListItem", "Hyperlink", "ComboBox"}:
                    continue
                if kind != "Text" and not self._in_view(wrapper):
                    # Chromium can expose all 37 district buttons as visible,
                    # including rows below the scroll viewport. Such controls
                    # must be found by search/scroll before becoming actionable.
                    continue
                # Keep the outer semantic label, but operate/read the deepest
                # equivalent native control. The outer Edit often reports only
                # a placeholder; its inner Edit is the actual input element.
                parent = wrapper.parent()
                if kind in {"Edit", "CheckBox", "RadioButton"} and parent.element_info.control_type == kind:
                    continue
                target = self._inner_control(wrapper, kind)
                text = wrapper.window_text().strip()
                value, selected = "", None
                if kind == "Edit":
                    try:
                        value = target.get_value()
                    except Exception:
                        pass
                    text = text or self._value(wrapper) or value
                if kind in {"CheckBox", "RadioButton"}:
                    try:
                        selected = bool(target.get_toggle_state()) if kind == "CheckBox" else bool(target.is_selected())
                    except Exception:
                        # Some ARIA controls expose SelectionItem instead of Toggle.
                        try:
                            selected = bool(target.is_selected())
                        except Exception:
                            pass
                key = str(len(nodes))
                wrappers[key], labels[key] = target, wrapper
                nodes.append(Node(key, kind, text, value, selected, target.is_enabled()))
            if not nodes:
                raise PageError(f"「{self.profile.window_title}」未提供可读取的控件，请恢复或重新打开小程序")
            self._token += 1
            self._wrappers = wrappers
            self._labels, self._root = labels, root
            return Snapshot(tuple(nodes), self._token)
        except PageError:
            raise
        except Exception as exc:
            raise PageError(f"读取「{self.profile.window_title}」失败，窗口可能已关闭或页面正在切换") from exc

    @staticmethod
    def _document_url(document):
        url = getattr(document.element_info, "rich_text", "") or ""
        try:
            return document.iface_value.CurrentValue or url
        except Exception:
            return url

    @staticmethod
    def _overlay_controls(owner):
        controls, pending = [], list(reversed(owner.children()))
        while pending:
            child = pending.pop()
            if child.element_info.control_type == "Document":
                continue
            controls.append(child)
            pending.extend(reversed(child.children()))
        return controls

    @classmethod
    def _page_controls(cls, root):
        controls = list(root.descendants())
        try:
            owner = root.parent()
            if owner.window_text() != "AppIndex":
                return controls
            # wx.showModal dialogs are siblings of Page-Frame in AppIndex,
            # unlike page-owned personal sheets. Retained Document subtrees
            # must remain excluded, but native overlays must be observable.
            controls.extend(cls._overlay_controls(owner))
        except AttributeError:
            pass
        return controls

    def _known_native_modal(self, roots):
        # A full-window native mask can intercept every hit-test while cached
        # Page-Frames have identical bounds. Expose only a recognized native
        # dialog; do not infer which business page sits underneath the mask.
        if not self._is_foreground():
            return None
        owners = []
        for root in roots:
            owner = root.parent()
            if owner.window_text() != "AppIndex" or owner.element_info.control_type != "Document":
                return None
            if not any(self._same_element(owner, previous) for previous in owners):
                owners.append(owner)
        if len(owners) != 1:
            return None
        owner = owners[0]
        if self.profile.app_id not in self._document_url(owner):
            raise PageError(f"无法核对「{self.profile.window_title}」弹窗的应用身份，已停止输入")
        items = [node for node in self._overlay_controls(owner)
                 if node.element_info.control_type in {"Text", "Button"}
                 and node.is_visible() and self._in_view(node)]
        text = [node.window_text().strip() for node in items]
        candidates = []
        titles = [i for i, node in enumerate(items) if node.element_info.control_type == "Text"
                  and text[i] in self.profile.login_error_titles]
        for number, start in enumerate(titles):
            end = titles[number + 1] if number + 1 < len(titles) else len(items)
            bodies = [i for i in range(start + 1, end) if items[i].element_info.control_type == "Text"
                      and any(error in text[i] for error in self.profile.login_errors)]
            if bodies:
                closes = [i for i in range(bodies[-1] + 1, end)
                          if text[i] in self.profile.login_error_close and items[i].is_enabled()]
                buttons = [i for i in closes if items[i].element_info.control_type == "Button"]
                closes = buttons or closes
                if len(closes) == 1:
                    candidates.append(items[start:closes[0] + 1])
        for start, node in enumerate(items):
            if node.element_info.control_type != "Text" or text[start] not in self.profile.logout_prompts:
                continue
            closes = [i for i in range(start + 1, len(items)) if items[i].is_enabled()
                      and text[i] in {"确定", *self.profile.logout_confirm}]
            if len(closes) == 1:
                candidates.append(items[start:closes[0] + 1])
        return (owner, candidates[0]) if len(candidates) == 1 else None

    @staticmethod
    def _value(wrapper):
        try:
            return wrapper.get_value() or ""
        except Exception:
            return ""

    @staticmethod
    def _inner_control(wrapper, kind):
        if kind not in {"Edit", "CheckBox", "RadioButton"}:
            return wrapper
        current = wrapper
        for _ in range(5):
            try:
                children = [c for c in current.children() if c.element_info.control_type == kind and c.is_visible()]
            except (AttributeError, TypeError):
                break
            if len(children) != 1:
                break
            current = children[0]
        return current

    @staticmethod
    def _same_element(left, right):
        try:
            left_id = tuple(left.element_info.runtime_id)
            right_id = tuple(right.element_info.runtime_id)
            if left_id and right_id and any(left_id) and any(right_id):
                return left_id == right_id
        except Exception:
            pass
        try:
            left_element = getattr(left.element_info, "element", None)
            right_element = getattr(right.element_info, "element", None)
            if left_element is not None and right_element is not None:
                # UIA can return no RuntimeId while parent() produces separate
                # wrappers for the same native AppIndex. Use UIA's comparison,
                # not Python metadata equality or an empty ID as identity proof.
                from pywinauto.uia_defines import IUIA
                return bool(IUIA().iuia.CompareElements(left_element, right_element))
        except Exception:
            pass
        return left is right

    @staticmethod
    def _overlap(rect, window_rect):
        return max(0, min(rect.right, window_rect.right) - max(rect.left, window_rect.left)) * max(0, min(rect.bottom, window_rect.bottom) - max(rect.top, window_rect.top))

    def _document_at_point(self, x, y, roots):
        """Hit-test the rendered page instead of trusting cached document order."""
        from pywinauto.uia_defines import IUIA
        uia = IUIA()
        element = uia.iuia.ElementFromPoint(wintypes.POINT(x, y))
        wanted = {tuple(root.element_info.runtime_id): root for root in roots}
        for _ in range(40):
            if not element:
                break
            rid = tuple(element.GetRuntimeId())
            if rid in wanted:
                return wanted[rid]
            element = uia.iuia.RawViewWalker.GetParentElement(element)
        return None

    def _current_document(self, roots):
        if len(roots) == 1:
            return roots[0]
        try:
            rect = self.window.rectangle()
            # Cached pages may still claim IsOffscreen=False. Their bounds are
            # translated outside the client viewport when they are not active.
            measured = [(self._overlap(r.rectangle(), rect), r) for r in roots]
            valid = [(area, r) for area, r in measured if area > 0]
            if not valid:
                raise PageError("页面正在切换，尚未找到当前显示的文档")
            if self._is_foreground():
                for fx, fy in ((.5, .45), (.5, .7), (.25, .35)):
                    hit = self._document_at_point(int(rect.left + (rect.right-rect.left)*fx), int(rect.top + (rect.bottom-rect.top)*fy), roots)
                    if hit is not None:
                        return hit
            best = max(area for area, _ in valid)
            matches = [r for area, r in valid if area == best]
            if len(matches) == 1:
                return matches[0]
            raise _AmbiguousDocument("微信保留了多个页面，暂时无法确认当前显示页；请将小程序切到前台重试")
        except PageError:
            raise
        except Exception as exc:
            raise PageError("无法确认小程序当前页面，未读取缓存页面") from exc

    def _resolve(self, snapshot, node):
        self.before_input()
        if snapshot.token != self._token or node.key not in self._wrappers:
            raise PageError("页面已刷新，旧控件已失效")
        self._prepare_window(activate=not self.background)
        wrapper = self._wrappers[node.key]
        if not wrapper.is_visible() or not wrapper.is_enabled():
            raise PageError(f"「{self.profile.window_title}」目标控件已不可用，请重新读取页面")
        if wrapper.element_info.control_type != node.kind:
            raise PageError("当前控件类型与读取结果不一致，未发送输入")
        label = self._labels.get(node.key, wrapper)
        if node.kind != "Edit" and label.window_text().strip() != node.text.strip():
            raise PageError("当前控件文字与读取结果不一致，未发送输入")
        return wrapper

    def click(self, snapshot, node):
        wrapper = self._resolve(snapshot, node)
        self._token += 1  # Never replay an input against a stale observation.
        try:
            # Only a missing pattern permits fallback. Unknown invocation
            # errors may already have acted and must never cause a second click.
            try:
                invoke = wrapper.iface_invoke
            except AttributeError:
                invoke = None
                if hasattr(wrapper, "invoke") and not hasattr(wrapper, "element_info"):
                    wrapper.invoke()
                    self._after_input()
                    return
            except Exception as exc:
                from pywinauto.uia_defines import NoPatternInterfaceError
                if not isinstance(exc, NoPatternInterfaceError):
                    raise
                invoke = None
            if invoke is not None:
                invoke.Invoke()
            elif not hasattr(wrapper, "click_input"):
                wrapper.invoke()  # Fake providers and unit tests.
            elif self.background:
                raise PageError("该按钮不支持后台调用，请取消「后台运行」后重试")
            else:
                self._physical_click(wrapper)
            self._after_input()
        except PageError:
            raise
        except Exception as exc:
            # A failed invocation may already have acted. Do not click a second time.
            raise PageError("控件调用结果不确定，请查看小程序后继续；未重复点击") from exc

    def _physical_click(self, wrapper):
        rect, bounds = wrapper.rectangle(), self.window.rectangle()
        x, y = (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2
        if not (bounds.left <= x < bounds.right and bounds.top <= y < bounds.bottom) or not self._is_foreground():
            raise PageError("目标控件不在当前可点击窗口内，未发送鼠标输入")
        wrapper.click_input()

    def _in_view(self, wrapper):
        try:
            rect, bounds = wrapper.rectangle(), self.window.rectangle()
        except AttributeError:
            return True  # Non-native fake providers used by offline tests.
        x, y = (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2
        return (rect.right > rect.left and rect.bottom > rect.top
                and bounds.left <= x < bounds.right and bounds.top <= y < bounds.bottom)

    def scroll_region(self, snapshot, direction="down"):
        self.before_input()
        if snapshot.token != self._token or direction not in {"up", "down"} or self._root is None:
            raise PageError("区县页面已刷新，请重新读取后滚动")
        self._prepare_window(activate=not self.background)
        self._token += 1
        for wrapper in [self._root] + self._root.descendants():
            try:
                pattern = wrapper.iface_scroll
            except Exception:
                continue
            if pattern.CurrentVerticallyScrollable:
                old = pattern.CurrentVerticalScrollPercent
                pattern.Scroll(2, 3 if direction == "down" else 1)
                self._after_input()
                return pattern.CurrentVerticalScrollPercent != old
        if self.background:
            raise PageError("区县列表不支持后台滚动，请取消「后台运行」后重试")
        rect = self.window.rectangle()
        self.window.wheel_mouse_input(wheel_dist=-4 if direction == "down" else 4, coords=(int((rect.right-rect.left)*.6), int((rect.bottom-rect.top)*.7)))
        self._after_input()
        return True

    def edit(self, snapshot, node, value):
        wrapper = self._resolve(snapshot, node)
        self._token += 1
        try:
            # SetValue replaces the complete field without keyboard focus or
            # pywinauto's text-splicing/action-log path for sensitive identity data.
            wrapper.iface_value.SetValue(value)
            self._after_input()
        except Exception as exc:
            raise PageError("输入框不支持安全设置文本，请人工填写或更新页面适配") from exc

    def select(self, snapshot, node, selected):
        wrapper = self._resolve(snapshot, node)
        if node.selected is None:
            raise PageError("无法读取选项选中状态，已停止输入")
        if node.selected == selected:
            return
        self._token += 1
        try:
            if node.kind == "RadioButton" and selected:
                wrapper.select()
            elif node.kind == "CheckBox":
                wrapper.toggle()
            else:
                raise PageError("该选项未提供可验证的选择接口")
            self._after_input()
        except PageError:
            raise
        except Exception as exc:
            raise PageError("选项操作结果不确定，请核对后继续") from exc

    def _forget_window(self):
        self.window = None
        self._connected_window = None
        self._connected_identity = None
        self._wrappers.clear()
        self._labels.clear()
        self._root = None
        self._token += 1

    def close_mini_program(self, timeout=5):
        """Close only the connected, verified mini-program window, then confirm it is gone.

        This is a batch action, separate from releasing COM/the control lock.
        It never searches for another window, reopens an app, or ends a process.
        """
        if self.window is None or self.window is not self._connected_window or self._connected_identity is None:
            raise PageError("没有已连接的小程序窗口，未尝试关闭")
        self.before_input()
        self.profile.validate()
        window = self.window
        handle, pid = self._connected_identity
        if not handle or getattr(window, "handle", None) != handle:
            raise PageError("小程序窗口标识已变化，未尝试关闭")
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.IsWindow.argtypes = [wintypes.HWND]
        user32.IsWindow.restype = wintypes.BOOL
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD

        def still_same_window():
            if not user32.IsWindow(handle):
                return False
            actual_pid = wintypes.DWORD()
            if not user32.GetWindowThreadProcessId(handle, ctypes.byref(actual_pid)):
                raise PageError("无法确认小程序窗口所属进程，未继续关闭")
            return actual_pid.value == pid

        if not still_same_window():
            self._forget_window()
            return
        if window.process_id() != pid or self._process_name(pid).casefold() != self.profile.process_name.casefold():
            raise PageError("小程序窗口所属进程已变化，未尝试关闭")
        # Re-read the connected document's AppID immediately before acting.
        # A matching title or remembered HWND alone is not an application check.
        self.snapshot()
        self.before_input()
        if not still_same_window():
            self._forget_window()
            return
        try:
            pattern = window.iface_window
        except AttributeError:
            pattern = None
        except Exception as exc:
            from pywinauto.uia_defines import NoPatternInterfaceError
            if not isinstance(exc, NoPatternInterfaceError):
                raise PageError("无法读取小程序窗口关闭接口，未发送关闭请求") from exc
            pattern = None
        self._token += 1
        action_error = None
        try:
            if pattern is not None:
                pattern.Close()
            else:
                # WM_CLOSE targets this native window, not WeChat's main window
                # or the shared WeChatAppEx process hosting other mini-programs.
                user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
                user32.PostMessageW.restype = wintypes.BOOL
                if not user32.PostMessageW(handle, 0x0010, 0, 0):
                    raise PageError("未能向小程序窗口发送关闭请求")
        except Exception as exc:
            # Close may have already acted. Check the result without sending a
            # second request through a different API.
            action_error = exc
        deadline = time.monotonic() + max(0, timeout)
        while True:
            if not still_same_window():
                self._forget_window()
                return
            self.before_input()
            if time.monotonic() >= deadline:
                break
            time.sleep(.1)
        if action_error is not None:
            raise PageError("小程序窗口关闭结果无法确认；窗口仍在，请查看微信提示") from action_error
        raise PageError("小程序窗口关闭超时；窗口仍在，请查看微信提示")

    def close(self):
        if self._mutex:
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.ReleaseMutex(self._mutex)
            kernel.CloseHandle(self._mutex)
            self._mutex = None
        if self._com:
            self._com.CoUninitialize()
            self._com = None
        self._forget_window()
