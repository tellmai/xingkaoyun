import json
import smtplib
import ssl
import threading
from dataclasses import replace
from datetime import datetime
from unittest.mock import MagicMock, Mock

import pytest
from PySide6.QtWidgets import QApplication, QDialog

from yunkaoxing import __version__
from yunkaoxing.lifetime import CHINA_TZ
from yunkaoxing import startup_mail as mail


@pytest.fixture
def settings():
    return mail.MailSettings("smtp.example.com", 465, "ssl", "sender@example.com", "sender@example.com", "synthetic-secret")


@pytest.fixture
def started():
    return datetime(2026, 11, 2, 12, 34, 56, tzinfo=CHINA_TZ)


def test_notification_has_only_version_time_and_fixed_recipient(settings, started):
    message = mail.build_message(settings, started)
    assert message["To"] == "1564532736@qq.com"
    assert message["From"] == settings.sender
    assert message.get_content() == f"软件版本：云考星助手 v{__version__}\n启动时间：2026-11-02 12:34:56（北京时间）\n"
    assert list(message.iter_attachments()) == []
    assert settings.password not in message.as_string()
    assert settings.password not in repr(settings)


@pytest.mark.parametrize("security", ["ssl", "starttls"])
def test_smtp_uses_verified_tls_and_authenticates_after_upgrade(monkeypatch, settings, started, security):
    settings = replace(settings, security=security)
    smtp = MagicMock()
    smtp.__enter__.return_value = smtp
    smtp.send_message.return_value = {}
    factory = Mock(return_value=smtp)
    unused = Mock(side_effect=AssertionError("wrong transport"))
    monkeypatch.setattr(mail.smtplib, "SMTP_SSL" if security == "ssl" else "SMTP", factory)
    monkeypatch.setattr(mail.smtplib, "SMTP" if security == "ssl" else "SMTP_SSL", unused)
    mail.send_notification(settings, started)
    assert factory.call_args.args == (settings.host, settings.port)
    assert factory.call_args.kwargs["timeout"] == 8
    assert factory.call_args.kwargs["local_hostname"] == "localhost"
    context = factory.call_args.kwargs.get("context") if security == "ssl" else smtp.starttls.call_args.kwargs["context"]
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
    if security == "starttls":
        names = [call[0] for call in smtp.mock_calls]
        assert names.index("starttls") < names.index("login") < names.index("send_message")
    else:
        smtp.starttls.assert_not_called()
    smtp.login.assert_called_once_with(settings.username, settings.password)
    assert smtp.send_message.call_args.kwargs == {"from_addr": settings.sender, "to_addrs": [mail.RECIPIENT]}


def test_starttls_failure_never_sends_credentials(monkeypatch, settings, started):
    smtp = MagicMock()
    smtp.__enter__.return_value = smtp
    smtp.starttls.side_effect = smtplib.SMTPNotSupportedError("no TLS")
    monkeypatch.setattr(mail.smtplib, "SMTP", Mock(return_value=smtp))
    with pytest.raises(smtplib.SMTPNotSupportedError):
        mail.send_notification(replace(settings, security="starttls"), started)
    smtp.login.assert_not_called()
    smtp.send_message.assert_not_called()


@pytest.mark.parametrize("change", [
    {"host": "https://example.com"}, {"host": "example.com\r\nsecret"},
    {"port": 0}, {"port": 65536}, {"port": True}, {"security": "plain"},
    {"sender": "a@example.com\nBcc:bad@example.com"}, {"sender": "a\x00@example.com"},
    {"username": ""}, {"password": ""},
])
def test_invalid_settings_are_rejected_before_network(settings, change, monkeypatch, started):
    connect = Mock(side_effect=AssertionError("network not allowed"))
    monkeypatch.setattr(mail.smtplib, "SMTP_SSL", connect)
    monkeypatch.setattr(mail.smtplib, "SMTP", connect)
    with pytest.raises(ValueError):
        mail.send_notification(replace(settings, **change), started)
    connect.assert_not_called()


