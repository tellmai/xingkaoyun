from dataclasses import replace

import pytest

from yunkaoxing.engine import Control, LoginError, MiniProgramSession, Stopped
from yunkaoxing.knowledge import KnowledgeIndex
from yunkaoxing.models import RunOptions
from yunkaoxing.page import Node, PageError, Snapshot, parse_question, parse_score
from yunkaoxing.profile import SiteProfile


def nodes(*rows):
    return tuple(Node(str(i), *row) for i, row in enumerate(rows))


class FakePort:
    def __init__(self, candidate, paper):
        self.candidate, self.paper = candidate, paper
        self.view, self.number, self.token = "login", 1, 0
        self.fields, self.answers, self.actions = {}, {}, []
        self.agreed, self.modal, self.home_id = False, False, None
        self.score = 96

    def snapshot(self):
        self.token += 1
        c = self.candidate
        if self.view == "login":
            items = nodes(("Text", "考生登录"), ("Text", c.district),
                ("Edit", "请输入真实姓名", self.fields.get("name", "")),
                ("Edit", "请输入15位或18位身份证号", self.fields.get("id", "")),
                ("CheckBox", "同意用户服务协议和隐私政策", "", self.agreed),
                ("Button", "安全登录"))
        elif self.view == "home":
            items = list(nodes(("Text", "考试服务"), ("Text", c.name), ("Text", "黔江区/合成单位"),
                ("Button", "人 个人资料 ›"), ("Button", "合成测试考试"), ("Button", "退出")))
            if self.modal:
                items += [Node("modal", "Button", "关闭个人资料"), Node("id", "Text", self.home_id or c.id_number[:6] + "********" + c.id_number[-4:]), Node("district", "Text", c.district)]
        elif self.view == "details":
            items = nodes(("Text", "合成测试考试"), ("Button", "开始考试"))
        elif self.view == "result":
            items = nodes(("Text", "考试完成"), ("Text", f"考试成绩：{self.score} 分"))
        else:
            q = self.paper[self.number - 1]
            items = list(nodes(("Text", "合成测试考试"), ("Text", f"{self.number}/{len(self.paper)}"), ("Text", q.kind + "题"), ("Text", q.stem)))
            for label, text in q.options.items():
                items.append(Node(label, "CheckBox" if q.kind == "多选" else "RadioButton", f"{label}. {text}", selected=label in self.answers.get(self.number, set())))
            items += [Node("next", "Button", "下一题"), Node("prev", "Button", "上一题"), Node("submit", "Button", "交卷")]
        return Snapshot(tuple(items), self.token)

    def assert_current(self, snapshot):
        assert snapshot.token == self.token, "stale input"
        self.token += 1

    def edit(self, s, n, value):
        self.assert_current(s)
        self.fields["name" if n.text == "请输入真实姓名" else "id"] = value
        self.actions.append(("edit", n.text))

    def select(self, s, n, selected):
        self.assert_current(s)
        self.actions.append(("select", n.text))
        if self.view == "login":
            self.agreed = selected
            return
        selections = self.answers.setdefault(self.number, set())
        if n.kind == "RadioButton" and selected:
            selections.clear()
        selections.add(n.key) if selected else selections.discard(n.key)

    def click(self, s, n):
        self.assert_current(s)
        self.actions.append(("click", n.text))
        if n.text == "安全登录":
            assert self.agreed
            self.view = "home"
        elif n.text == "人 个人资料 ›":
            self.modal = True
        elif n.text == "关闭个人资料":
            self.modal = False
        elif n.text == "合成测试考试":
            self.view = "details"
        elif n.text == "开始考试":
            self.view = "question"
        elif n.text == "下一题":
            self.number += 1
        elif n.text == "上一题":
            self.number -= 1
        elif n.text == "交卷":
            self.view = "result"


def session(port, **kwargs):
    return MiniProgramSession(port, SiteProfile(), RunOptions(mode="exam", exam_title="合成测试考试", minimum_seconds=0, auto_submit=True), Control(), **kwargs)


