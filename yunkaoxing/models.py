from dataclasses import dataclass, field
import math


@dataclass(frozen=True)
class Candidate:
    row: int
    district: str
    name: str = field(repr=False)
    id_number: str = field(repr=False)

    @property
    def masked_id(self):
        return self.id_number[:3] + "*" * max(0, len(self.id_number) - 7) + self.id_number[-4:]


@dataclass(frozen=True)
class Question:
    number: int
    kind: str
    stem: str
    options: dict[str, str]
    answers: tuple[str, ...]
    pages: tuple[int, ...] = ()


@dataclass
class IdentityImport:
    candidates: list[Candidate]
    issues: list[str]
    sheet: str
    rejected: list[tuple[Candidate, str]] = field(default_factory=list)
    source_name: str = ""
    source_bytes: bytes | None = field(default=None, repr=False)


@dataclass
class QuestionIssue:
    number: int | None = None
    kind: str = ""
    stem: str = ""
    options: dict[str, str] = field(default_factory=dict)
    answers: tuple[str, ...] = ()
    pages: tuple[int, ...] = ()
    reason: str = ""
    raw: str = ""


@dataclass
class BankImport:
    questions: list[Question]
    issues: list[str]
    page_count: int
    anomalies: list[QuestionIssue] = field(default_factory=list)


@dataclass(frozen=True)
class RunOptions:
    mode: str = "login"
    exam_title: str = ""
    minimum_seconds: float = 600
    auto_submit: bool = False
    timeout_seconds: int = 30
    max_questions: int = 300
    score_min: int = 98
    score_max: int = 100
    simulate_scores: bool = False
    maximum_seconds: float | None = None
    fastest: bool = False
    minimum_score: int = 90
    background: bool = False
    concurrency: int = 1

    def validate(self):
        if isinstance(self.concurrency, bool) or not isinstance(self.concurrency, int) or not 1 <= self.concurrency <= 30:
            raise ValueError("并发数量须为 1–30 的整数")
        if self.mode not in {"login", "exam", "practice"}:
            raise ValueError("请选择登录验证、小程序考试或本地模拟")
        if self.mode == "exam" and not self.exam_title.strip():
            raise ValueError("请输入考试的完整名称，用于核对目标考试")
        if not 5 <= self.timeout_seconds <= 120 or not 1 <= self.max_questions <= 1000:
            raise ValueError("超时或题目数量上限不正确")
        if self.mode != "login":
            maximum = self.minimum_seconds if self.maximum_seconds is None else self.maximum_seconds
            if not math.isfinite(self.minimum_seconds) or not math.isfinite(maximum) or (not self.fastest and not 0 <= self.minimum_seconds <= maximum <= 10800):
                raise ValueError("答题用时须为 0–180 分钟，最短用时不能大于最长用时")
            if not 0 <= self.score_min <= self.score_max <= 100:
                raise ValueError("得分范围须为 0–100 分")
            if not 0 <= self.minimum_score <= 100 or max(self.minimum_score, self.score_min) > self.score_max:
                raise ValueError("最低分和得分范围没有有效交集")
