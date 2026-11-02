import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from .models import Candidate
from .privacy import redact


LABELS = {"pending": "未开始", "running": "进行中", "waiting": "等待人工核对",
          "completed": "已完成", "failed": "失败", "stopped": "已停止 / 结果待核对"}


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass
class Record:
    candidate: Candidate
    status: str = "pending"
    stage: str = ""
    reason: str = ""
    started: str = ""
    ended: str = ""
    score: float | None = None
    total: int | None = None
    elapsed_seconds: float | None = None


class StoreError(RuntimeError):
    pass


class BatchStore:
    def __init__(self, root, candidates, rejected=(), mode="login", options=None):
        self.folder = Path(root).resolve() / f"云考星批次_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}"
        self.mode = mode
        self.options = asdict(options) if options else {}
        self.records = [Record(c) for c in candidates] + [Record(c, "failed", reason=r, ended=now()) for c, r in rejected]
        self.records.sort(key=lambda r: r.candidate.row)

    def update(self, index, **changes):
        record = self.records[index]
        for key, value in changes.items():
            if key not in Record.__dataclass_fields__ or key == "candidate":
                raise ValueError("无效的状态字段")
            if key == "reason":
                value = redact(value, (record.candidate.name, record.candidate.id_number))
            setattr(record, key, value)
        self.persist()
        return record

    def persist(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "mode": self.mode, "options": self.options,
                   "updated": now(), "records": [asdict(r) for r in self.records]}
        try:
            target = self.folder / "人员状态.json"
            temp = target.with_suffix(".json.tmp")
            with temp.open("w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, target)
            self.export()
        except (OSError, ValueError) as exc:
            raise StoreError("结果写入失败；请关闭结果 Excel、检查磁盘及目录权限，批次已停止") from exc

    def export(self):
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
        for done, filename in ((True, "已完成信息统计.xlsx"), (False, "未完成信息统计.xlsx")):
            wb = Workbook()
            ws = wb.active
            ws.title = "云考星统计"
            ws.append(["所在的区县", "姓名", "身份证号", "源文件行号", "状态", "阶段", "说明", "开始时间", "结束时间", "得分", "总题数", "实际用时（分钟）", "模式"])
            for r in self.records:
                if (r.status == "completed") != done:
                    continue
                c = r.candidate
                values = [c.district, c.name, c.id_number, c.row, LABELS[r.status], r.stage, r.reason,
                          r.started, r.ended, r.score, r.total,
                          round(r.elapsed_seconds / 60, 3) if r.elapsed_seconds is not None else None, self.mode]
                ws.append(values)
                for cell, value in zip(ws[ws.max_row], values):
                    if isinstance(value, str):
                        cell.data_type = "s"
                        cell.number_format = "@"
            for cell in ws[1]:
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = PatternFill("solid", fgColor="157D72")
            for letter, width in zip("ABCDEFGHIJKLM", (18, 18, 26, 14, 28, 18, 70, 30, 30, 12, 12, 22, 16)):
                ws.column_dimensions[letter].width = width
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
            target = self.folder / filename
            temp = target.with_suffix(".tmp.xlsx")
            try:
                wb.save(temp)
                os.replace(temp, target)
            finally:
                wb.close()


def recover_interrupted(folder):
    """Never replay live submissions. Recover ambiguous records as stopped, for manual review."""
    data = json.loads((Path(folder) / "人员状态.json").read_text(encoding="utf-8"))
    candidates, rejected = [], []
    for record in data["records"]:
        if record["status"] == "completed":
            continue
        candidate = Candidate(**record["candidate"])
        candidates.append(candidate)
    return candidates
