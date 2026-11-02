"""Conservative matching by the complete stem and option texts."""
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, replace

from .models import BankImport, QuestionIssue


def normalize(text):
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", "", text).casefold()


def option_texts_are_unique(options):
    """Only distinct, non-empty option texts can be remapped after shuffling."""
    values = [normalize(value) for value in options.values()]
    return all(values) and len(values) == len(set(values))


def labeled_key(kind, stem, options):
    return kind, normalize(stem), tuple(sorted((label, normalize(text)) for label, text in options.items()))


def text_key(kind, stem, options):
    return kind, normalize(stem), tuple(sorted(normalize(text) for text in options.values()))


def _content_key(stem, options):
    """Question type is inferred from answers, so it cannot hide an answer conflict."""
    if not isinstance(stem, str) or not stem.strip() or not isinstance(options, dict) or not options:
        return None
    if any(not isinstance(label, str) or not isinstance(text, str) for label, text in options.items()):
        return None
    return normalize(stem), tuple(sorted((label, normalize(text)) for label, text in options.items()))


def _question_problems(question, *, require_option_text=False):
    problems = []
    if not isinstance(question.number, int) or isinstance(question.number, bool) or question.number < 1:
        problems.append("题号须为正整数")
    if question.kind not in {"单选", "多选", "判断"}:
        problems.append("题型无法识别")
    if not isinstance(question.stem, str) or not question.stem.strip():
        problems.append("题干为空")
    options = question.options
    if not isinstance(options, dict) or len(options) < 2:
        problems.append("题目至少需要两个选项")
        options = options if isinstance(options, dict) else {}
    if any(not isinstance(label, str) or not re.fullmatch(r"[A-Z]", label) for label in options):
        problems.append("选项编号应为单个大写字母")
    elif options and set(options) != {chr(65 + i) for i in range(len(options))}:
        problems.append("选项编号须从 A 开始连续排列")
    if any(not isinstance(text, str) for text in options.values()):
        problems.append("选项文字格式异常")
    elif require_option_text and any(not normalize(text) for text in options.values()):
        problems.append("选项文字为空")
    answers = question.answers
    if not isinstance(answers, (tuple, list)) or not answers:
        problems.append("缺少正确答案")
    elif any(not isinstance(answer, str) or not re.fullmatch(r"[A-Z]", answer) for answer in answers):
        problems.append("正确答案格式异常")
    elif len(set(answers)) != len(answers):
        problems.append("正确答案编号重复")
    elif not set(answers).issubset(options):
        problems.append("答案与选项不匹配")
    elif question.kind != "多选" and len(answers) != 1:
        problems.append("单选或判断题只能有一个正确答案")
    return problems


def issue_summary(issue):
    number = f"第 {issue.number} 题" if issue.number is not None else "未识别题目"
    pages = f"（页 {','.join(map(str, issue.pages))}）" if issue.pages else ""
    return f"{number}{pages}：{issue.reason}"


def _question_raw(question):
    values = question.options.items() if isinstance(question.options, dict) else ()
    options = "\n".join(f"{label}. {text}" for label, text in values)
    answers = question.answers if isinstance(question.answers, (tuple, list)) else (question.answers,)
    return f"{question.number}、{question.stem}\n{options}\n正确答案：{''.join(str(answer) for answer in answers)}"


def _question_issue(question, reason, raw):
    options = question.options if isinstance(question.options, dict) else {}
    answers = question.answers if isinstance(question.answers, (tuple, list)) else (question.answers,)
    return QuestionIssue(
        number=question.number if isinstance(question.number, int) and not isinstance(question.number, bool) else None,
        kind=str(question.kind or "未识别"), stem=str(question.stem or ""),
        options={str(label): str(text or "") for label, text in options.items()},
        answers=tuple(str(answer) for answer in answers if answer is not None),
        pages=tuple(question.pages or ()), reason=reason, raw=raw,
    )


