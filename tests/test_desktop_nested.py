"""Regression for nested WeChat accessibility nodes, without using live Windows."""
import itertools
import sys
from types import SimpleNamespace

import pytest

from yunkaoxing.desktop import DesktopUIA
from yunkaoxing.engine import Control as RunControl, MiniProgramSession
from yunkaoxing.models import RunOptions
from yunkaoxing.page import PageError
from yunkaoxing.profile import SiteProfile


def rect(left=0, top=0, right=420, bottom=800):
    return SimpleNamespace(left=left, top=top, right=right, bottom=bottom)


class Value:
    def __init__(self, value):
        self.CurrentValue, self.writes = value, []

    def SetValue(self, value):
        self.writes.append(value)
        self.CurrentValue = value


class Control:
    identities = itertools.count(1)

    def __init__(self, kind, text="", value="", *, children=(), selected=None, bounds=None):
        self.element_info = SimpleNamespace(control_type=kind, runtime_id=(next(self.identities),), rich_text="")
        self.text, self.iface_value = text, Value(value)
        self.items, self.selected, self.toggles = list(children), selected, 0
        self.container, self.bounds = None, bounds or rect()
        for child in self.items:
            child.container = self

    def window_text(self):
        return self.text

    def get_value(self):
        return self.iface_value.CurrentValue

    def is_visible(self):
        return True

    def is_enabled(self):
        return True

    def parent(self):
        return self.container or SimpleNamespace(element_info=SimpleNamespace(control_type="Group"))

    def children(self):
        return self.items

    def descendants(self):
        result = []
        for child in self.items:
            result.append(child)
            result.extend(child.descendants())
        return result

    def rectangle(self):
        return self.bounds

    def get_toggle_state(self):
        if self.selected is None:
            raise AttributeError("outer semantic wrapper has no toggle state")
        return int(self.selected)

    def toggle(self):
        self.selected = not self.selected
        self.toggles += 1


class Document(Control):
    def __init__(self, *controls, bounds=None):
        super().__init__("Document", "Page-Frame", children=controls, bounds=bounds)
        self.iface_value = Value(f"https://servicewechat.com/{SiteProfile().app_id}/5/page-frame.html")


class Window:
    def __init__(self, docs, *, active=False):
        self.docs, self.active, self.focuses = list(docs), active, 0

    def descendants(self, **kwargs):
        assert kwargs == {"control_type": "Document"}
        return self.docs

    def is_visible(self):
        return True

    def is_minimized(self):
        return False

    def is_active(self):
        return self.active

    def rectangle(self):
        return rect()

    def set_focus(self):
        self.focuses += 1


def make_port(*documents, background=True, active=False):
    port = DesktopUIA(SiteProfile(), background=background)
    port.window = Window(documents, active=active)
    return port


def test_nested_edits_keep_outer_placeholder_but_read_and_write_inner_value():
    name = Control("Edit", value="旧合成姓名")
    identity = Control("Edit", value="110101199001010010")
    outer_name = Control("Edit", value="请输入真实姓名", children=[name])
    outer_identity = Control("Edit", value="请输入15位或18位身份证号", children=[identity])
    port = make_port(Document(outer_name, outer_identity))
    before = port.snapshot()
    assert len(before.nodes) == 2
    node = before.find(["请输入真实姓名"], ("Edit",))
    assert node.value == "旧合成姓名"
    assert before.find(["请输入15位或18位身份证号"], ("Edit",)).value == "110101199001010010"
    port.edit(before, node, "新合成姓名")
    assert name.iface_value.writes == ["新合成姓名"]
    assert outer_name.iface_value.writes == []
    after = port.snapshot()
    assert after.find(["请输入真实姓名"], ("Edit",)).value == "新合成姓名"
    assert identity.iface_value.writes == []


