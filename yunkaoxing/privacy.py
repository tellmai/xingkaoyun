import re


def redact(message: str, secrets=()) -> str:
    message = str(message)
    for secret in secrets:
        if secret:
            message = message.replace(secret, "[已隐藏]")
    message = re.sub(r"(?<!\d)\d{17}[\dXx](?!\d)|(?<!\d)\d{15}(?!\d)", "[身份证号已隐藏]", message)
    # Never echo browser field values/call logs into the UI or persistent logs.
    return message.split("Call log:")[0].strip()
