import time
from dataclasses import replace

from PySide6.QtWidgets import QApplication

from yunkaoxing.demo import demo_data
from yunkaoxing.models import RunOptions
from yunkaoxing.profile import SiteProfile
from yunkaoxing.simulator import EXAM_TITLE, SimulatorBridge, SimulatorPort, SimulatorWindow
from yunkaoxing.worker import BatchWorker


def test_rejected_native_login_is_reported_then_next_person_finishes_exam(tmp_path, candidate):
    app = QApplication.instance() or QApplication([])
    _, bank = demo_data()
    people = [candidate, replace(candidate, row=3, name="合成下一位", id_number="110101199001010029")]
    rejected_id = candidate.id_number
    class RejectFirst(SimulatorWindow):
        def login(self):
            if self.id_edit.text().strip() == rejected_id:
                self.error = "登录失败：当前考生未报名"
                self.render()
                return
            super().login()
    sim = RejectFirst(bank.questions, people)
    bridge = SimulatorBridge(sim)
    worker = BatchWorker(SiteProfile(), RunOptions(mode="exam", exam_title=EXAM_TITLE,
                         fastest=True, background=True, simulate_scores=True, auto_submit=True), people, [], bank.questions,
                         str(tmp_path), port_factory=lambda before: SimulatorPort(bridge, before, background=True))
    sim.active = True
    worker.start()
    try:
        deadline = time.monotonic() + 40
        while worker.isRunning() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.001)
        app.processEvents()
        assert not worker.isRunning()
        first, second = worker.store.records
        assert first.status == "failed" and "未报名" in first.reason and first.score is None
        assert second.status == "completed" and 98 <= second.score <= 100
        assert len(sim.history) == 1 and sim.history[0]["id"] == people[1].id_number
    finally:
        worker.control.stop.set()
        while worker.isRunning():
            app.processEvents()
            time.sleep(.001)
        sim.active = False
        sim.close()