def test_actual_login_and_personal_modal_contract(candidate, bank):
    port = FakePort(candidate, bank[:3])
    s = session(port)
    s.login(candidate)
    assert port.view == "home" and not port.modal
    assert port.fields == {"name": candidate.name, "id": candidate.id_number}
    assert ("click", "人 个人资料 ›") in port.actions


def test_existing_login_requires_matching_masked_id(candidate, bank):
    port = FakePort(candidate, bank[:3])
    port.view = "home"
    port.home_id = "110101********9999"
    assert not session(port).verify_identity(candidate)
    assert not port.modal


def test_login_reports_the_actual_rejection_and_can_recover(candidate, bank):
    port = FakePort(candidate, bank[:3])
    original_snapshot, original_click = port.snapshot, port.click
    port.error_text = ""
    def snapshot():
        s = original_snapshot()
        return replace(s, nodes=s.nodes + ((Node("error", "Text", port.error_text),) if port.error_text else ()))
    def click(s, n):
        if n.text == "安全登录":
            port.assert_current(s)
            port.error_text = "登录失败：当前考区未报名"
        else:
            original_click(s, n)
    port.snapshot, port.click = snapshot, click
    s = session(port)
    with pytest.raises(LoginError, match="当前考区未报名"):
        s.login(candidate)
    s.recover_login()
    assert port.view == "login"


def test_filled_login_fields_are_found_by_their_observed_labels(candidate, bank):
    port = FakePort(candidate, bank[:3])
    s = session(port)
    snapshot = Snapshot(nodes(("Text", "姓名"), ("Edit", "旧姓名", "旧姓名"),
        ("Text", "身份证号"), ("Edit", "旧证件", "旧证件"), ("Button", "安全登录")))
    assert s._login_field(snapshot, s.p.name_inputs, "姓名").value == "旧姓名"
    assert s._login_field(snapshot, s.p.id_inputs, "身份证号").value == "旧证件"
    ambiguous = replace(snapshot, nodes=snapshot.nodes + (Node("third", "Edit", "其他"),))
    with pytest.raises(LoginError):
        s._login_field(ambiguous, s.p.name_inputs, "姓名")


def test_previous_inline_error_first_frame_does_not_reject_a_successful_next_login(candidate, bank):
    port = FakePort(candidate, bank[:3])
    original_snapshot, original_click = port.snapshot, port.click
    port.pending_response, port.response_frames = False, 0
    previous_error = "登录失败：上一位考生未报名"
    def snapshot():
        if port.pending_response:
            port.response_frames += 1
            if port.response_frames >= 2:
                port.view = "home"
        s = original_snapshot()
        if port.view == "login":
            return replace(s, nodes=s.nodes + (Node("old_error", "Text", previous_error),))
        return s
    def click(s, n):
        if n.text == "安全登录":
            port.assert_current(s)
            port.pending_response = True
        else:
            original_click(s, n)
    port.snapshot, port.click = snapshot, click
    engine = session(port)
    engine.control.sleep = lambda _: None
    engine.login(candidate)
    assert port.view == "home" and port.response_frames >= 2


def test_complete_three_kinds_then_reverse_review_and_server_score(candidate, bank):
    port = FakePort(candidate, bank[:3])
    s = session(port)
    s.login(candidate)
    s.open_exam(candidate)
    result = s.answer_exam(KnowledgeIndex(bank))
    assert result["score"] == 96  # The server, not the matcher, supplies the final score.
    assert result["total"] == 3
    assert port.answers == {i: set(q.answers) for i, q in enumerate(bank[:3], 1)}
    assert sum(a == ("click", "上一题") for a in port.actions) == 2
    assert port.actions[-1] == ("click", "交卷")


def test_duplicate_text_choices_keep_distinct_letters_through_review(candidate, bank):
    repeated = replace(bank[1], number=433, options={"A": "甲", "B": "相同", "C": "丙", "D": "相同"}, answers=("A", "B", "D"))
    paper = [repeated, bank[0], bank[2]]
    port = FakePort(candidate, paper)
    port.view = "question"
    result = session(port).answer_exam(KnowledgeIndex(paper))
    assert port.answers[1] == {"A", "B", "D"}
    assert result["total"] == 3
    assert port.actions[-1] == ("click", "交卷")


