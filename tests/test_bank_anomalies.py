from dataclasses import replace

import pytest

from yunkaoxing.importers import parse_question_lines
from yunkaoxing.knowledge import KnowledgeIndex, sanitize_bank
from yunkaoxing.models import BankImport, Question


def question(number, **changes):
    original = Question(number, "单选", f"合成正常题 {number}", {"A": "甲", "B": "乙"}, ("A",), (1,))
    return replace(original, **changes)


def test_sanitize_keeps_reliable_duplicate_text_options_and_skips_malformed_answers():
    repeated = question(433, kind="多选", options={"A": "甲", "B": "相同", "C": "丙", "D": "相同"}, answers=("A", "B", "D"))
    unknown = question(2, answers=("Z",))
    empty = question(3, options={"A": "", "B": "乙"})
    duplicated_answer = question(4, answers=("A", "A"))
    result = sanitize_bank(BankImport([repeated, unknown, empty, duplicated_answer], [], 1))
    assert result.questions == [repeated]
    assert {issue.number for issue in result.anomalies} == {2, 3, 4}
    assert len(result.issues) == 3
    assert all(issue.raw and issue.pages == (1,) for issue in result.anomalies)
    assert KnowledgeIndex(result.questions).match(repeated.kind, repeated.stem, repeated.options).answers == ("A", "B", "D")


def test_all_conflicting_answers_are_quarantined_without_losing_other_questions():
    first = question(1, stem="同一合成题")
    second = replace(first, number=2, answers=("B",), pages=(2,))
    third = replace(first, number=3, kind="多选", answers=("A", "B"), pages=(3,))
    good = question(4)
    result = sanitize_bank(BankImport([first, second, third, good], [], 3))
    assert result.questions == [good]
    assert {issue.number for issue in result.anomalies} == {1, 2, 3}
    assert all("答案冲突" in issue.reason for issue in result.anomalies)
    with pytest.raises(ValueError, match="冲突"):
        KnowledgeIndex([first, second])


def test_parsing_anomalies_retains_raw_pages_and_partial_fields():
    data = parse_question_lines([
        (1, "1、正常题"), (1, "A. 甲"), (1, "B. 乙"), (1, "正确答案：A"),
        (2, "2、异常答案题"), (2, "A. 甲"), (3, "B. 乙"), (3, "正确答案：Z"),
        (3, "3、缺少答案题"), (3, "A. 甲"), (3, "B. 乙"),
    ], 3)
    assert [q.number for q in data.questions] == [1]
    assert len(data.anomalies) == len(data.issues) == 2
    first, second = data.anomalies
    assert first.number == 2 and first.stem == "异常答案题" and first.pages == (2, 3)
    assert first.options == {"A": "甲", "B": "乙"} and first.answers == ("Z",)
    assert "2、异常答案题" in first.raw and "正确答案：Z" in first.raw
    assert second.number == 3 and "缺少正确答案" in second.reason and second.answers == ()


def test_duplicate_numbers_are_all_reported_and_sanitization_is_idempotent():
    data = parse_question_lines([
        (1, "1、同号甲题"), (1, "A. 甲"), (1, "B. 乙"), (1, "正确答案：A"),
        (2, "1、同号乙题"), (2, "A. 丙"), (2, "B. 丁"), (2, "正确答案：B"),
        (3, "2、正常题"), (3, "A. 戊"), (3, "B. 己"), (3, "正确答案：A"),
    ], 3)
    assert [q.number for q in data.questions] == [2]
    assert len(data.anomalies) == 2 and all("题号重复" in issue.reason for issue in data.anomalies)
    again = sanitize_bank(data)
    assert again.questions == data.questions
    assert again.anomalies == data.anomalies
    assert again.issues == data.issues


@pytest.mark.parametrize("lines", [[], [(1, "只有未识别文字")], [
    (1, "1、仅异常题"), (1, "A. 甲"), (1, "B. 乙"), (1, "正确答案：Z")]])
def test_empty_or_wholly_invalid_import_returns_anomaly_report(lines):
    result = parse_question_lines(lines, 1)
    assert not result.questions
    assert result.anomalies and result.issues


def test_nonconsecutive_options_are_skipped_without_rejecting_the_whole_bank():
    bad = question(1, options={"A": "甲", "C": "丙"})
    good = question(2)
    result = sanitize_bank(BankImport([bad, good], [], 1))
    assert result.questions == [good]
    assert "连续" in result.anomalies[0].reason


def test_can_relabel_rejects_global_answer_mapping_conflicts():
    first = question(1, stem="同题干", options={"A": "甲", "B": "乙", "C": "丙"})
    second = replace(first, number=2, options={"A": "乙", "B": "甲", "C": "丙"})
    knowledge = KnowledgeIndex([first, second])
    assert not knowledge.can_relabel(first.kind, first.stem, first.options)
    assert not knowledge.can_relabel(second.kind, second.stem, second.options)
    assert KnowledgeIndex([first]).can_relabel(first.kind, first.stem, first.options)
