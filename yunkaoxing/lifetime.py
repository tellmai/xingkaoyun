"""Application start cutoff, evaluated in China Standard Time."""
from datetime import datetime, timedelta, timezone


CHINA_TZ = timezone(timedelta(hours=8))
EXPIRES_AT = datetime(2027, 7, 1, tzinfo=CHINA_TZ)
EXPIRY_MESSAGE = "软件使用期限已结束（北京时间 2027 年 7 月 1 日起停止使用）。"


def china_now():
    return datetime.now(CHINA_TZ)


def ensure_available(now=None):
    now = china_now() if now is None else now
    if now.tzinfo is None:
        raise ValueError("期限检查需要带时区的时间")
    if now >= EXPIRES_AT:
        raise ValueError(EXPIRY_MESSAGE)