def test_nested_checkbox_uses_inner_state_and_retains_outer_semantic_label():
    inner = Control("CheckBox", "✓", selected=True)
    outer = Control("CheckBox", "同意用户服务协议和隐私政策", children=[inner])
    port = make_port(Document(outer))
    before = port.snapshot()
    assert len(before.nodes) == 1
    node = before.find([outer.text], ("CheckBox",))
    assert node.selected is True
    port.select(before, node, False)
    assert inner.selected is False and inner.toggles == 1
    assert outer.selected is None and outer.toggles == 0


def test_nested_selection_validates_outer_label_even_when_inner_glyph_differs():
    inner = Control("CheckBox", "", selected=False)
    outer = Control("CheckBox", "同意用户服务协议和隐私政策", children=[inner])
    port = make_port(Document(outer))
    observed = port.snapshot()
    node = observed.find([outer.text], ("CheckBox",))
    port.select(observed, node, True)
    assert inner.selected and inner.toggles == 1
    refreshed = port.snapshot()
    old_node = refreshed.find([outer.text], ("CheckBox",))
    outer.text = "另一份协议"
    with pytest.raises(PageError, match="控件文字"):
        port.select(refreshed, old_node, False)
    assert inner.selected and inner.toggles == 1


@pytest.mark.parametrize("reverse", [False, True])
def test_current_document_uses_viewport_overlap_instead_of_cache_order(reverse):
    active = Document(Control("Text", "选择区县"), bounds=rect())
    cached = Document(Control("Text", "考生登录"), bounds=rect(-420, 0, 0, 800))
    roots = [active, cached]
    if reverse:
        roots.reverse()
    port = make_port(*roots)
    snapshot = port.snapshot()
    assert snapshot.text == "选择区县"
    assert port._root is active


@pytest.mark.parametrize("reverse", [False, True])
def test_largest_visible_document_wins_over_a_partially_overlapping_cached_page(reverse):
    active = Document(Control("Text", "考试服务"), bounds=rect())
    partial = Document(Control("Text", "考生登录"), bounds=rect(-200, 0, 200, 800))
    roots = [partial, active]
    if reverse:
        roots.reverse()
    port = make_port(*roots)
    assert port.snapshot().text == "考试服务"


def test_foreground_hit_test_selects_active_document_when_bounds_tie(monkeypatch):
    active = Document(Control("Text", "选择区县"), bounds=rect())
    cached = Document(Control("Text", "考生登录"), bounds=rect())
    port = make_port(active, cached, background=False, active=True)
    hits = []
    def hit(x, y, roots):
        hits.append((x, y))
        assert roots == [active, cached]
        assert 0 < x < 420 and 0 < y < 800
        return active
    monkeypatch.setattr(port, "_document_at_point", hit)
    assert port.snapshot().text == "选择区县"
    assert hits and port._root is active


def test_ambiguous_background_documents_fail_without_reading_a_guessed_cache(monkeypatch):
    first = Document(Control("Text", "选择区县"), bounds=rect())
    last = Document(Control("Text", "考生登录"), bounds=rect())
    port = make_port(first, last, background=True, active=False)
    monkeypatch.setattr(port, "_document_at_point", lambda *args: pytest.fail("background hit test must not inspect an occluding window"))
    with pytest.raises(PageError, match="多个页面"):
        port.snapshot()
    assert port._root is None and not port._wrappers


def test_native_foreground_handle_avoids_false_uia_focus_failure(monkeypatch):
    class Function:
        def __init__(self, function):
            self.function = function
        def __call__(self, *args):
            return self.function(*args)
    user = SimpleNamespace(
        GetForegroundWindow=Function(lambda: 456),
        GetAncestor=Function(lambda handle, flag: 123),
    )
    monkeypatch.setattr("yunkaoxing.desktop.ctypes.WinDLL", lambda *args, **kwargs: user)
    port = make_port(background=False, active=False)
    port.window.handle = 123
    port._prepare_window(activate=True)
    assert port._is_foreground()
    assert port.window.focuses == 0


