import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from yunkaoxing.lifetime import CHINA_TZ, EXPIRES_AT, EXPIRY_MESSAGE, ensure_available


@pytest.mark.parametrize("instant", [
    datetime(2027, 6, 1, tzinfo=CHINA_TZ),
    EXPIRES_AT - timedelta(microseconds=1),
    datetime(2027, 6, 30, 15, 59, 59, tzinfo=timezone.utc),
])
def test_june_remains_available(instant):
    ensure_available(instant)


@pytest.mark.parametrize("instant", [
    EXPIRES_AT, EXPIRES_AT + timedelta(days=300),
    datetime(2027, 6, 30, 16, tzinfo=timezone.utc),
])
def test_cutoff_is_inclusive_and_uses_china_time(instant):
    with pytest.raises(ValueError, match="2027 年 7 月 1 日"):
        ensure_available(instant)


def test_naive_clock_is_rejected():
    with pytest.raises(ValueError, match="时区"):
        ensure_available(datetime(2027, 6, 30))


@pytest.mark.parametrize("option", [[], ["--demo"], ["--simulator"]])
def test_every_interactive_entry_stops_before_main_window_and_email(monkeypatch, option):
    import main
    import yunkaoxing.gui as gui
    import yunkaoxing.startup_mail as mail

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr("PySide6.QtWidgets.QApplication", lambda argv: app)
    dialog = Mock()
    monkeypatch.setattr(QMessageBox, "critical", dialog)
    window = Mock(side_effect=AssertionError("must not create workbench"))
    monkeypatch.setattr(gui, "MainWindow", window)
    sender = Mock(side_effect=AssertionError("must not send mail"))
    monkeypatch.setattr(mail.StartupMailer, "start", sender)
    monkeypatch.setattr(main, "china_now", lambda: EXPIRES_AT)
    monkeypatch.setattr("sys.argv", ["main.py", *option])
    assert main.main() == 3
    assert dialog.call_args.args == (None, "软件已到期", EXPIRY_MESSAGE)
    window.assert_not_called()
    sender.assert_not_called()


def test_offline_selftest_cannot_bypass_cutoff(monkeypatch, tmp_path):
    import main
    monkeypatch.setattr(main, "china_now", lambda: EXPIRES_AT)
    monkeypatch.setattr("sys.argv", ["main.py", "--smoke-test", str(tmp_path)])
    assert main.main() == 3
    assert json.loads((tmp_path / "smoke-result.json").read_text("utf-8")) == {"ok": False, "error": EXPIRY_MESSAGE}


def test_open_window_cannot_start_a_new_batch_after_expiry(monkeypatch, tmp_path):
    import yunkaoxing.gui as gui
    app = QApplication.instance() or QApplication([])
    window = gui.MainWindow(tmp_path, __import__("pathlib").Path(__file__).resolve().parents[1])
    warning = Mock()
    monkeypatch.setattr(QMessageBox, "warning", warning)
    monkeypatch.setattr(gui, "ensure_available", lambda: ensure_available(EXPIRES_AT))
    try:
        window.start_batch()
        assert window.worker is None
        assert warning.call_args.args[2] == EXPIRY_MESSAGE
    finally:
        window.close()
