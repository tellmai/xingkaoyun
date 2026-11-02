"""Optional regression against the user's reference PDF, never shipped in a release."""
from pathlib import Path

import pytest

from yunkaoxing.importers import load_bank
from yunkaoxing.knowledge import KnowledgeIndex, option_texts_are_unique
from yunkaoxing.knowledge import normalize
from yunkaoxing.page import Node, Snapshot, parse_question


REFERENCE = Path(__file__).resolve().parents[2] / "filedata" / "题库.pdf"


@pytest.fixture(scope="module")
def reference_bank():
    if not REFERENCE.is_file():
        pytest.skip("用户的参考 PDF 不随项目分发")
    return load_bank(str(REFERENCE))


def test_reference_bank_imports_and_matches_all_1021_questions(reference_bank):
    assert reference_bank.page_count == 276
    assert len(reference_bank.questions) == 1021
    assert not reference_bank.issues
    knowledge = KnowledgeIndex(reference_bank.questions)
    for q in reference_bank.questions:
        assert knowledge.match(q.kind, q.stem, q.options).answers == tuple(sorted(q.answers))


def test_reference_questions_433_and_434_keep_distinct_answer_letters(reference_bank):
    questions = {q.number: q for q in reference_bank.questions}
    knowledge = KnowledgeIndex(reference_bank.questions)
    assert not option_texts_are_unique(questions[433].options)
    assert not option_texts_are_unique(questions[434].options)
    assert knowledge.match(questions[433].kind, questions[433].stem, questions[433].options).answers == ("A", "B", "D")
    assert knowledge.match(questions[434].kind, questions[434].stem, questions[434].options).answers == ("A", "B", "C", "D")


def test_reference_bank_questions_remain_readable_in_simulator_page_layout(reference_bank):
    for q in reference_bank.questions:
        kind = "CheckBox" if q.kind == "多选" else "RadioButton"
        nodes = [Node("progress", "Text", "1/100"), Node("kind", "Text", q.kind + "题"), Node("stem", "Text", q.stem)]
        nodes += [Node(label, kind, f"{label}. {text}", selected=False) for label, text in q.options.items()]
        parsed = parse_question(Snapshot(tuple(nodes)))
        assert parsed.kind == q.kind, q.number
        assert normalize(parsed.stem) == normalize(q.stem), q.number
        assert {label: normalize(text) for label, text in parsed.options.items()} == {label: normalize(text) for label, text in q.options.items()}, q.number
