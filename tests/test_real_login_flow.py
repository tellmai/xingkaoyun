"""Provider-shaped login regressions; all identities and windows are synthetic."""
from dataclasses import replace

import pytest

from yunkaoxing.engine import Control, LoginError, MiniProgramSession
from yunkaoxing.models import RunOptions
from yunkaoxing.page import Node, PageError, Snapshot
from yunkaoxing.profile import SiteProfile


class Clock:
    value = 0
    def __call__(self):
        return self.value


class FastControl(Control):
    def __init__(self, clock):
        super().__init__()
        self.clock = clock
    def sleep(self, seconds):
        self.check()
        self.clock.value += seconds


class LoginPort:
    """Expose a clipped list, delayed UIA values, and asynchronous page changes."""
    def __init__(self, candidate, *, pages=None, search_works=True, allow_edit=True):
        self.candidate = candidate
        self.pages = pages or [[candidate.district]]
        self.search_works, self.allow_edit = search_works, allow_edit
        self.stage, self.viewport, self.token = "region", 0, 0
        self.selected_district, self.query = "", ""
        self.fields = {"name": "", "id": ""}
        self.pending_fields, self.pending_page = {}, None
        self.agreed, self.personal = False, False
        self.pending_agreement = 0
        self.searches, self.scrolls, self.clicks, self.submissions = [], [], [], []
        self.transient_reads = 0

    def snapshot(self):
        if self.transient_reads:
            self.transient_reads -= 1
            raise PageError("小程序文档正在替换")
        if self.pending_page:
            stage, remaining = self.pending_page
            if remaining <= 1:
                self.stage, self.pending_page = stage, None
            else:
                self.pending_page = stage, remaining - 1
        for key, (value, remaining) in list(self.pending_fields.items()):
            if remaining <= 1:
                if key == "search":
                    self.query = value
                else:
                    self.fields[key] = value
                del self.pending_fields[key]
            else:
                self.pending_fields[key] = value, remaining - 1
        if self.pending_agreement:
            self.pending_agreement -= 1
            if not self.pending_agreement:
                self.agreed = True
        self.token += 1
        if self.stage == "region":
            names = [name for page in self.pages for name in page] if self.query and self.search_works else self.pages[self.viewport]
            if self.query and self.search_works:
                names = [name for name in names if self.query in name]
            nodes = [Node("title", "Text", "选择区县"),
                     Node("search", "Edit", self.query or "搜索区县名称或拼音", self.query)]
            nodes += [Node("district:" + name, "Button", "选择" + name) for name in names]
        elif self.stage == "login":
            nodes = [Node("title", "Text", "考生登录"),
                     Node("district", "Text", "当前考区：" + self.selected_district),
                     Node("reselect", "Button", "重新选择 ›"),
                     Node("name_label", "Text", "姓名："),
                     Node("name", "Edit", self.fields["name"] or "请输入真实姓名", self.fields["name"]),
                     Node("id_label", "Text", "身份证号"),
                     Node("id", "Edit", self.fields["id"] or "请输入15位或18位身份证号", self.fields["id"]),
                     Node("agree", "CheckBox", "同意用户服务协议和隐私政策", selected=self.agreed),
                     Node("login", "Button", "安全登录")]
        else:
            c = self.candidate
            nodes = [Node("title", "Text", "考试服务"), Node("name", "Text", "姓名：" + c.name),
                     Node("unit", "Text", self.selected_district + "/模拟单位/模拟部门"),
                     Node("none", "Text", "当前暂无考试与学习安排"),
                     Node("personal", "Button", "人 个人资料 ›"),
                     Node("logout", "Button", "退出")]
            if self.personal:
                masked = c.id_number[:6] + "********" + c.id_number[-4:]
                nodes += [Node("id", "Text", "身份证号：" + masked),
                          Node("district", "Text", "考区：" + self.selected_district),
                          Node("close", "Button", "关闭个人资料")]
        return Snapshot(tuple(nodes), self.token)

    def current(self, snapshot):
        assert snapshot.token == self.token, "an input used an old observation"
        self.token += 1

    def edit(self, snapshot, node, value):
        self.current(snapshot)
        if not self.allow_edit and node.key != "search":
            return
        self.pending_fields[node.key] = value, 2
        if node.key == "search":
            self.searches.append(value)

    def select(self, snapshot, node, selected):
        self.current(snapshot)
        assert node.key == "agree" and selected
        self.pending_agreement = 2

    def click(self, snapshot, node):
        self.current(snapshot)
        self.clicks.append(node.key)
        if node.key.startswith("district:"):
            self.selected_district = node.key.split(":", 1)[1]
            self.pending_page = "login", 2
        elif node.key == "reselect":
            self.fields, self.agreed = {"name": "", "id": ""}, False
            self.query = ""
            self.pending_page = "region", 3
        elif node.key == "login":
            assert self.agreed
            self.submissions.append(dict(self.fields))
            self.candidate = replace(self.candidate, name=self.fields["name"], id_number=self.fields["id"], district=self.selected_district)
            self.pending_page = "home", 2
        elif node.key == "logout":
            self.pending_page = "login", 3
        elif node.key == "personal":
            self.personal = True
        elif node.key == "close":
            self.personal = False

    def scroll_region(self, snapshot, direction):
        self.current(snapshot)
        assert self.stage == "region"
        self.scrolls.append(direction)
        old = self.viewport
        self.viewport = max(0, self.viewport - 1) if direction == "up" else min(len(self.pages) - 1, self.viewport + 1)
        return self.viewport != old


