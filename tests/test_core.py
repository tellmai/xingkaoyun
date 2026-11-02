import json
import random
from dataclasses import replace

import pytest
from openpyxl import Workbook, load_workbook

from yunkaoxing.engine import Control, Stopped
from yunkaoxing.importers import load_identities, parse_question_lines
from yunkaoxing.knowledge import KnowledgeIndex
from yunkaoxing.models import RunOptions
from yunkaoxing.practice import grade, make_paper, run_practice
from yunkaoxing.privacy import redact
from yunkaoxing.profile import SiteProfile
from yunkaoxing.store import BatchStore, StoreError, recover_interrupted


def test_excel_import_preserves_text_ids_and_rejects_precision_loss(tmp_path, candidate):
    wb = Workbook()
    wb.active.append(["无关页"])
    ws = wb.create_sheet("身份")
    ws.append(["所在的区县", "姓名", "身份证号"])
    ws.append([candidate.district, candidate.name, candidate.id_number])
    ws.append([candidate.district, "无效", int(candidate.id_number)])
    path = tmp_path / "people.xlsx"
    wb.save(path)
    data = load_identities(str(path))
    assert data.sheet == "身份" and len(data.candidates) == 1 and len(data.rejected) == 1
    assert data.candidates[0].id_number == candidate.id_number
    assert candidate.id_number not in repr(candidate)


def test_pdf_parser_continuations_and_invalid_answers():
    data = parse_question_lines([(1, "1、合成题干"), (1, "A. 甲"), (2, "继续甲"),
        (2, "B. 乙"), (2, "正确答案：AB"), (2, "2、坏题"), (2, "A. 甲"), (2, "正确答案：Z")], 2)
    assert len(data.questions) == 1 and len(data.issues) == 1
    assert data.questions[0].kind == "多选" and data.questions[0].pages == (1, 2)


def test_option_reordering_does_not_change_answer_text(bank):
    q = bank[1]
    result = KnowledgeIndex(bank).match(q.kind, q.stem, {"A": q.options["B"], "B": q.options["C"], "C": q.options["A"]})
    assert result.answers == ("B", "C")


def test_unknown_or_conflicting_question_stops(bank):
    with pytest.raises(ValueError, match="完全匹配"):
        KnowledgeIndex(bank).match("单选", "未知题", {"A": "a", "B": "b"})
    with pytest.raises(ValueError, match="冲突"):
        KnowledgeIndex([bank[0], replace(bank[0], number=999, answers=("B",))])


@pytest.mark.parametrize("options", [{"A": "相同", "B": "相同", "C": "其他"}, {"A": "", "B": "乙", "C": "丙"}])
def test_duplicate_or_empty_option_text_uses_original_labels(bank, options):
    q = replace(bank[0], number=433, options=options, answers=("A",))
    knowledge = KnowledgeIndex([q])
    assert knowledge.match(q.kind, q.stem, options).answers == ("A",)
    changed = {"A": options["C"], "B": options["A"], "C": options["B"]}
    with pytest.raises(ValueError, match="完全匹配"):
        knowledge.match(q.kind, q.stem, changed)


def test_ambiguous_text_mapping_stays_disabled(bank):
    first = replace(bank[0], options={"A": "甲", "B": "乙", "C": "丙"}, answers=("A",))
    second = replace(first, number=900, options={"A": "乙", "B": "甲", "C": "丙"}, answers=("A",))
    knowledge = KnowledgeIndex([first, second, first])
    assert knowledge.match(first.kind, first.stem, first.options).answers == ("A",)
    assert knowledge.match(second.kind, second.stem, second.options).answers == ("A",)
    with pytest.raises(ValueError, match="完全匹配"):
        knowledge.match(first.kind, first.stem, {"A": "丙", "B": "乙", "C": "甲"})