def test_native_modal_siblings_are_read_without_retained_page_controls():
    page_text = Control("Text", "考试服务")
    active = Document(page_text)
    old_input = Control("Edit", "请输入真实姓名", value="上次合成姓名")
    retained = Document(Control("Text", "考生登录"), old_input,
                        bounds=rect(-420, 0, 0, 800))
    modal_message = Control("Text", "未找到对应的考生信息，请核对后重试")
    modal_confirm = Control("Text", "确定")
    modal_group = Control("Group", children=[modal_confirm])
    owner = Control("Document", "AppIndex", children=[retained, active, modal_message, modal_group])
    port = make_port(retained, active)

    # wx.showModal is outside Page-Frame; enumerating just the active subtree
    # loses the actual login error, while enumerating all AppIndex descendants
    # would also reintroduce stale login fields from the retained page.
    assert active.parent() is owner
    assert port._page_controls(active) == [page_text, modal_message, modal_group, modal_confirm]
    snapshot = port.snapshot()
    assert [node.text for node in snapshot.nodes] == [page_text.text, modal_message.text, "确定"]
    assert not any(node.kind == "Edit" or node.text == "考生登录" for node in snapshot.nodes)
    assert port._root is active


@pytest.mark.parametrize("root_value", ["", "Page-Frame"])
def test_app_identity_uses_owning_appindex_when_page_value_is_unavailable(root_value):
    active = Document(Control("Text", "选择区县"))
    active.iface_value.CurrentValue = root_value
    owner = Control("Document", "AppIndex",
                    value=f"https://servicewechat.com/{SiteProfile().app_id}/5/page-frame.html",
                    children=[active])
    port = make_port(active)
    assert active.parent() is owner
    assert port.snapshot().text == "选择区县"


def test_missing_page_identity_does_not_accept_an_unrelated_appindex():
    active = Document(Control("Text", "选择区县"))
    active.iface_value.CurrentValue = ""
    Control("Document", "AppIndex", value="https://servicewechat.com/wx_unrelated_app/5/page-frame.html",
            children=[active])
    port = make_port(active)
    with pytest.raises(PageError, match="无法核对"):
        port.snapshot()
    assert port._root is None and not port._wrappers


def test_snapshot_excludes_visible_buttons_whose_centers_are_outside_window():
    fully_visible = Control("Button", "完全可见", bounds=rect(10, 100, 100, 150))
    visible_center = Control("Button", "中心在内", bounds=rect(-30, 200, 70, 250))
    clipped_center = Control("Button", "右侧中心在外", bounds=rect(390, 300, 470, 350))
    below_view = Control("Button", "下方列表项", bounds=rect(10, 790, 100, 850))
    empty_bounds = Control("Button", "尚未布局", bounds=rect(10, 400, 10, 450))
    offscreen_text = Control("Text", "列表保留的语义文字", bounds=rect(10, 900, 100, 950))
    port = make_port(Document(fully_visible, visible_center, clipped_center, below_view,
                              empty_bounds, offscreen_text))

    # Chromium's IsOffscreen can remain false below a scrolling list. Those
    # nodes cannot be exposed as clickable targets just because they overlap
    # a window edge; semantic text remains available for page interpretation.
    assert all(c.is_visible() for c in (fully_visible, visible_center, clipped_center, below_view))
    snapshot = port.snapshot()
    assert [node.text for node in snapshot.nodes if node.kind == "Button"] == ["完全可见", "中心在内"]
    assert offscreen_text.text in snapshot.text