def session(port, mode="login"):
    clock = Clock()
    return MiniProgramSession(port, SiteProfile(), RunOptions(mode=mode, timeout_seconds=5), FastControl(clock), clock=clock)


class SheetLoginPort(LoginPort):
    """The district picker overlays a still-readable, still-enabled login form."""
    def __init__(self, candidate, *, picker=False, **kwargs):
        super().__init__(candidate, **kwargs)
        self.stage, self.selected_district = "login", candidate.district
        self.picker, self.pending_picker = picker, None
        self.fields = {"name": "合成上次姓名", "id": "110101199001010029"}
        self.login_edits, self.closing_reads = [], 0

    def snapshot(self):
        if self.pending_picker:
            opened, remaining = self.pending_picker
            if not opened:
                self.closing_reads += 1
            if remaining <= 1:
                self.picker, self.pending_picker = opened, None
            else:
                self.pending_picker = opened, remaining - 1
        s = super().snapshot()
        if self.picker:
            names = [name for page in self.pages for name in page] if self.query and self.search_works else self.pages[self.viewport]
            if self.query and self.search_works:
                names = [name for name in names if self.query in name]
            overlay = [Node("picker-title", "Text", "选择考区"),
                       Node("picker-help", "Text", "选择后将保留已填写的登录信息"),
                       Node("picker-close", "Button", "关闭"),
                       Node("search", "Edit", self.query or "搜索区县名称或拼音", self.query)]
            overlay += [Node("district:" + name, "Button", "选择" + name) for name in names]
            return replace(s, nodes=s.nodes + tuple(overlay))
        return s

    def edit(self, snapshot, node, value):
        if node.key == "search":
            assert self.picker
        else:
            assert not self.picker, "attempted to fill the login backdrop before the picker closed"
            self.login_edits.append(node.key)
        super().edit(snapshot, node, value)

    def click(self, snapshot, node):
        if node.key == "reselect" or node.key.startswith("district:"):
            self.current(snapshot)
            self.clicks.append(node.key)
            if node.key == "reselect":
                self.query = ""
                self.pending_picker = True, 3
            else:
                assert self.picker
                self.selected_district = node.key.split(":", 1)[1]
                self.pending_picker = False, 4
            return
        super().click(snapshot, node)

    def scroll_region(self, snapshot, direction):
        self.current(snapshot)
        assert self.picker
        self.scrolls.append(direction)
        old = self.viewport
        self.viewport = max(0, old - 1) if direction == "up" else min(len(self.pages) - 1, old + 1)
        return old != self.viewport