def test_practice_independent_scoring_and_range(bank):
    paper = make_paper(bank, random.Random(7))
    assert len(paper) == len({q.number for q in paper}) == 100
    assert {q.kind for q in paper} == {"单选", "多选", "判断"}
    answers = {i: q.answers for i, q in enumerate(paper)}
    assert grade(paper, answers) == 100
    answers[0] = ()
    assert grade(paper, answers) == 99
    for target in (0, 90, 98, 100):
        result = run_practice(bank, RunOptions(mode="practice", minimum_seconds=0, score_min=target, score_max=target, minimum_score=0), Control())
        assert result["score"] == target and result["total"] == 100


def test_stop_interrupts_before_work(bank):
    control = Control()
    control.stop.set()
    with pytest.raises(Stopped):
        run_practice(bank, RunOptions(mode="practice"), control)


def test_store_exports_and_resume_without_completed_people(tmp_path, candidate):
    people = [candidate, replace(candidate, row=3, name="=SUM(1,2)", id_number="110101199001010029")]
    store = BatchStore(tmp_path, people)
    store.update(0, status="completed", score=99)
    store.update(1, status="running")
    data = json.loads((store.folder / "人员状态.json").read_text(encoding="utf-8"))
    assert data["records"][0]["score"] == 99
    wb = load_workbook(store.folder / "未完成信息统计.xlsx")
    assert wb.active["B2"].data_type == "s"
    assert wb.active["C2"].value == people[1].id_number
    wb.close()
    assert recover_interrupted(store.folder) == people[1:]
    assert load_identities(str(store.folder / "未完成信息统计.xlsx")).candidates[0].id_number == people[1].id_number


def test_store_does_not_swallow_io_failure(tmp_path, candidate, monkeypatch):
    store = BatchStore(tmp_path, [candidate])
    monkeypatch.setattr(store, "export", lambda: (_ for _ in ()).throw(PermissionError()))
    with pytest.raises(StoreError):
        store.persist()
    assert (store.folder / "人员状态.json").exists()


def test_secrets_are_redacted(candidate):
    safe = redact(f"{candidate.name} {candidate.id_number}\nCall log: fields", [candidate.name])
    assert candidate.name not in safe and candidate.id_number not in safe and "fields" not in safe


def test_profile_is_restricted_to_known_mini_program(tmp_path):
    path = tmp_path / "profile.json"
    path.write_text('{"app_id":"another-app"}', encoding="utf-8")
    with pytest.raises(ValueError):
        SiteProfile.load(path)


@pytest.mark.parametrize("options", [RunOptions(mode="exam"), RunOptions(mode="practice", minimum_seconds=-1), RunOptions(mode="practice", score_min=101), RunOptions(mode="bad"), RunOptions(mode="practice", minimum_seconds=600, maximum_seconds=300), RunOptions(mode="practice", minimum_score=99, score_max=98), RunOptions(mode="practice", maximum_seconds=float("nan"))])
def test_invalid_options(options):
    with pytest.raises(ValueError):
        options.validate()


def test_fastest_practice_does_not_wait_for_the_time_range(bank):
    control = Control()
    control.sleep = lambda _: pytest.fail("最快完成不能等待时间范围")
    result = run_practice(bank, RunOptions(mode="practice", fastest=True, minimum_seconds=600, maximum_seconds=900), control)
    assert 98 <= result["score"] <= 100


def test_fastest_ignores_disabled_time_range_order():
    RunOptions(mode="practice", fastest=True, minimum_seconds=900, maximum_seconds=600).validate()


def test_login_only_ignores_inactive_exam_settings():
    RunOptions(mode="login", exam_title="", minimum_seconds=900, maximum_seconds=600,
               score_min=100, score_max=98, minimum_score=99).validate()


@pytest.mark.parametrize("value", [0, 31, True, 1.5])
def test_concurrency_requires_a_bounded_integer(value):
    with pytest.raises(ValueError, match="并发数量"):
        RunOptions(concurrency=value).validate()
