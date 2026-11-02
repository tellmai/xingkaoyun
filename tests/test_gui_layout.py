from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from yunkaoxing.gui import MainWindow
from yunkaoxing.models import BankImport, QuestionIssue


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def ui_app():
    return QApplication.instance() or QApplication([])


def test_workbench_has_visible_test_switch_and_fixed_actions_at_minimum_size(ui_app, tmp_path):
    window = MainWindow(tmp_path, ROOT)
    try:
        window.resize(1000, 720)
        window.show()
        ui_app.processEvents()
        assert window.settings_tabs.tabText(0) == "执行与模拟"
        assert window.settings_tabs.tabText(1) == "连接与保存"
        assert window.test_mode.isVisible()
        for widget in (window.start, window.stop, window.check, window.log_toggle):
            rect = widget.rect()
            assert window.rect().contains(widget.mapTo(window, rect.topLeft()))
            assert window.rect().contains(widget.mapTo(window, rect.bottomRight()))
        assert window.identity_table.empty_hint.isVisible()
        assert not window.log_drawer.isVisible()
        window.log_toggle.setChecked(True)
        ui_app.processEvents()
        assert window.log_drawer.isVisible()
        window.log_toggle.setChecked(False)
        assert not window.log_drawer.isVisible()
    finally:
        window.close()


def test_test_switch_and_fastest_controls_keep_workflow_in_sync(ui_app, tmp_path):
    window = MainWindow(tmp_path, ROOT)
    try:
        window.test_mode.setChecked(False)
        assert window.target.currentData() == "wechat"
        assert window.mode.currentData() == "login"
        assert window.score_min.isEnabled()
        window.test_mode.setChecked(True)
        assert window.target.currentData() == "simulator"
        assert window.score_min.isEnabled()
        window.fastest.setChecked(True)
        assert not window.minimum.isEnabled() and not window.maximum.isEnabled()
        window.fastest.setChecked(False)
        assert window.minimum.isEnabled() and window.maximum.isEnabled()
        assert window.concurrency.value() == 1 and window.concurrency.isEnabled()
        assert window.concurrency.maximum() == 30
    finally:
        window.close()


def test_anomalies_remain_inspectable_when_no_valid_questions(ui_app, tmp_path):
    window = MainWindow(tmp_path, ROOT)
    try:
        issue = QuestionIssue(433, "单选", "演示异常题干", {"A": "甲", "B": "乙"}, (), (17, 18), "缺少正确答案", "433、演示异常题干\nA.甲\nB.乙")
        window.imported("bank", BankImport([], ["第 433 题：缺少正确答案"], 18, [issue]))
        assert window.questions == []
        assert window.bank_tabs.currentIndex() == 1
        assert window.anomaly_list.count() == 1
        assert "433" in window.anomaly_list.item(0).text()
        detail = window.anomaly_detail.toPlainText()
        assert "缺少正确答案" in detail and "17、18" in detail
        assert "演示异常题干" in detail and "PDF 原文" in detail
    finally:
        window.close()


def test_question_filters_include_option_text_and_kind(ui_app, tmp_path):
    window = MainWindow(tmp_path, ROOT)
    try:
        window.load_demo()
        window.kind.setCurrentText("多选")
        window.search.setText("演示选项丙")
        assert len(window.filtered) == 50
        assert all(q.kind == "多选" for q in window.filtered)
        assert window.fastest.isChecked()
    finally:
        window.close()
