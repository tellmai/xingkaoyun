from pathlib import Path
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import QObject, Signal

from yunkaoxing.gui import MainWindow
from yunkaoxing.models import IdentityImport
from yunkaoxing.store import Record


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def gui_app():
    return QApplication.instance() or QApplication([])


def test_import_bars_show_weighted_progress_and_wait_for_display(gui_app, tmp_path):
    window = MainWindow(tmp_path, ROOT)
    try:
        window.set_import_progress("identity", 0, 0, "打开 Excel")
        assert window.identity_import_progress.maximum() == 0
        window.set_import_progress("identity", 25, 100, "读取人员")
        assert window.identity_import_progress.value() == 25
        window.set_import_progress("bank", 276, 276, "读取 PDF")
        assert window.bank_import_progress.value() == 90
        assert "276/276" in window.bank_import_caption.text()
        window.set_import_progress("bank", 20, 20, "解析题目")
        assert window.bank_import_progress.value() == 98
        window.set_import_progress("bank", 1, 1, "完成")
        assert window.bank_import_progress.value() == 99
        window.load_demo()
        assert window.identity_import_progress.value() == 100
        assert window.bank_import_progress.value() == 100
    finally:
        window.close()


def test_import_failure_leaves_clear_failure_state(gui_app, tmp_path, monkeypatch):
    window = MainWindow(tmp_path, ROOT)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: None)
    try:
        window.set_import_progress("bank", 0, 0, "打开 PDF")
        window.import_failed("bank", "文件无法读取")
        assert window.bank_import_progress.maximum() == 100
        assert window.bank_import_progress.value() < 100
        assert "导入失败" in window.bank_import_caption.text()
    finally:
        window.close()


def test_background_and_mini_program_name_are_persisted(gui_app, tmp_path):
    window = MainWindow(tmp_path, ROOT)
    assert not window.background.isChecked()
    window.background.setChecked(True)
    window.mini_program_name.setText("云考星测试窗口")
    window.close()
    restored = MainWindow(tmp_path, ROOT)
    try:
        assert restored.background.isChecked()
        assert restored.mini_program_name.text() == "云考星测试窗口"
    finally:
        restored.close()


def test_exam_score_goals_remain_editable_and_saved_in_real_login_mode(gui_app, tmp_path):
    window = MainWindow(tmp_path, ROOT)
    try:
        window.test_mode.setChecked(False)
        assert window.mode.currentData() == "login"
        assert all(box.isEnabled() for box in (window.minimum_score, window.score_min, window.score_max))
        assert window.score_min.accessibleName() == "得分下限"
        assert window.score_max.accessibleName() == "得分上限"
        assert "模拟" not in window.score_hint.text()
        window.minimum_score.setValue(80)
        window.score_min.setValue(92)
        window.score_max.setValue(96)
    finally:
        window.close()
    restored = MainWindow(tmp_path, ROOT)
    try:
        assert restored.target.currentData() == "wechat"
        assert (restored.minimum_score.value(), restored.score_min.value(), restored.score_max.value()) == (80, 92, 96)
        assert restored.score_min.isEnabled() and restored.score_max.isEnabled()
        restored.test_mode.setChecked(True)
        assert restored.score_hint.text() == "考试目标分数"
        assert (restored.score_min.value(), restored.score_max.value()) == (92, 96)
    finally:
        restored.close()


def test_explicit_open_simulator_stays_visible_in_background_mode(gui_app, tmp_path):
    window = MainWindow(tmp_path, ROOT)
    try:
        window.load_demo()
        window.background.setChecked(True)
        window.open_simulator(show=False)
        assert not window.simulator.isVisible()
        window.show_simulator.click()
        gui_app.processEvents()
        assert window.simulator.isVisible()
        assert window.background.isChecked()
    finally:
        window.close()