def test_district_sheet_excludes_the_readable_login_backdrop(candidate):
    port = SheetLoginPort(candidate, picker=True)
    engine = session(port)
    s = port.snapshot()
    assert len([n for n in s.nodes if n.kind == "Edit"]) == 3
    assert engine._on_region(s) and not engine._on_login(s)
    engine.login(candidate)
    assert port.closing_reads >= 4 and port.login_edits == ["name", "id"]
    assert port.submissions == [{"name": candidate.name, "id": candidate.id_number}]


def test_district_sheet_search_uses_its_own_edit_with_three_inputs_present(candidate):
    person = replace(candidate, district="沙坪坝区")
    port = SheetLoginPort(person, picker=True, pages=[["黔江区"], ["沙坪坝区"]])
    session(port).login(person)
    assert port.searches == ["沙坪坝"] and not port.scrolls
    assert port.login_edits == ["name", "id"] and port.stage == "home"


def test_district_sheet_can_clear_a_named_query_before_scrolling(candidate):
    person = replace(candidate, district="沙坪坝区")
    port = SheetLoginPort(person, picker=True, pages=[["黔江区"], ["沙坪坝区"]], search_works=False)
    session(port).login(person)
    assert port.searches == ["沙坪坝", ""] and "down" in port.scrolls
    assert port.submissions and port.login_edits == ["name", "id"]


def test_same_person_can_finish_two_fresh_logins_with_a_district_bottom_sheet(candidate):
    port = SheetLoginPort(candidate)
    engine = session(port)
    for _ in range(2):
        engine.login(candidate)
        engine.logout()
        s = port.snapshot()
        assert port.picker and engine._on_region(s) and not engine._on_login(s)
    flow = [key for key in port.clicks if key in {"logout", "reselect", "login"} or key.startswith("district:")]
    assert flow == ["reselect", "district:" + candidate.district, "login", "logout", "reselect",
                    "district:" + candidate.district, "login", "logout", "reselect"]
    assert len(port.submissions) == 2 and port.login_edits == ["name", "id", "name", "id"]


def test_offscreen_district_is_searched_then_values_and_agreement_are_read_back(candidate):
    person = replace(candidate, district="沙坪坝区")
    port = LoginPort(person, pages=[["两江新区", "璧山区"], ["黔江区"], ["沙坪坝区"]])
    engine = session(port)
    engine.login(person)
    assert port.searches == ["沙坪坝"] and not port.scrolls
    assert port.submissions == [{"name": person.name, "id": person.id_number}]
    assert port.stage == "home" and not port.personal
    assert "none" in {node.key for node in port.snapshot().nodes}


def test_unavailable_search_falls_back_to_bounded_scrolling_with_same_page_title(candidate):
    person = replace(candidate, district="沙坪坝区")
    port = LoginPort(person, pages=[["两江新区"], ["黔江区"], ["沙坪坝区"]], search_works=False)
    session(port).login(person)
    assert "down" in port.scrolls and len(port.scrolls) <= 12
    assert port.submissions and port.selected_district == person.district


def test_missing_district_reports_the_requested_name_without_submitting(candidate):
    person = replace(candidate, district="不存在的考区")
    port = LoginPort(person, pages=[["黔江区"], ["沙坪坝区"]])
    with pytest.raises(LoginError, match="不存在的考区"):
        session(port).login(person)
    assert not port.submissions and len(port.scrolls) <= 12


def test_login_verification_reselects_even_the_current_district(candidate):
    port = LoginPort(candidate)
    port.stage, port.selected_district = "login", candidate.district
    port.fields = {"name": "旧名字", "id": "110101199001010029"}
    session(port).login(candidate)
    assert port.clicks[:2] == ["reselect", "district:" + candidate.district]
    assert port.submissions == [{"name": candidate.name, "id": candidate.id_number}]


def test_exam_mode_preserves_an_already_verified_home(candidate):
    port = LoginPort(candidate)
    port.stage, port.selected_district = "home", candidate.district
    session(port, mode="exam").login(candidate)
    assert not port.submissions and "logout" not in port.clicks
    assert port.stage == "home" and not port.personal


