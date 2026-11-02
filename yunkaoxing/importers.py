import re
from io import BytesIO
from pathlib import Path
from typing import Callable

from .models import BankImport, Candidate, IdentityImport, Question, QuestionIssue
from .knowledge import issue_summary, sanitize_bank


def load_identities(path: str, progress: Callable[[str], None] = lambda _: None,
                    on_progress: Callable[[int, int, str], None] | None = None,
                    *, content: bytes | None = None) -> IdentityImport:
    from openpyxl import load_workbook

    if Path(path).suffix.lower() != ".xlsx":
        raise ValueError("请选择 .xlsx 身份文件")
    report = on_progress or (lambda current, total, stage: None)
    report(0, 0, "打开 Excel")
    progress("正在打开身份信息 Excel…")
    wb = load_workbook(BytesIO(content) if content is not None else path, read_only=True, data_only=True)
    required = ("所在的区县", "姓名", "身份证号")
    try:
        for sheet in wb:
            rows = sheet.iter_rows()
            header = next(rows, ())
            names = [str(c.value or "").strip() for c in header]
            if not all(n in names for n in required):
                continue
            indices = [names.index(n) for n in required]
            candidates, issues, rejected = [], [], []
            total = max(0, (sheet.max_row or 1) - 1)
            step = max(1, total // 100)
            report(0, total, "读取人员")
            for row_number, cells in enumerate(rows, 2):
                processed = row_number - 2
                if processed and processed % step == 0:
                    report(processed, total, "读取人员")
                chosen = [cells[i].value if i < len(cells) else None for i in indices]
                if all(v is None or str(v).strip() == "" for v in chosen):
                    continue
                district, name, raw_id = chosen
                def reject(reason):
                    issues.append(f"第 {row_number} 行：{reason}")
                    values = ["" if v is None else str(v).strip() for v in chosen]
                    rejected.append((Candidate(row_number, *values), reason))
                if any(v is None or not str(v).strip() for v in chosen):
                    reject("区县、姓名、身份证号均为必填")
                    continue
                if not isinstance(raw_id, str):
                    # Excel keeps only 15 significant decimal digits. Never guess lost ID digits.
                    if isinstance(raw_id, bool) or not isinstance(raw_id, (int, float)) or raw_id != int(raw_id) or len(str(int(raw_id))) != 15:
                        reject("身份证号为数值或格式异常，请从原始资料重新录入为文本；无法恢复已丢失的精度")
                        continue
                    raw_id = str(int(raw_id))
                id_number = raw_id.strip().upper()
                if not re.fullmatch(r"(?:[0-9]{15}|[0-9]{17}[0-9X])", id_number):
                    reject("身份证号应为 15 位或 18 位，末位可为 X")
                    continue
                candidates.append(Candidate(row_number, str(district).strip(), str(name).strip(), id_number))
            report(total, total, "完成")
            progress(f"身份读取完成：有效 {len(candidates)} 人，无效 {len(rejected)} 行")
            return IdentityImport(candidates, issues, sheet.title, rejected)
        raise ValueError("找不到包含「所在的区县、姓名、身份证号」三列表头的工作表")
    finally:
        wb.close()


def parse_question_lines(lines: list[tuple[int, str]], page_count: int,
                         on_progress: Callable[[int, int, str], None] | None = None) -> BankImport:
    questions, issues, anomalies = [], [], []
    current: list[tuple[int, str]] = []
    current_raw: list[tuple[int, str]] = []
    raw_blocks = {}
    number = None

    def finish() -> None:
        if number is None:
            return
        pages = tuple(sorted({p for p, _ in current}))
        stem, options, answers = [], {}, ()
        active_option = None
        problems = []
        ended = False
        for _, line in current:
            answer_match = re.fullmatch(r"正确答案\s*[：:]\s*([A-Z]+)\s*", line)
            if answer_match:
                if answers:
                    problems.append("出现多个答案标记")
                answers = tuple(answer_match[1])
                ended = True
                continue
            if ended:
                if line:
                    problems.append("答案后存在未识别内容")
                continue
            option_match = re.match(r"^([A-Z])[.．、]\s*(.*)", line)
            if option_match:
                active_option = option_match[1]
                if active_option in options:
                    problems.append("选项编号重复")
                options[active_option] = option_match[2]
            elif active_option:
                options[active_option] += "\n" + line
            else:
                stem.append(line)
        if not answers:
            problems.append("缺少正确答案")
        if not options or not set(answers).issubset(options):
            problems.append("答案与选项不匹配")
        if not any(stem):
            problems.append("题干为空")
        values = [re.sub(r"\s+", "", v) for v in options.values()]
        kind = "判断" if values == ["正确", "错误"] else "多选" if len(answers) > 1 else "单选"
        raw = "\n".join(line for _, line in current_raw)
        if problems:
            anomaly = QuestionIssue(number, kind, "\n".join(stem), options, answers, pages,
                                    "；".join(dict.fromkeys(problems)), raw)
            anomalies.append(anomaly)
            issues.append(issue_summary(anomaly))
            return
        question = Question(number, kind, "\n".join(stem), options, answers, pages)
        questions.append(question)
        raw_blocks[id(question)] = raw

    report = on_progress or (lambda current, total, stage: None)
    total_lines = len(lines)
    step = max(1, total_lines // 100)
    report(0, total_lines, "解析题目")
    for line_index, (page, raw_line) in enumerate(lines):
        if line_index and line_index % step == 0:
            report(line_index, total_lines, "解析题目")
        line = raw_line.strip()
        if not line or re.fullmatch(r"\d{4}\s*年度法治理论知识考试复习资料", line):
            continue
        start = re.match(r"^(\d+)[、．.]\s*(.*)", line)
        if start:
            finish()
            number = int(start[1])
            current = [(page, start[2])]
            current_raw = [(page, raw_line)]
        elif number is not None:
            current.append((page, line))
            current_raw.append((page, raw_line))
    finish()
    report(total_lines, total_lines, "解析题目")
    if not questions and not issues:
        reason = "未发现可识别题目；本版需要包含文字、题号、选项及「正确答案」的 PDF"
        anomaly = QuestionIssue(None, "未识别", "", {}, (),
                                tuple(sorted({page for page, raw in lines if raw.strip()})), reason,
                                "\n".join(raw for _, raw in lines))
        anomalies.append(anomaly)
        issues.append(issue_summary(anomaly))
    report(0, 1, "检查题目")
    result = sanitize_bank(BankImport(questions, issues, page_count, anomalies), raw_blocks=raw_blocks)
    report(1, 1, "完成")
    return result


def load_bank(path: str, progress: Callable[[str], None] = lambda _: None,
              on_progress: Callable[[int, int, str], None] | None = None) -> BankImport:
    import pdfplumber

    if Path(path).suffix.lower() != ".pdf":
        raise ValueError("请选择 PDF 题库")
    report = on_progress or (lambda current, total, stage: None)
    report(0, 0, "打开 PDF")
    lines = []
    with pdfplumber.open(path) as pdf:
        count = len(pdf.pages)
        report(0, count, "读取 PDF")
        step = max(1, count // 100)
        for i, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            page_lines = text.splitlines()
            if page_lines and page_lines[0].strip() == str(i):
                page_lines.pop(0)
            if page_lines and page_lines[-1].strip() == str(i):
                page_lines.pop()
            lines.extend((i, line) for line in page_lines)
            if i % 20 == 0 or i == count:
                progress(f"正在解析题库：{i}/{count} 页")
            if i == 1 or i % step == 0 or i == count:
                report(i, count, "读取 PDF")
            page.flush_cache()
    return parse_question_lines(lines, count, on_progress=report)