def test_missing_question_never_submits(candidate, bank):
    port = FakePort(candidate, bank[:3])
    port.view = "question"
    with pytest.raises(PageError, match="完全匹配"):
        session(port).answer_exam(KnowledgeIndex(bank[1:]))
    assert not port.actions


def test_unreadable_selection_prevents_any_answer(candidate, bank):
    port = FakePort(candidate, bank[:3])
    port.view = "question"
    original = port.snapshot
    port.snapshot = lambda: replace(original(), nodes=tuple(replace(n, selected=None) if n.kind == "RadioButton" else n for n in original().nodes))
    with pytest.raises(PageError, match="选项状态"):
        session(port).answer_exam(KnowledgeIndex(bank))
    assert not port.actions


def test_second_review_detects_lost_answers(candidate, bank):
    port = FakePort(candidate, bank[:3])
    port.view = "question"
    original = port.click
    def click(s, n):
        original(s, n)
        if n.text == "上一题":
            port.answers[port.number] = set()
    port.click = click
    with pytest.raises(PageError, match="复核不一致"):
        session(port).answer_exam(KnowledgeIndex(bank))
    assert not any(text == "交卷" for _, text in port.actions)


def test_manual_submission_waits_for_operator(candidate, bank):
    port = FakePort(candidate, bank[:3])
    port.view = "question"
    waits = []
    def operator(reason):
        waits.append(reason)
        port.view = "result"
    s = session(port, wait_operator=operator)
    s.options = replace(s.options, auto_submit=False)
    assert s.answer_exam(KnowledgeIndex(bank))["score"] == 96
    assert len(waits) == 1 and not any(text == "交卷" for _, text in port.actions)


def test_no_available_exam_has_explicit_result(candidate, bank):
    port = FakePort(candidate, bank)
    port.view = "home"
    original = port.snapshot
    def snapshot():
        s = original()
        return replace(s, nodes=s.nodes + (Node("empty", "Text", "当前暂无考试与学习安排"),))
    port.snapshot = snapshot
    with pytest.raises(PageError, match="暂无考试"):
        session(port).open_exam(candidate)


def test_stop_does_not_click_next_or_submit(candidate, bank):
    port = FakePort(candidate, bank[:3])
    port.view = "question"
    s = session(port)
    s.control.stop.set()
    with pytest.raises(Stopped):
        s.answer_exam(KnowledgeIndex(bank))
    assert not port.actions


def test_question_parser_rejects_ambiguous_layout(candidate, bank):
    port = FakePort(candidate, bank[:3])
    port.view = "question"
    assert parse_question(port.snapshot()).stem == bank[0].stem
    s = port.snapshot()
    with pytest.raises(PageError, match="题号"):
        parse_question(replace(s, nodes=s.nodes + (Node("other", "Text", "2/99"),)))


def test_type_and_progress_are_not_inferred_from_words_inside_the_question():
    s = Snapshot(nodes(("Text", "2/100"), ("Text", "多选题"),
        ("Text", "请判断下列说法，比例为 1/2 时哪些正确？"),
        ("CheckBox", "A. 判断依据甲", "", False), ("CheckBox", "B. 判断依据乙", "", False)))
    q = parse_question(s)
    assert q.kind == "多选" and q.number == 2 and q.total == 100


def test_numeric_prefix_inside_source_stem_is_preserved():
    stem = "8. 根据材料判断下列合同是否有效。"
    s = Snapshot(nodes(("Text", "1/100"), ("Text", "单选题"), ("Text", stem),
        ("RadioButton", "A. 有效", "", False), ("RadioButton", "B. 无效", "", False)))
    assert parse_question(s).stem == stem


def test_score_parser_needs_result_marker_and_one_score():
    assert parse_score(Snapshot(nodes(("Text", "成绩：98"))), ["考试完成"]) is None
    with pytest.raises(PageError, match="无法唯一"):
        parse_score(Snapshot(nodes(("Text", "考试完成"), ("Text", "成绩：98"), ("Text", "成绩：100"))), ["考试完成"])
