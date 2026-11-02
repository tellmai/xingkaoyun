import time

import pytest
from PySide6.QtWidgets import QApplication

from yunkaoxing.demo import demo_data
from yunkaoxing.gui import MainWindow
from yunkaoxing.simulator import SimulatorBridge, SimulatorPort, SimulatorWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def pump(app, predicate, timeout=45):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.001)
    app.processEvents()
    assert predicate(), "Qt worker did not finish"


def test_three_people_complete_native_login_exam_review_and_export(app, tmp_path):
    root = __import__('pathlib').Path(__file__).resolve().parents[1]
    window = MainWindow(tmp_path, root)
    window.load_demo()
    window.background.setChecked(True)
    window.candidates = window.candidates[:3]
    window.output.setText(str(tmp_path / "results"))
    window.start_batch()
    worker = window.worker
    try:
        pump(app, lambda: not worker.isRunning())
        assert len(worker.store.records) == 3
        assert all(r.status == "completed" and 98 <= r.score <= 100 and r.total == 100 for r in worker.store.records)
        assert worker.store.mode == "simulator_exam"
        assert len(window.simulator.history) == 3
        assert window.simulator.stage == "result"
        assert (worker.store.folder / "已完成信息统计.xlsx").exists()
    finally:
        worker.control.stop.set()
        pump(app, lambda: not worker.isRunning())
        window.close()


def test_stop_waits_for_current_person_and_keeps_rest_pending(app, tmp_path):
    root = __import__('pathlib').Path(__file__).resolve().parents[1]
    window = MainWindow(tmp_path, root)
    window.load_demo()
    window.background.setChecked(True)
    window.candidates = window.candidates[:2]
    window.output.setText(str(tmp_path / "results"))
    window.start_batch()
    worker = window.worker
    worker.message.connect(lambda text: worker.control.after_current.set() if "第 10/100 题" in text else None)
    try:
        pump(app, lambda: not worker.isRunning())
        assert worker.store.records[-1].status == "pending"
        assert worker.store.records[0].status == "completed"
        assert len(window.simulator.history) == 1
    finally:
        worker.control.stop.set()
        pump(app, lambda: not worker.isRunning())
        window.close()


def test_immediate_stop_keeps_current_unsubmitted_and_next_pending(app, tmp_path):
    root = __import__('pathlib').Path(__file__).resolve().parents[1]
    window = MainWindow(tmp_path, root)
    window.load_demo()
    window.background.setChecked(True)
    window.candidates = window.candidates[:2]
    window.output.setText(str(tmp_path / "results"))
    window.start_batch()
    worker = window.worker
    worker.message.connect(lambda text: worker.control.stop.set() if "第 5/100 题" in text else None)
    try:
        pump(app, lambda: not worker.isRunning())
        assert worker.store.records[0].status == "stopped"
        assert worker.store.records[1].status == "pending"
        assert window.simulator.history == []
        assert window.simulator.stage == "question"
    finally:
        worker.control.stop.set()
        pump(app, lambda: not worker.isRunning())
        window.close()


def test_simulator_does_not_submit_incomplete_paper_or_duplicate_results(app):
    _, bank = demo_data()
    sim = SimulatorWindow(bank.questions)
    sim.district = "黔江区"
    from yunkaoxing.models import Candidate
    sim.candidate = Candidate(0, "黔江区", "合成考生", "110101199001010010")
    sim.start_exam()
    sim.ask_submit()
    assert sim.stage == "question" and not sim.submit_confirm and "100" in sim.error
    sim.answers = {i: set(q.answers) for i, q in enumerate(sim.paper)}
    sim.submit()
    assert sim.result["score"] == 100 and len(sim.history) == 1
    sim.submit()
    assert len(sim.history) == 1
    sim.close()


def test_native_port_rejects_stale_observations(app):
    _, bank = demo_data()
    sim = SimulatorWindow(bank.questions)
    port = SimulatorPort(SimulatorBridge(sim))
    old = port.snapshot()
    node = old.find(["选择黔江区"], ("Button",))
    port.snapshot()
    from yunkaoxing.page import PageError
    with pytest.raises(PageError, match="变化"):
        port.click(old, node)
    sim.close()


def test_native_simulator_keeps_duplicate_text_options_in_original_order(app, bank, candidate):
    from dataclasses import replace
    from yunkaoxing.knowledge import KnowledgeIndex
    from yunkaoxing.page import parse_question
    repeated = replace(bank[1], number=433, options={"A": "甲", "B": "相同", "C": "丙", "D": "相同"}, answers=("A", "B", "D"))
    source = bank[:99] + [repeated]
    sim = SimulatorWindow(source)
    sim.candidate, sim.district = candidate, candidate.district
    sim.start_exam()
    sim.number = next(i for i, q in enumerate(sim.paper) if q.number == 433)
    sim.render()
    q = parse_question(sim.snapshot())
    assert q.options == repeated.options
    assert KnowledgeIndex(source).match(q.kind, q.stem, q.options).answers == ("A", "B", "D")
    sim.close()


def test_native_simulator_does_not_shuffle_conflicting_layouts(app, bank, candidate):
    from dataclasses import replace
    first = replace(bank[0], number=9001, options={"A": "甲", "B": "乙", "C": "丙"}, answers=("A",))
    second = replace(first, number=9002, options={"A": "乙", "B": "甲", "C": "丙"}, answers=("A",))
    source = bank[:98] + [first, second]
    sim = SimulatorWindow(source)
    sim.candidate, sim.district = candidate, candidate.district
    sim.start_exam()
    for expected in (first, second):
        q = next(q for q in sim.paper if q.number == expected.number)
        assert q.options == expected.options and q.answers == expected.answers
    sim.close()