def test_login_verification_logs_out_and_reenters_even_the_same_person(candidate):
    port = LoginPort(candidate)
    port.stage, port.selected_district = "home", candidate.district
    session(port).login(candidate)
    flow = [key for key in port.clicks if key in {"logout", "reselect", "login"} or key.startswith("district:")]
    assert flow == ["logout", "reselect", "district:" + candidate.district, "login"]
    assert port.submissions == [{"name": candidate.name, "id": candidate.id_number}]


@pytest.mark.parametrize("next_district", ["黔江区", "沙坪坝区"])
def test_each_identity_exits_before_the_next_district_and_login(candidate, next_district):
    other = replace(candidate, row=3, district=next_district, name="合成下一人", id_number="110101199001010029")
    port = LoginPort(candidate, pages=[["黔江区", "沙坪坝区"]])
    engine = session(port)
    for person in (candidate, other):
        engine.login(person)
        engine.logout()
        assert port.stage == "region" and not port.personal
    flow = [key for key in port.clicks if key in {"logout", "reselect", "login"} or key.startswith("district:")]
    assert flow == ["district:" + candidate.district, "login", "logout", "reselect",
                    "district:" + next_district, "login", "logout", "reselect"]
    assert port.submissions == [{"name": person.name, "id": person.id_number} for person in (candidate, other)]


def test_logout_does_not_report_success_while_home_is_still_visible(candidate):
    port = LoginPort(candidate)
    port.stage, port.selected_district = "home", candidate.district
    original_click = port.click
    def click(snapshot, node):
        original_click(snapshot, node)
        if node.key == "logout":
            port.pending_page = None
    port.click = click
    engine = session(port)
    reports = []
    engine.report = reports.append
    with pytest.raises(PageError, match="退出当前考生超时"):
        engine.logout()
    assert "已退出当前考生" not in reports


class LogoutOverlayPort(LoginPort):
    """AppIndex-level native dialog remains alongside the current page nodes."""
    def __init__(self, candidate, *, kind="Text", text="确定", prompt=True, existing=False):
        super().__init__(candidate)
        self.stage, self.selected_district = "home", candidate.district
        self.dialog, self.confirm_kind, self.confirm_text = existing, kind, text
        self.show_prompt, self.confirm_reads = prompt, 0

    def snapshot(self):
        s = super().snapshot()
        if self.dialog:
            self.confirm_reads += 1
            overlay = [Node("overlay-title", "Text", "退出登录"), Node("cancel", "Text", "取消")]
            if self.show_prompt:
                overlay.append(Node("logout-prompt", "Text", "确定退出当前考生账号吗？"))
            if self.confirm_reads >= 3:
                overlay.append(Node("confirm-logout", self.confirm_kind, self.confirm_text))
            return replace(s, nodes=s.nodes + tuple(overlay))
        return s

    def click(self, snapshot, node):
        if node.key in {"logout", "confirm-logout"}:
            self.current(snapshot)
            self.clicks.append(node.key)
            if node.key == "logout":
                self.dialog = True
            else:
                self.dialog = False
                self.pending_page = "login", 3
            return
        super().click(snapshot, node)


@pytest.mark.parametrize("kind,text,prompt", [("Text", "确定", True), ("Button", "确定", True),
                                               ("Button", "确定退出", False), ("Button", "确认退出", False)])
def test_logout_confirms_observed_native_or_legacy_dialog(candidate, kind, text, prompt):
    port = LogoutOverlayPort(candidate, kind=kind, text=text, prompt=prompt)
    session(port).logout()
    assert port.clicks == ["logout", "confirm-logout", "reselect"]
    assert port.confirm_reads >= 3 and not port.dialog and port.stage == "region"


@pytest.mark.parametrize("kind,prompt", [("Text", False), ("Button", False)])
def test_logout_never_clicks_an_unrelated_generic_confirmation(candidate, kind, prompt):
    port = LogoutOverlayPort(candidate, kind=kind, prompt=prompt)
    with pytest.raises(PageError, match="退出当前考生超时"):
        session(port).logout()
    assert port.clicks == ["logout"] and port.dialog and port.stage == "home"


def test_logout_handles_an_already_open_native_confirmation_before_clicking_home(candidate):
    port = LogoutOverlayPort(candidate, existing=True)
    session(port).logout()
    assert port.clicks == ["confirm-logout", "reselect"]
    assert port.stage == "region" and not port.dialog


