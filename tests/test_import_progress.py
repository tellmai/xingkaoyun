from openpyxl import Workbook

from yunkaoxing.gui import ImportWorker
from yunkaoxing.importers import load_bank, load_identities


def write_people(path, count):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["所在的区县", "姓名", "身份证号"])
    for index in range(count):
        sheet.append(["黔江区", f"合成考生{index}", f"11010119900101{index:04d}"])
    workbook.save(path)
    workbook.close()


def test_identity_import_reports_row_totals_and_throttles_updates(tmp_path):
    path = tmp_path / "people.xlsx"
    write_people(path, 250)
    events = []
    result = load_identities(str(path), on_progress=lambda *event: events.append(event))
    assert len(result.candidates) == 250
    assert events[0] == (0, 0, "打开 Excel")
    assert (0, 250, "读取人员") in events
    assert events[-1] == (250, 250, "完成")
    assert len(events) < 150
    current = [done for done, total, stage in events if stage == "读取人员"]
    assert current == sorted(current)


def test_pdf_reading_finishes_before_parsing_and_checking_are_complete(monkeypatch):
    class Page:
        def __init__(self, number):
            self.number = number
        def extract_text(self):
            return f"{self.number}、合成题干{self.number}\nA. 甲\nB. 乙\n正确答案：A"
        def flush_cache(self):
            pass
    class Pdf:
        pages = [Page(n) for n in range(1, 21)]
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
    import pdfplumber
    monkeypatch.setattr(pdfplumber, "open", lambda _: Pdf())
    events = []
    result = load_bank("synthetic.pdf", on_progress=lambda *event: events.append(event))
    assert len(result.questions) == 20
    reading_done = events.index((20, 20, "读取 PDF"))
    assert any(stage == "解析题目" for _, _, stage in events[reading_done + 1:])
    assert events[-2] == (0, 1, "检查题目")
    assert events[-1] == (1, 1, "完成")
    assert sum(stage == "完成" for _, _, stage in events) == 1


def test_import_worker_forwards_structured_progress_for_identity_files(tmp_path):
    path = tmp_path / "people.xlsx"
    write_people(path, 5)
    worker = ImportWorker("identity", str(path))
    events, results = [], []
    worker.import_progress.connect(lambda *event: events.append(event))
    worker.result.connect(results.append)
    worker.run()
    assert results and len(results[0].candidates) == 5
    assert events[-1] == (5, 5, "完成")