def test_offviewport_district_is_searched_before_it_can_be_selected(monkeypatch):
    requested = "黔江区"
    search = Control("Edit", value="搜索区县名称或拼音", bounds=rect(20, 145, 395, 195))
    target = Control("Button", "选择" + requested, bounds=rect(20, 950, 200, 1000))
    page = Document(Control("Text", "选择区县"), search,
                    Control("Text", requested, bounds=rect(20, 950, 200, 1000)), target)
    port = make_port(page)
    first = port.snapshot()
    assert requested in first.text
    assert not any(node.kind == "Button" for node in first.nodes)
    selected = []
    write = search.iface_value.SetValue

    def search_results(value):
        write(value)
        # The actual search result is now rendered inside the viewport.
        target.bounds = rect(20, 350, 200, 400)

    def open_login():
        selected.append(requested)
        port.window.docs = [Document(Control("Text", "考生登录"),
                                     Control("Text", "当前考区 " + requested),
                                     Control("Button", "安全登录"))]

    search.iface_value.SetValue = search_results
    target.iface_invoke = SimpleNamespace(Invoke=open_login)
    monkeypatch.setattr(port, "scroll_region", lambda *args, **kwargs: pytest.fail("search already found the requested district"))
    session = MiniProgramSession(port, SiteProfile(), RunOptions(mode="login", timeout_seconds=5), RunControl())
    session._select_district(requested)
    assert search.iface_value.writes == ["黔江"]
    assert selected == [requested]
    assert session._on_login(port.snapshot())


def masked_modal_port(monkeypatch, *, title="提示", body="身份证号码格式不正确，请核对后重试",
                      close="确定", background=False, app_id=None):
    region = Document(Control("Text", "选择区县"), Control("Button", "选择黔江区"))
    login = Document(Control("Text", "考生登录"), Control("Edit", "请输入真实姓名"),
                     Control("Button", "安全登录"))
    confirm = Control("Text", close, bounds=rect(230, 440, 300, 480))
    owner = Control("Document", "AppIndex",
                    value=f"https://servicewechat.com/{app_id or SiteProfile().app_id}/6/page-frame.html",
                    children=[region, login, Control("Button", "其他原生入口"),
                              Control("Text", title), Control("Text", body),
                              Control("Group", children=[confirm]), Control("Button", "其他后续入口")])
    port = make_port(region, login, background=background, active=not background)
    monkeypatch.setattr(port, "_document_at_point", lambda *args: None)
    return port, owner, region, login, confirm


@pytest.mark.parametrize("previous_root", [False, True])
def test_native_mask_exposes_only_verified_login_modal_without_choosing_cached_page(monkeypatch, previous_root):
    port, _, region, _, _ = masked_modal_port(monkeypatch)
    if previous_root:
        port._root = region
    assert port._root is region if previous_root else port._root is None
    snapshot = port.snapshot()
    assert [node.text for node in snapshot.nodes] == ["提示", "身份证号码格式不正确，请核对后重试", "确定"]
    assert not any(node.kind == "Edit" or node.text in {"考生登录", "选择区县", "安全登录"} for node in snapshot.nodes)
    assert port._root is None
    session = MiniProgramSession(port, SiteProfile(), RunOptions(mode="login"), RunControl())
    assert session._login_error_dismiss(snapshot).text == "确定"
    assert "身份证号码格式不正确" in session._login_error_text(snapshot)


def test_dismissing_native_mask_does_not_make_a_cached_business_page_current(monkeypatch):
    port, owner, region, login, confirm = masked_modal_port(monkeypatch)
    dismissed = []

    def dismiss():
        dismissed.append(True)
        owner.items = [region, login]

    confirm.iface_invoke = SimpleNamespace(Invoke=dismiss)
    observed = port.snapshot()
    port.click(observed, observed.find(["确定"], ("Text",)))
    assert dismissed == [True]
    with pytest.raises(PageError, match="多个页面"):
        port.snapshot()
    monkeypatch.setattr(port, "_document_at_point", lambda *args: login)
    current = port.snapshot()
    assert "考生登录" in current.text and "确定" not in current.text
    assert port._root is login


@pytest.mark.parametrize("title,body,close", [("提示", "请升级后继续", "确定"),
                                             ("更新通知", "身份证号码格式不正确", "确定"),
                                             ("提示", "身份证号码格式不正确", "升级")])
def test_unknown_native_modal_does_not_bypass_ambiguous_document_rejection(monkeypatch, title, body, close):
    port, _, _, _, _ = masked_modal_port(monkeypatch, title=title, body=body, close=close)
    with pytest.raises(PageError, match="多个页面"):
        port.snapshot()
    assert port._root is None and not port._wrappers