def test_logout_requires_the_exact_confirmation_text_with_a_native_prompt(candidate):
    port = LogoutOverlayPort(candidate, text="确定清除")
    with pytest.raises(PageError, match="退出当前考生超时"):
        session(port).logout()
    assert port.clicks == ["logout"]


def test_wrong_district_waits_for_reselect_before_entering_the_requested_one(candidate):
    port = LoginPort(candidate, pages=[["黔江区", "沙坪坝区"]])
    port.stage, port.selected_district = "login", "沙坪坝区"
    session(port).login(candidate)
    assert port.clicks[:2] == ["reselect", "district:黔江区"]
    assert port.submissions


def test_readback_mismatch_never_submits_candidate_details(candidate):
    port = LoginPort(candidate, allow_edit=False)
    with pytest.raises(PageError, match="姓名填写回读"):
        session(port).login(candidate)
    assert not port.submissions


def test_only_an_unambiguous_observed_abbreviation_can_select_a_district(candidate):
    person = replace(candidate, district="黔江")
    port = LoginPort(person, pages=[["黔江区"]])
    session(port).login(person)
    assert port.selected_district == "黔江区" and port.submissions
    ambiguous = replace(candidate, district="同名")
    other = LoginPort(ambiguous, pages=[["同名区", "同名县"]])
    with pytest.raises(LoginError, match="多个选项"):
        session(other).login(ambiguous)
    assert not other.submissions


def test_wait_retries_a_transient_document_read_but_not_a_rejection(candidate):
    port = LoginPort(candidate)
    port.transient_reads = 2
    engine = session(port)
    observed = engine.wait(engine._on_region, "等待区县")
    assert engine._on_region(observed)


def test_home_and_personal_information_wait_for_delayed_accessibility_values(candidate):
    port = LoginPort(candidate)
    original = port.snapshot
    reads = {"home": 0, "personal": 0}
    def snapshot():
        s = original()
        if port.stage == "home":
            phase = "personal" if port.personal else "home"
            reads[phase] += 1
            if reads[phase] < 3:
                hidden = "id" if port.personal else "name"
                s = replace(s, nodes=tuple(node for node in s.nodes if node.key != hidden))
        return s
    port.snapshot = snapshot
    session(port).login(candidate)
    assert reads["home"] >= 3 and reads["personal"] >= 3
    assert port.submissions and not port.personal


class LoginErrorPort(LoginPort):
    """A native error is appended after the readable login-page backdrop."""
    def __init__(self, candidate, *, kind="Text", reject_once=False, existing=False, stuck=False, title="提示"):
        super().__init__(candidate)
        self.close_kind, self.reject_once, self.stuck = kind, reject_once, stuck
        self.error_modal, self.error_title = existing, title
        self.error_body = "登录失败：当前考区未报名" if existing else ""
        self.inline_error, self.pending_close = "", 0
        self.show_close = True
        if existing:
            self.stage, self.selected_district = "login", candidate.district

    def snapshot(self):
        if self.pending_close and not self.stuck:
            self.pending_close -= 1
            if not self.pending_close:
                self.error_modal, self.error_body = False, ""
        s = super().snapshot()
        extra = [Node("inline-error", "Text", self.inline_error)] if self.inline_error else []
        if self.error_modal:
            extra += [Node("error-title", "Text", self.error_title), Node("error-body", "Text", self.error_body)]
            if self.show_close:
                extra.append(Node("error-close", self.close_kind, "确定"))
        return replace(s, nodes=s.nodes + tuple(extra))

    def edit(self, snapshot, node, value):
        assert not self.error_modal, "attempted input behind a native error dialog"
        super().edit(snapshot, node, value)

    def click(self, snapshot, node):
        if node.key == "error-close":
            self.current(snapshot)
            self.clicks.append(node.key)
            self.pending_close = 3
            return
        assert not self.error_modal, "attempted navigation behind a native error dialog"
        if node.key == "login" and self.reject_once:
            self.current(snapshot)
            self.clicks.append(node.key)
            self.submissions.append(dict(self.fields))
            self.reject_once, self.error_modal = False, True
            self.error_body = "登录失败：当前考区未报名"
            return
        if node.key == "reselect":
            self.inline_error = ""
        super().click(snapshot, node)


