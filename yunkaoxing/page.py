"""Pure page parsing. No input is performed by this module."""
import re
from dataclasses import dataclass


class PageError(RuntimeError):
    pass


@dataclass(frozen=True)
class Node:
    key: str
    kind: str
    text: str
    value: str = ""
    selected: bool | None = None
    enabled: bool = True


@dataclass(frozen=True)
class Snapshot:
    nodes: tuple[Node, ...]
    token: int = 0

    @property
    def text(self):
        return "\n".join(n.text for n in self.nodes if n.text)

    def find(self, names, kinds=()):
        matches = [n for n in self.nodes if n.enabled and n.text.strip() in names and (not kinds or n.kind in kinds)]
        if len(matches) != 1:
            raise PageError("页面控件未找到或不唯一：" + " / ".join(names))
        return matches[0]


@dataclass(frozen=True)
class VisibleQuestion:
    number: int
    total: int
    kind: str
    stem: str
    options: dict[str, str]
    controls: dict[str, Node]

    @property
    def signature(self):
        return (self.number, self.kind, self.stem, tuple(sorted(self.options.items())))


def parse_question(snapshot):
    """Accept one fully exposed question; reject ambiguous page layouts."""
    progress = set()
    kinds = set()
    for node in snapshot.nodes:
        if node.kind != "Text":
            continue
        value = node.text.strip()
        match = re.fullmatch(r"(?:第\s*)?(\d+)\s*(?:/|／|of)\s*(\d+)\s*(?:题)?", value, flags=re.I)
        if not match:
            match = re.fullmatch(r"第\s*(\d+)\s*题\s*[，,]?\s*共\s*(\d+)\s*题", value)
        if match and 1 <= int(match[1]) <= int(match[2]):
            progress.add((int(match[1]), int(match[2])))
        kind_match = re.fullmatch(r"[【\[（(]?\s*(单选|多选|判断)题?\s*[】\]）)]?", value)
        if kind_match:
            kinds.add(kind_match[1])
    if len(progress) != 1:
        raise PageError("无法唯一读取当前题号和总题数，请检查页面适配")
    number, total = progress.pop()
    if len(kinds) != 1:
        raise PageError("无法唯一识别当前题型")
    kind = kinds.pop()
    options, controls, positions = {}, {}, []
    for i, node in enumerate(snapshot.nodes):
        if node.kind not in {"RadioButton", "CheckBox", "Button", "ListItem"}:
            continue
        m = re.fullmatch(r"\s*([A-Z])\s*[.．、:：)）]?\s+(.+?)\s*", node.text, flags=re.S)
        if not m:
            m = re.fullmatch(r"\s*([A-Z])[.．、:：)）]\s*(.+?)\s*", node.text, flags=re.S)
        if m:
            label, content = m.groups()
        elif kind == "判断" and node.text.strip() in {"正确", "错误", "对", "错"}:
            label = "A" if node.text.strip() in {"正确", "对"} else "B"
            content = "正确" if label == "A" else "错误"
        else:
            continue
        if label in options:
            raise PageError("检测到重复选项控件，无法确认当前试题")
        options[label], controls[label] = content, node
        positions.append(i)
    if len(options) < 2 or set(options) != set(chr(65 + i) for i in range(len(options))):
        raise PageError("选项不完整，或小程序尚未暴露可读的选项控件")
    # Text nodes before the first option, after the most recent question type/progress.
    prefix = snapshot.nodes[:min(positions)]
    boundary = -1
    stem_lines = []
    for i, node in enumerate(prefix):
        value = node.text.strip()
        if re.fullmatch(r"(?:第\s*)?\d+\s*[/／]\s*\d+\s*题?", value) or re.fullmatch(r"[【\[（(]?" + kind + r"题?[】\]）)]?", value):
            boundary = i
    for node in prefix[boundary + 1:]:
        if node.kind != "Text" or not node.text.strip():
            continue
        value = node.text.strip()
        if re.fullmatch(r"(?:剩余时间|倒计时|用时)?\s*\d{1,3}:\d{2}(?::\d{2})?", value):
            continue
        if value not in stem_lines:
            stem_lines.append(value)
    stem = "\n".join(stem_lines)
    if boundary < 0 or not stem.strip():
        raise PageError("无法可靠分离题干，请检查小程序页面适配")
    return VisibleQuestion(number, total, kind, stem, options, controls)


def parse_score(snapshot, markers):
    if not any(m in snapshot.text for m in markers):
        return None
    values = re.findall(r"(?:考试成绩|考试得分|最终得分|总得分|得分|成绩)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(?:分)?", snapshot.text)
    scores = set(float(v) for v in values)
    if len(scores) != 1:
        raise PageError("成绩页已出现，但无法唯一读取服务端成绩；结果待核对")
    score = scores.pop()
    if not 0 <= score <= 1000:
        raise PageError("成绩数值超出可识别范围")
    return score