def sanitize_bank(result, *, raw_blocks=None):
    """Keep usable questions and retain every excluded record for the anomaly viewer.

    Empty or wholly invalid imports are valid reports. Callers validate the usable
    question count when starting an exam, rather than losing the import report.
    """
    original_anomalies = list(result.anomalies)
    raw_blocks = raw_blocks or {}
    records = list(result.questions) + original_anomalies
    numbered = Counter(record.number for record in records if isinstance(record.number, int))
    grouped_answers = defaultdict(set)
    for record in records:
        key = _content_key(record.stem, record.options)
        if key is not None and isinstance(record.answers, (tuple, list)) and record.answers and all(isinstance(answer, str) for answer in record.answers):
            grouped_answers[key].add(tuple(sorted(record.answers)))
    conflicts = {key for key, answers in grouped_answers.items() if len(answers) > 1}

    def additional_problems(record):
        problems = []
        if isinstance(record.number, int) and numbered.get(record.number, 0) > 1:
            problems.append("题号重复，同号记录已全部跳过")
        if _content_key(record.stem, record.options) in conflicts:
            problems.append("相同题干和选项编号的正确答案冲突，相关题目已全部跳过")
        return problems

    anomalies = []
    for issue in original_anomalies:
        reasons = [issue.reason]
        reasons.extend(problem for problem in additional_problems(issue) if problem not in issue.reason)
        anomalies.append(replace(issue, reason="；".join(reasons)))
    questions = []
    for question in result.questions:
        problems = _question_problems(question, require_option_text=True) + additional_problems(question)
        if not problems:
            questions.append(question)
            continue
        anomalies.append(_question_issue(question, "；".join(dict.fromkeys(problems)),
                                        raw_blocks.get(id(question), _question_raw(question))))
    anomalies.sort(key=lambda issue: (issue.pages[0] if issue.pages else result.page_count + 1,
                                     issue.number if issue.number is not None else float("inf")))
    old_summaries = {issue_summary(issue) for issue in original_anomalies}
    summaries = [summary for summary in result.issues if summary not in old_summaries]
    summaries.extend(issue_summary(issue) for issue in anomalies)
    return BankImport(questions, list(dict.fromkeys(summaries)), result.page_count, anomalies)


@dataclass(frozen=True)
class Match:
    answers: tuple[str, ...]
    source_number: int


class KnowledgeIndex:
    def __init__(self, questions):
        self.entries = {}
        self.labeled_entries = {}
        for q in questions:
            problems = _question_problems(q)
            if problems:
                raise ValueError("题库存在异常题目：" + "；".join(problems))
            exact = labeled_key(q.kind, q.stem, q.options)
            match = Match(tuple(sorted(q.answers)), q.number)
            if exact in self.labeled_entries and self.labeled_entries[exact].answers != match.answers:
                raise ValueError("题库存在相同题干和选项编号但答案冲突的题目")
            self.labeled_entries[exact] = match
            key = text_key(q.kind, q.stem, q.options)
            correct = frozenset(normalize(q.options[label]) for label in q.answers)
            if not option_texts_are_unique(q.options) or (key in self.entries and (self.entries[key] is None or self.entries[key][0] != correct)):
                # Duplicate/empty options still work with the original labels. Never
                # infer which duplicate an answer refers to from its text alone.
                self.entries[key] = None
            else:
                self.entries[key] = (correct, q.number)
        if not self.labeled_entries:
            raise ValueError("请先导入有效题库")

    def can_relabel(self, kind, stem, options):
        """Whether this bank has one reliable answer mapping after option reordering."""
        key = text_key(kind, stem, options)
        return option_texts_are_unique(options) and key in self.entries and self.entries[key] is not None

    def match(self, kind, stem, options):
        exact = labeled_key(kind, stem, options)
        if exact in self.labeled_entries:
            return self.labeled_entries[exact]
        key = text_key(kind, stem, options)
        if key not in self.entries or self.entries[key] is None or not option_texts_are_unique(options):
            raise ValueError("题库没有完全匹配的题干和选项，已暂停；请人工核对")
        correct, number = self.entries[key]
        return Match(tuple(sorted(k for k, v in options.items() if normalize(v) in correct)), number)