@pytest.mark.parametrize("kind,title", [("Text", "提示"), ("Button", "登录失败"), ("Text", "温馨提示")])
def test_native_login_error_is_closed_before_the_next_identity(candidate, kind, title):
    other = replace(candidate, row=3, name="合成下一人", id_number="110101199001010029")
    port = LoginErrorPort(candidate, kind=kind, title=title, reject_once=True)
    engine = session(port)
    reports = []
    engine.report = reports.append
    with pytest.raises(LoginError, match="当前考区未报名") as failure:
        engine.login(candidate)
    original_reason = str(failure.value)
    engine.recover_login()
    engine.login(other)
    assert str(failure.value) == original_reason and "当前考区未报名" in original_reason
    assert any("当前考区未报名" in report for report in reports)
    assert port.clicks.index("error-close") < port.clicks.index("reselect")
    assert port.submissions == [{"name": person.name, "id": person.id_number} for person in (candidate, other)]
    assert port.stage == "home" and not port.error_modal


@pytest.mark.parametrize("message", ["登录失败：当前考区未报名", "身份证号码格式不正确，请核对后重试"])
def test_new_session_clears_a_preexisting_native_error_before_filling(candidate, message):
    port = LoginErrorPort(candidate, existing=True)
    port.error_body = message
    engine = session(port)
    assert engine._login_error_text(port.snapshot()) == message
    engine.login(candidate)
    assert port.clicks[:3] == ["error-close", "reselect", "district:" + candidate.district]
    assert port.submissions == [{"name": candidate.name, "id": candidate.id_number}]


def test_inline_login_error_is_cleared_by_reselect_without_a_generic_close(candidate):
    port = LoginErrorPort(candidate)
    port.stage, port.selected_district = "login", candidate.district
    port.inline_error = "登录失败：当前考区未报名"
    engine = session(port)
    engine.recover_login()
    assert port.clicks == ["reselect"] and port.stage == "region" and not port.inline_error
    engine.login(candidate)
    assert port.stage == "home" and "error-close" not in port.clicks


def test_unrelated_native_notice_is_not_closed_without_a_known_login_error(candidate):
    port = LoginErrorPort(candidate, existing=True)
    port.error_body = "请阅读最新通知"
    session(port).recover_login()
    assert port.error_modal and not port.clicks


def test_inline_error_before_an_unrelated_notice_does_not_authorize_its_confirmation(candidate):
    port = LoginErrorPort(candidate, existing=True)
    port.inline_error, port.error_body = "登录失败：上一位考生未报名", "请阅读最新通知"
    with pytest.raises(LoginError, match="另有无法确认的提示"):
        session(port).recover_login()
    assert port.error_modal and not port.clicks


def test_picker_close_is_not_used_to_dismiss_an_inline_login_error(candidate):
    port = SheetLoginPort(candidate, picker=True)
    original = port.snapshot
    def snapshot():
        s = original()
        return replace(s, nodes=s.nodes + (Node("inline-error", "Text", "登录失败：上一位考生未报名"),))
    port.snapshot = snapshot
    session(port).recover_login()
    assert port.picker and not port.clicks


@pytest.mark.parametrize("entry", ["recover_login", "login"])
def test_native_error_close_timeout_never_falls_through_to_identity_input(candidate, entry):
    port = LoginErrorPort(candidate, existing=True, stuck=True)
    engine = session(port)
    with pytest.raises(PageError, match="关闭登录提示超时"):
        getattr(engine, entry)(candidate) if entry == "login" else engine.recover_login()
    assert port.clicks == ["error-close"] and not port.submissions and port.error_modal


def test_recognized_native_error_waits_for_a_safe_close_control_and_reports_timeout(candidate):
    port = LoginErrorPort(candidate, existing=True)
    port.show_close = False
    with pytest.raises(PageError, match="等待登录提示关闭入口超时"):
        session(port).recover_login()
    assert port.error_modal and not port.clicks