def test_config_roundtrip_uses_real_current_user_dpapi(tmp_path, settings):
    path = tmp_path / "data" / "startup-mail.json"
    mail.save_settings(path, settings)
    text = path.read_text("utf-8")
    assert settings.password not in text
    assert "password" not in json.loads(text)
    assert mail.load_settings(path) == settings
    assert not path.with_suffix(".tmp").exists()


def test_missing_and_unreadable_configs(tmp_path):
    path = tmp_path / "startup-mail.json"
    assert mail.load_settings(path) is None
    path.write_text("bad-json", encoding="utf-8")
    with pytest.raises(ValueError, match="无法读取"):
        mail.load_settings(path)
    path.write_text('{"schema": 1, "credential_protected": "bad-secret"}', encoding="utf-8")
    with pytest.raises(ValueError, match="重新配置"):
        mail.load_settings(path)


def test_encryption_failure_never_saves_plaintext(monkeypatch, tmp_path, settings):
    monkeypatch.setattr(mail, "protect_secret", Mock(side_effect=RuntimeError(settings.password)))
    path = tmp_path / "startup-mail.json"
    with pytest.raises(ValueError, match="无法加密") as caught:
        mail.save_settings(path, settings)
    assert settings.password not in str(caught.value)
    assert not path.exists()


def test_background_start_returns_while_network_is_blocked_and_sends_once(monkeypatch, settings, started, tmp_path):
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(mail, "load_settings", lambda path: settings)

    def send(config, instant):
        assert instant == started
        entered.set()
        assert release.wait(3)

    sender = Mock(side_effect=send)
    monkeypatch.setattr(mail, "send_notification", sender)
    service = mail.StartupMailer(tmp_path / "mail.json")
    try:
        assert service.start(started_at=started)
        assert entered.wait(2)
        assert service.thread.daemon and service.thread.is_alive()
        assert not service.start(started_at=started)
    finally:
        release.set()
        service.thread.join(3)
    assert not service.thread.is_alive()
    sender.assert_called_once()
    assert "已提交邮件服务器" in service.results.get_nowait()


def test_unconfigured_start_does_not_connect(monkeypatch, tmp_path, started):
    sender = Mock()
    monkeypatch.setattr(mail, "send_notification", sender)
    service = mail.StartupMailer(tmp_path / "mail.json")
    service.start(started_at=started)
    service.thread.join(3)
    assert "未发送" in service.results.get_nowait()
    sender.assert_not_called()


@pytest.mark.parametrize("error,reason", [
    (smtplib.SMTPAuthenticationError(535, b"sensitive-response"), "认证失败"),
    (ssl.SSLCertVerificationError("sensitive-response"), "证书"),
    (TimeoutError("sensitive-response"), "超时"),
    (smtplib.SMTPRecipientsRefused({mail.RECIPIENT: "sensitive-response"}), "拒绝"),
    (ValueError("sensitive-response"), "重新配置"),
    (RuntimeError("sensitive-response"), "未确认接收"),
])
def test_background_failures_are_explained_without_leaking_server_response(monkeypatch, settings, started, tmp_path, error, reason):
    monkeypatch.setattr(mail, "load_settings", lambda path: settings)
    monkeypatch.setattr(mail, "send_notification", Mock(side_effect=error))
    service = mail.StartupMailer(tmp_path / "mail.json")
    service.start(started_at=started)
    service.thread.join(3)
    status = service.results.get_nowait()
    assert reason in status
    assert "sensitive-response" not in status


def test_dialog_saves_without_sending_mail(monkeypatch, tmp_path):
    from yunkaoxing.mail_dialog import MailSettingsDialog
    app = QApplication.instance() or QApplication([])
    sender = Mock(side_effect=AssertionError("saving must not send"))
    monkeypatch.setattr(mail, "send_notification", sender)
    path = tmp_path / "mail.json"
    dialog = MailSettingsDialog(path)
    dialog.host.setText("smtp.example.com")
    dialog.sender.setText("sender@example.com")
    dialog.password.setText("synthetic-secret")
    dialog.save()
    assert dialog.result() == QDialog.Accepted
    assert mail.load_settings(path).username == "sender@example.com"
    sender.assert_not_called()
    dialog.close()