@pytest.mark.parametrize("finished_all_people", [True, False])
def test_simulator_closes_only_after_the_whole_batch_finishes(gui_app, tmp_path, finished_all_people):
    window = MainWindow(tmp_path, ROOT)
    try:
        window.load_demo()
        window.open_simulator(show=True)
        assert window.simulator.isVisible()
        window.worker = SimpleNamespace(finished_all_people=finished_all_people, isRunning=lambda: False)
        window.worker_finished()
        assert window.simulator.isVisible() is not finished_all_people
    finally:
        window.close()


def test_test_switch_restores_simulator_concurrency_and_uses_login_only_when_off(gui_app, tmp_path):
    window = MainWindow(tmp_path, ROOT)
    try:
        assert window.concurrency.isEnabled()
        window.concurrency.setValue(5)
        window.test_mode.setChecked(False)
        assert window.target.currentData() == "wechat" and window.mode.currentData() == "login"
        assert window.concurrency.value() == 1 and not window.concurrency.isEnabled()
        window.test_mode.setChecked(True)
        assert window.mode.currentData() == "exam"
        assert window.concurrency.isEnabled() and window.concurrency.value() == 5
        assert window.concurrency.maximum() == 30
    finally:
        window.close()


def test_stale_saved_mode_cannot_enable_exams_when_test_switch_is_off(gui_app, tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "settings.json").write_text(json.dumps({"target": "wechat", "mode": "exam", "concurrency": 8}), encoding="utf-8")
    window = MainWindow(tmp_path, ROOT)
    try:
        assert not window.test_mode.isChecked() and window.mode.currentData() == "login"
        window.test_mode.setChecked(True)
        assert window.concurrency.value() == 8
    finally:
        window.close()


def test_new_identity_import_replaces_old_scores_with_pending_preview(gui_app, tmp_path):
    window = MainWindow(tmp_path, ROOT)
    try:
        window.load_demo()
        old = window.candidates[0]
        window.store = object()
        window.result_folder = tmp_path / "old-results"
        window.records = [Record(old, status="completed", score=100)]
        candidate = replace(old, row=8, name="另一名合成考生")
        rejected = replace(old, row=9, name="无效合成考生")
        window.imported("identity", IdentityImport([candidate], ["第9行格式不正确"], "新工作表", [(rejected, "资料格式不正确")]))
        assert window.store is None and window.result_folder is None
        assert [r.candidate for r in window.records] == [candidate, rejected]
        assert [r.status for r in window.records] == ["pending", "failed"]
        assert all(r.score is None for r in window.records)
        assert window.result_table.rowCount() == 2
        assert window.result_tables["completed"].rowCount() == 0
        assert window.result_tables["failed"].rowCount() == 1
        assert not window.export.isEnabled() and not window.open_results.isEnabled()
        assert not (tmp_path / "old-results").exists()
    finally:
        window.close()


@pytest.mark.parametrize("background", [False, True])
def test_batch_creates_independent_visible_or_hidden_simulator_slots(gui_app, tmp_path, monkeypatch, background):
    import yunkaoxing.gui as gui_module
    class DummyWorker(QObject):
        message = Signal(str)
        started_batch = Signal(object)
        record_changed = Signal(int, object)
        needs_operator = Signal(str)
        completed = Signal(str)
        finished = Signal()
        def __init__(self, *args, **kwargs):
            super().__init__(args[7])
            self.profile, self.options, self.arguments = args[0], args[1], kwargs
        def isRunning(self):
            return False
        def start(self):
            pass
    monkeypatch.setattr(gui_module, "BatchWorker", DummyWorker)
    window = MainWindow(tmp_path, ROOT)
    try:
        window.load_demo()
        window.background.setChecked(background)
        window.concurrency.setValue(2)
        window.mini_program_name.setText("自定义云考星窗口")
        window.start_batch()
        assert window.worker.profile.window_title == "自定义云考星窗口"
        assert window.worker.options.background == background
        assert window.worker.options.concurrency == 2
        assert len(window.simulators) == 2
        assert all(sim.isVisible() == (not background) for sim in window.simulators)
        factory = window.worker.arguments["slot_port_factory"]
        assert factory(0, lambda: None).bridge is not factory(1, lambda: None).bridge
    finally:
        window.close()
