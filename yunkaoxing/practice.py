"""Offline practice is scored independently; score range applies only here."""
import random
import time

from .knowledge import KnowledgeIndex


def make_paper(questions, rng=None):
    rng = rng or random.SystemRandom()
    KnowledgeIndex(questions)
    if len(questions) < 100 or len({q.number for q in questions}) != len(questions):
        raise ValueError("本地模拟需要至少 100 道题号不重复的有效题目")
    selected = []
    for kind, count in (("单选", 34), ("多选", 33), ("判断", 33)):
        pool = [q for q in questions if q.kind == kind]
        if not pool:
            raise ValueError("本地模拟需要单选、多选和判断三种题型")
        selected.extend(rng.sample(pool, min(count, len(pool))))
    used = {q.number for q in selected}
    selected += rng.sample([q for q in questions if q.number not in used], 100 - len(selected))
    rng.shuffle(selected)
    return selected


def grade(paper, answers):
    if set(answers) != set(range(len(paper))):
        raise ValueError("本地试卷答案不完整")
    for i, q in enumerate(paper):
        if not set(answers[i]) <= q.options.keys():
            raise ValueError("本地试卷包含无效选项")
    return sum(set(answers[i]) == set(q.answers) for i, q in enumerate(paper))


def run_practice(questions, options, control, report=lambda _: None, rng=None):
    rng = rng or random.SystemRandom()
    started = time.monotonic()
    paper = make_paper(questions, rng)
    index = KnowledgeIndex(questions)
    target = rng.randint(max(options.minimum_score, options.score_min), options.score_max)
    wrong = set(rng.sample(range(100), 100 - target))
    answers = {}
    for i, q in enumerate(paper):
        control.check()
        answers[i] = index.match(q.kind, q.stem, q.options).answers
        if i in wrong:
            answers[i] = next((tuple([label]) for label in q.options if {label} != set(q.answers)), ())
        if (i + 1) % 10 == 0:
            report(f"本地模拟已作答 {i + 1}/100 题")
    first = grade(paper, answers)
    if first != target or grade(paper, dict(answers)) != first:
        raise ValueError("本地模拟评分校验失败")
    remaining = (0 if options.fastest else options.minimum_seconds) - (time.monotonic() - started)
    if remaining > 0:
        report(f"本地模拟复核完成，等待最短用时 {remaining / 60:.1f} 分钟")
        control.sleep(remaining)
    return {"score": first, "total": 100, "elapsed_seconds": round(time.monotonic() - started, 2),
            "reason": "本地模拟，双次独立评分校验；非小程序考试成绩"}
