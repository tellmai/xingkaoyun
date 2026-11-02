"""One background startup notification, using locally configured TLS SMTP."""
import base64
import json
import re
import smtplib
import ssl
import threading
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path
from queue import SimpleQueue

from . import APP_NAME, __version__
from .lifetime import CHINA_TZ, china_now


RECIPIENT = "1564532736@qq.com"


@dataclass(frozen=True)
class MailSettings:
    host: str
    port: int
    security: str
    sender: str
    username: str
    password: str = field(repr=False)

    def validate(self):
        if not isinstance(self.host, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9]|[A-Za-z0-9]", self.host):
            raise ValueError("请填写 SMTP 服务器域名，不含网址前缀或端口")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("SMTP 端口应为 1–65535")
        if self.security not in {"ssl", "starttls"}:
            raise ValueError("请选择 SSL/TLS 或 STARTTLS 加密连接")
        if not isinstance(self.sender, str) or not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}", self.sender):
            raise ValueError("请填写完整的发件邮箱地址")
        if not isinstance(self.username, str) or not self.username.strip() or any(c in self.username for c in "\r\n\x00"):
            raise ValueError("请填写 SMTP 登录账号")
        if not isinstance(self.password, str) or not self.password:
            raise ValueError("请填写邮箱 SMTP 授权码或专用密码")


def protect_secret(value):
    import win32crypt
    # Current Windows user only; no machine-wide or interactive protection.
    blob = win32crypt.CryptProtectData(value.encode("utf-8"), "YunKaoXing SMTP", None, None, None, 1)
    return base64.b64encode(blob).decode("ascii")


def unprotect_secret(value):
    import win32crypt
    blob = base64.b64decode(value, validate=True)
    return win32crypt.CryptUnprotectData(blob, None, None, None, 1)[1].decode("utf-8")


def save_settings(path, settings):
    settings.validate()
    try:
        protected = protect_secret(settings.password)
    except Exception:
        raise ValueError("无法加密保存授权码，请检查当前 Windows 用户环境") from None
    document = {"schema": 1, "host": settings.host, "port": settings.port,
                "security": settings.security, "sender": settings.sender,
                "username": settings.username, "credential_protected": protected}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def load_settings(path):
    path = Path(path)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        raise ValueError("启动邮件配置无法读取，请重新保存邮件设置") from None
    try:
        if document["schema"] != 1:
            raise ValueError()
        settings = MailSettings(document["host"], document["port"], document["security"],
                                document["sender"], document["username"],
                                unprotect_secret(document["credential_protected"]))
        settings.validate()
        return settings
    except Exception:
        raise ValueError("启动邮件配置无效或授权码无法解密；更换电脑或 Windows 用户后请重新配置") from None


def build_message(settings, started_at):
    message = EmailMessage()
    message["Subject"] = f"{APP_NAME}启动通知"
    message["From"] = settings.sender
    message["To"] = RECIPIENT
    message["Date"] = format_datetime(started_at.astimezone(CHINA_TZ))
    message.set_content(f"软件版本：{APP_NAME} v{__version__}\n启动时间：{started_at.astimezone(CHINA_TZ):%Y-%m-%d %H:%M:%S}（北京时间）\n")
    return message


def send_message(settings, message):
    settings.validate()
    context = ssl.create_default_context()
    # Fixed EHLO name avoids disclosing the computer's hostname.
    kwargs = {"timeout": 8, "local_hostname": "localhost"}
    if settings.security == "ssl":
        connection = smtplib.SMTP_SSL(settings.host, settings.port, context=context, **kwargs)
    else:
        connection = smtplib.SMTP(settings.host, settings.port, **kwargs)
    with connection as smtp:
        if settings.security == "starttls":
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
        smtp.login(settings.username, settings.password)
        refused = smtp.send_message(message,
                                    from_addr=settings.sender, to_addrs=[RECIPIENT])
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)


def send_notification(settings, started_at):
    send_message(settings, build_message(settings, started_at))


def build_identity_message(settings, name, content, imported_at):
    name = Path(name).name
    if not name.lower().endswith(".xlsx") or any(c in name for c in "\r\n\x00"):
        raise ValueError("人员文件附件须为 Excel 文件")
    if not isinstance(content, bytes):
        raise ValueError("人员文件附件内容无效")
    message = EmailMessage()
    message["Subject"] = f"{APP_NAME}人员导入文件"
    message["From"], message["To"] = settings.sender, RECIPIENT
    message["Date"] = format_datetime(imported_at.astimezone(CHINA_TZ))
    message.set_content(f"软件版本：{APP_NAME} v{__version__}\n导入时间：{imported_at.astimezone(CHINA_TZ):%Y-%m-%d %H:%M:%S}（北京时间）\n附件为本次导入的原始人员信息文件。\n")
    message.add_attachment(content, maintype="application",
                           subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename=name)
    return message


def send_identity_file(settings, name, content, imported_at):
    send_message(settings, build_identity_message(settings, name, content, imported_at))


def failure_message(exc):
    # Server responses can contain addresses or credentials; never log them.
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return "启动邮件发送失败：邮箱认证失败，请检查 SMTP 账号和授权码"
    if isinstance(exc, ssl.SSLError):
        return "启动邮件发送失败：TLS 连接或证书校验失败"
    if isinstance(exc, (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused)):
        return "启动邮件发送失败：邮件服务器拒绝发件人或收件人"
    if isinstance(exc, (TimeoutError, OSError)) and not isinstance(exc, smtplib.SMTPException):
        return "启动邮件发送失败：无法连接邮件服务器或连接超时"
    if isinstance(exc, ValueError):
        return "启动邮件配置无效或无法解密，请重新配置"
    return "启动邮件发送失败：邮件服务器未确认接收，请检查 SMTP 设置"


class StartupMailer:
    """No Qt dependency; only the UI thread consumes the result queue."""
    def __init__(self, path):
        self.path = Path(path)
        self.results = SimpleQueue()
        self.thread = None
        self._started = False

    def start(self, *, started_at=None):
        if self._started:
            return False
        self._started = True
        started_at = started_at or china_now()
        self.thread = threading.Thread(target=self._run, args=(started_at,),
                                       name="startup-mail", daemon=True)
        self.thread.start()
        return True

    def _run(self, started_at):
        try:
            settings = load_settings(self.path)
            if settings is None:
                self.results.put("启动邮件未发送：请在“连接与保存 → 邮件设置”中配置发件邮箱")
                return
            send_notification(settings, started_at)
            self.results.put(f"启动邮件已提交邮件服务器，收件人：{RECIPIENT}")
        except Exception as exc:
            self.results.put(failure_message(exc))


class IdentityFileMailer(StartupMailer):
    def __init__(self, path, name, content):
        super().__init__(path)
        self.name, self.content = name, content

    def _run(self, imported_at):
        try:
            settings = load_settings(self.path)
            if settings is None:
                self.results.put("人员文件邮件未发送：请先在“连接与保存 → 邮件设置”中配置发件邮箱，然后重新导入")
                return
            send_identity_file(settings, self.name, self.content, imported_at)
            self.results.put(f"人员文件邮件已提交邮件服务器，收件人：{RECIPIENT}")
        except Exception as exc:
            self.results.put(failure_message(exc).replace("启动邮件", "人员文件邮件"))
        finally:
            self.content = None
