from queue import Empty

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
    QMessageBox, QSpinBox, QVBoxLayout,
)

from .startup_mail import RECIPIENT, IdentityFileMailer, MailSettings, StartupMailer, load_settings, save_settings


def watch_startup_notification(parent, path, started_at, on_result):
    service = StartupMailer(path)
    parent.startup_mail_service = service
    watch_mail_job(parent, service, started_at, on_result)


def watch_identity_file_notification(parent, path, name, content, on_result):
    from .lifetime import china_now
    watch_mail_job(parent, IdentityFileMailer(path, name, content), china_now(), on_result)


def watch_mail_job(parent, service, started_at, on_result):
    timer = QTimer(parent)
    timer.setInterval(200)

    def poll():
        try:
            result = service.results.get_nowait()
        except Empty:
            return
        timer.stop()
        on_result(result)
        parent._background_mail_jobs.remove((service, timer))
        timer.deleteLater()

    timer.timeout.connect(poll)
    if not hasattr(parent, "_background_mail_jobs"):
        parent._background_mail_jobs = []
    parent._background_mail_jobs.append((service, timer))
    service.start(started_at=started_at)
    timer.start()


class MailSettingsDialog(QDialog):
    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = path
        self.setWindowTitle("邮件设置")
        self.resize(540, 440)
        layout = QVBoxLayout(self)
        note = QLabel(f"收件人：{RECIPIENT}\n启动后发送版本和时间通知；人员导入成功后，发送原始 Excel 文件附件（保留全部工作表及人员资料）。均在后台发送，失败不影响使用。")
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.host = QLineEdit()
        self.host.setPlaceholderText("填写发件邮箱服务商提供的 SMTP 服务器")
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(465)
        self.security = QComboBox()
        self.security.addItem("SSL/TLS", "ssl")
        self.security.addItem("STARTTLS", "starttls")
        self.security.currentIndexChanged.connect(lambda index: self.port.setValue(465 if index == 0 else 587))
        self.sender = QLineEdit()
        self.username = QLineEdit()
        self.username.setPlaceholderText("留空时使用发件邮箱")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText("邮箱 SMTP 授权码或专用密码")
        for caption, widget in (("SMTP 服务器", self.host), ("加密方式", self.security),
                                ("端口", self.port), ("发件邮箱", self.sender),
                                ("登录账号", self.username), ("授权码", self.password)):
            widget.setAccessibleName(caption)
            form.addRow(caption, widget)
        layout.addLayout(form)
        self.status = QLabel("保存后应用于下次启动及下次人员导入。授权码在本机加密保存；换电脑或 Windows 用户后需重新配置。")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        try:
            settings = load_settings(path)
            if settings:
                self.host.setText(settings.host)
                self.security.setCurrentIndex(self.security.findData(settings.security))
                self.port.setValue(settings.port)
                self.sender.setText(settings.sender)
                self.username.setText(settings.username)
                self.password.setText(settings.password)
        except ValueError as exc:
            self.status.setText(str(exc))

    def save(self):
        try:
            sender = self.sender.text().strip()
            settings = MailSettings(self.host.text().strip(), self.port.value(), self.security.currentData(),
                                    sender, self.username.text().strip() or sender, self.password.text())
            save_settings(self.path, settings)
        except ValueError as exc:
            QMessageBox.warning(self, "邮件设置未保存", str(exc))
            return
        except OSError:
            QMessageBox.warning(self, "邮件设置未保存", "无法写入配置，请检查软件目录的写入权限")
            return
        self.accept()
