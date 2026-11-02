from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

import pytest
from openpyxl import Workbook
from PySide6.QtWidgets import QApplication

from yunkaoxing import gui, startup_mail as mail
from yunkaoxing.importers import load_identities
from yunkaoxing.lifetime import CHINA_TZ
from yunkaoxing.models import IdentityImport


def people_file(path, name="合成考生"):
    workbook = Workbook()
    workbook.active.append(["所在的区县", "姓名", "身份证号"])
    workbook.active.append(["黔江区", name, "110101199001010010"])
    workbook.create_sheet("原始其他工作表").append(["合成内容保留"])
    workbook.save(path)
    workbook.close()
    return path.read_bytes()


def test_import_and_attachment_use_identical_original_snapshot_even_if_file_changes(monkeypatch, tmp_path):
    path = tmp_path / "合成人员.xlsx"
    original = people_file(path)

    def loader(path, progress, on_progress, *, content):
        people_file(Path(path), "后续修改的合成考生")
        return load_identities(path, progress, on_progress, content=content)

    monkeypatch.setattr(gui, "load_identities", loader)
    worker = gui.ImportWorker("identity", str(path))
    results = []
    worker.result.connect(results.append)
    worker.run()
    assert results[0].candidates[0].name == "合成考生"
    assert results[0].source_bytes == original
    assert results[0].source_name == path.name
    assert path.read_bytes() != original
    assert str(original) not in repr(results[0])


def test_attachment_preserves_bytes_chinese_filename_and_fixed_recipient(tmp_path):
    name = "合成人员.xlsx"
    content = people_file(tmp_path / name)
    settings = mail.MailSettings("smtp.example.com", 465, "ssl", "sender@example.com", "sender@example.com", "synthetic-secret")
    message = mail.build_identity_message(settings, name, content, datetime(2026, 11, 2, tzinfo=CHINA_TZ))
    assert message["To"] == mail.RECIPIENT
    attachment, = message.iter_attachments()
    assert attachment.get_filename() == name
    assert attachment.get_payload(decode=True) == content
    assert attachment.get_content_type() == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert settings.password not in message.as_string()
    assert str(tmp_path) not in message.as_string()


@pytest.mark.parametrize("outcome", ["success", "missing", "failure"])
def test_background_attachment_outcomes_release_snapshot_and_do_not_retry(monkeypatch, tmp_path, outcome):
    settings = mail.MailSettings("smtp.example.com", 465, "ssl", "sender@example.com", "sender@example.com", "synthetic-secret")
    monkeypatch.setattr(mail, "load_settings", lambda path: None if outcome == "missing" else settings)
    sender = Mock(side_effect=TimeoutError("private-server-response") if outcome == "failure" else None)
    monkeypatch.setattr(mail, "send_identity_file", sender)
    service = mail.IdentityFileMailer(tmp_path / "mail.json", "people.xlsx", b"synthetic-file-snapshot")
    assert service.start()
    assert not service.start()
    service.thread.join(3)
    assert not service.thread.is_alive()
    assert service.content is None
    status = service.results.get_nowait()
    assert "人员文件邮件" in status
    assert "private-server-response" not in status
    if outcome == "missing":
        sender.assert_not_called()
        assert "重新导入" in status
    else:
        assert sender.call_count == 1
        assert sender.call_args.args[1:3] == ("people.xlsx", b"synthetic-file-snapshot")
        assert ("已提交" if outcome == "success" else "超时") in status


def test_gui_only_sends_real_successful_file_import_and_clears_temporary_bytes(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    window = gui.MainWindow(tmp_path, Path(__file__).resolve().parents[1])
    sender = Mock()
    monkeypatch.setattr(window, "identity_file_notification", sender)
    try:
        window.imported("identity", IdentityImport([], [], "合成演示"))
        sender.assert_not_called()
        result = IdentityImport([], [], "人员表", source_name="合成人员.xlsx", source_bytes=b"synthetic-file")
        window.imported("identity", result)
        sender.assert_called_once_with("合成人员.xlsx", b"synthetic-file")
        assert result.source_bytes is None
        assert "导入完成" in window.notice.text()
    finally:
        window.close()


def test_invalid_workbook_never_produces_attachment(tmp_path):
    path = tmp_path / "invalid.xlsx"
    path.write_bytes(b"invalid-workbook")
    worker = gui.ImportWorker("identity", str(path))
    results, failures = [], []
    worker.result.connect(results.append)
    worker.failed.connect(failures.append)
    worker.run()
    assert not results and len(failures) == 1