def test_known_modal_cannot_resolve_ambiguous_background_documents(monkeypatch):
    port, _, _, _, _ = masked_modal_port(monkeypatch, background=True)
    with pytest.raises(PageError, match="多个页面"):
        port.snapshot()


def test_native_modal_requires_the_unique_appindex_application_identity(monkeypatch):
    port, _, _, _, _ = masked_modal_port(monkeypatch, app_id="unrelated-app")
    with pytest.raises(PageError, match="弹窗的应用身份"):
        port.snapshot()
    assert not port._wrappers


def test_native_modal_with_distinct_owners_does_not_choose_one_cached_application(monkeypatch):
    port, _, region, _, _ = masked_modal_port(monkeypatch)
    Control("Document", "AppIndex", value=f"https://servicewechat.com/{SiteProfile().app_id}/6/page-frame.html",
            children=[region])
    with pytest.raises(PageError, match="多个页面"):
        port.snapshot()


def test_known_native_logout_mask_exposes_only_prompt_and_confirmation(monkeypatch):
    port, _, _, _, _ = masked_modal_port(monkeypatch, title="退出登录", body="确定退出当前考生账号吗？")
    snapshot = port.snapshot()
    assert [node.text for node in snapshot.nodes] == ["确定退出当前考生账号吗？", "确定"]
    session = MiniProgramSession(port, SiteProfile(), RunOptions(mode="login"), RunControl())
    assert session._logout_confirmation(snapshot).text == "确定"
    assert port._root is None


def test_visible_snapshot_reactivates_once_before_resolving_native_mask(monkeypatch):
    port, _, _, _, _ = masked_modal_port(monkeypatch)
    port.window.active = False

    def activate():
        port.window.focuses += 1
        port.window.active = True

    monkeypatch.setattr(port.window, "set_focus", activate)
    first = port.snapshot()
    assert "身份证号码格式不正确" in first.text
    assert port.window.focuses == 1 and port._root is None
    second = port.snapshot()
    assert second.text == first.text and port.window.focuses == 1


@pytest.mark.parametrize("same", [False, True])
def test_missing_runtime_ids_use_native_comparison_for_separate_owner_wrappers(monkeypatch, same):
    left_element, right_element, compared = object(), object(), []
    left = SimpleNamespace(element_info=SimpleNamespace(runtime_id=0, element=left_element))
    right = SimpleNamespace(element_info=SimpleNamespace(runtime_id=0, element=right_element))

    def compare(first, second):
        compared.append((first, second))
        return same

    provider = SimpleNamespace(IUIA=lambda: SimpleNamespace(iuia=SimpleNamespace(CompareElements=compare)))
    monkeypatch.setitem(sys.modules, "pywinauto.uia_defines", provider)
    assert DesktopUIA._same_element(left, right) is same
    assert compared == [(left_element, right_element)]


@pytest.mark.parametrize("runtime_id", [None, 0, (), (0,)])
def test_empty_runtime_ids_and_equal_fake_metadata_are_not_identity_proof(runtime_id):
    left = SimpleNamespace(element_info=SimpleNamespace(runtime_id=runtime_id, control_type="Document"))
    right = SimpleNamespace(element_info=SimpleNamespace(runtime_id=runtime_id, control_type="Document"))
    assert left.element_info == right.element_info
    assert not DesktopUIA._same_element(left, right)
    assert DesktopUIA._same_element(left, left)


def test_native_comparison_failure_preserves_only_object_identity(monkeypatch):
    left = SimpleNamespace(element_info=SimpleNamespace(runtime_id=(), element=object()))
    right = SimpleNamespace(element_info=SimpleNamespace(runtime_id=(), element=object()))

    def unavailable(*args):
        raise RuntimeError("native provider unavailable")

    provider = SimpleNamespace(IUIA=lambda: SimpleNamespace(iuia=SimpleNamespace(CompareElements=unavailable)))
    monkeypatch.setitem(sys.modules, "pywinauto.uia_defines", provider)
    assert not DesktopUIA._same_element(left, right)
    assert DesktopUIA._same_element(left, left)
