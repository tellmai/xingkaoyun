from collections import Counter
from dataclasses import replace
import json
import threading
import time

import pytest

import yunkaoxing.worker as worker_module
from yunkaoxing.engine import LoginError
from yunkaoxing.models import RunOptions
from yunkaoxing.page import Snapshot
from yunkaoxing.profile import SiteProfile
from yunkaoxing.store import BatchStore, StoreError
from yunkaoxing.worker import BatchWorker


class SlotPort:
    def __init__(self, slot, ports):
        self.slot, self.owner, self.closed = slot, threading.get_ident(), False
        self.rows = []
        ports.append(self)

    def connect(self):
        assert self.owner == threading.get_ident()

    def snapshot(self):
        assert self.owner == threading.get_ident()
        return Snapshot(())

    def close(self):
        assert self.owner == threading.get_ident()
        self.closed = True


def batch(tmp_path, candidate, bank, concurrency=3, count=7, **changes):
    people = [replace(candidate, row=i + 2, name=f"合成考生{i + 1}", id_number=f"11010119900101{i + 10:04d}") for i in range(count)]
    ports = []
    options = RunOptions(mode="exam", exam_title="并发合成考试", fastest=True, background=True,
                         simulate_scores=True, concurrency=concurrency, **changes)
    worker = BatchWorker(SiteProfile(), options, people, [], bank, str(tmp_path),
                         slot_port_factory=lambda slot, before: SlotPort(slot, ports))
    return worker, ports


def test_concurrency_is_bounded_and_each_slot_keeps_its_own_port(tmp_path, candidate, bank, monkeypatch):
    worker, ports = batch(tmp_path, candidate, bank, count=7)
    lock, barrier = threading.Lock(), threading.Barrier(3)
    activity = {"active": 0, "peak": 0}
    processed = []
    class Session:
        def __init__(self, port, profile, options, control, report, wait_operator):
            self.port, self.options = port, options
        def login(self, person):
            assert self.port.owner == threading.get_ident()
            first_on_slot = not self.port.rows
            self.port.rows.append(person.row)
            with lock:
                activity["active"] += 1
                activity["peak"] = max(activity["peak"], activity["active"])
                processed.append(person.row)
            if first_on_slot:
                barrier.wait(5)
        def open_exam(self, person):
            pass
        def answer_exam(self, index):
            time.sleep(.025)
            with lock:
                activity["active"] -= 1
            return {"score": self.options.score_min, "total": 100}
    monkeypatch.setattr(worker_module, "MiniProgramSession", Session)
    worker.run()
    assert activity == {"active": 0, "peak": 3}
    assert sorted(processed) == list(range(2, 9))
    assert len(ports) == 3 and len({port.owner for port in ports}) == 3
    assert all(port.closed and port.rows for port in ports)
    assert all(record.status == "completed" for record in worker.store.records)
    counts = Counter(record.score for record in worker.store.records)
    assert max(counts.values()) - min(counts.values()) <= 1
    assert not worker._score_reserved or not any(worker._score_reserved.values())


def test_login_failure_frees_its_slot_and_other_people_still_finish(tmp_path, candidate, bank, monkeypatch):
    worker, ports = batch(tmp_path, candidate, bank, concurrency=2, count=5)
    processed, recovered = [], []
    class Session:
        def __init__(self, port, profile, options, control, report, wait_operator):
            self.port, self.options = port, options
        def login(self, person):
            self.port.rows.append(person.row)
            processed.append(person.row)
            if person.row == 2:
                raise LoginError("考区未报名")
        def recover_login(self):
            recovered.append(self.port.slot)
        def open_exam(self, person):
            pass
        def answer_exam(self, index):
            time.sleep(.015)
            return {"score": self.options.score_min, "total": 100}
    monkeypatch.setattr(worker_module, "MiniProgramSession", Session)
    worker.run()
    assert sorted(processed) == [2, 3, 4, 5, 6]
    assert recovered and len(ports) == 2
    assert worker.store.records[0].status == "failed"
    assert "未报名" in worker.store.records[0].reason
    assert all(record.status == "completed" for record in worker.store.records[1:])


@pytest.mark.parametrize("immediate", [False, True])
def test_stop_finishes_or_interrupts_only_the_already_active_people(tmp_path, candidate, bank, monkeypatch, immediate):
    worker, _ = batch(tmp_path, candidate, bank, concurrency=2, count=6)
    event = worker.control.stop if immediate else worker.control.after_current
    barrier = threading.Barrier(2, action=event.set)
    processed = []
    class Session:
        def __init__(self, port, profile, options, control, report, wait_operator):
            self.options, self.control = options, control
        def login(self, person):
            processed.append(person.row)
            barrier.wait(5)
            self.control.check()
        def open_exam(self, person):
            pass
        def answer_exam(self, index):
            return {"score": self.options.score_min, "total": 100}
    monkeypatch.setattr(worker_module, "MiniProgramSession", Session)
    worker.run()
    assert sorted(processed) == [2, 3]
    assert [record.status for record in worker.store.records[:2]] == (["stopped"] * 2 if immediate else ["completed"] * 2)
    assert all(record.status == "pending" for record in worker.store.records[2:])


def test_store_updates_do_not_overlap_between_sessions(tmp_path, candidate, bank, monkeypatch):
    worker, _ = batch(tmp_path, candidate, bank, count=9)
    guard = threading.Lock()
    state = {"inside": 0, "overlap": False}
    class CheckedStore(BatchStore):
        def persist(self):
            with guard:
                state["inside"] += 1
                if state["inside"] > 1:
                    state["overlap"] = True
            try:
                time.sleep(.001)
                return super().persist()
            finally:
                with guard:
                    state["inside"] -= 1
    class Session:
        def __init__(self, port, profile, options, control, report, wait_operator):
            self.options = options
        def login(self, person):
            time.sleep(.01)
        def open_exam(self, person):
            pass
        def answer_exam(self, index):
            return {"score": self.options.score_min, "total": 100}
    monkeypatch.setattr(worker_module, "BatchStore", CheckedStore)
    monkeypatch.setattr(worker_module, "MiniProgramSession", Session)
    worker.run()
    assert not state["overlap"] and state["inside"] == 0
    saved = json.loads((worker.store.folder / "人员状态.json").read_text(encoding="utf-8"))
    assert len(saved["records"]) == 9 and all(r["status"] == "completed" for r in saved["records"])


def test_storage_failure_stops_all_active_sessions_and_leaves_no_running_records(tmp_path, candidate, bank, monkeypatch):
    worker, _ = batch(tmp_path, candidate, bank, count=7)
    barrier, processed = threading.Barrier(3), []
    class FailingStore(BatchStore):
        failed = False
        def export(self):
            if not self.failed and any(record.status == "completed" for record in self.records):
                self.failed = True
                raise StoreError("磁盘写入失败")
            return super().export()
    class Session:
        def __init__(self, port, profile, options, control, report, wait_operator):
            self.options = options
        def login(self, person):
            processed.append(person.row)
            barrier.wait(5)
        def open_exam(self, person):
            pass
        def answer_exam(self, index):
            return {"score": self.options.score_min, "total": 100}
    monkeypatch.setattr(worker_module, "BatchStore", FailingStore)
    monkeypatch.setattr(worker_module, "MiniProgramSession", Session)
    worker.run()
    assert sorted(processed) == [2, 3, 4]
    assert isinstance(worker._fatal_error, StoreError) and worker.control.stop.is_set()
    assert all(record.status not in {"running", "waiting", "completed"} for record in worker.store.records)
    assert all(record.status == "pending" for record in worker.store.records[3:])


def test_real_wechat_and_shared_simulator_port_cannot_run_concurrently(tmp_path, candidate, bank):
    worker, _ = batch(tmp_path, candidate, bank)
    worker.slot_port_factory = None
    worker.port_factory = None
    worker.run()
    assert worker.store is None
    worker.port_factory = lambda _: pytest.fail("shared legacy port must not connect")
    worker.run()
    assert worker.store is None


def test_one_resume_releases_current_operator_waiters_and_keeps_pending_people_pending(tmp_path, candidate, bank, monkeypatch):
    worker, _ = batch(tmp_path, candidate, bank, concurrency=2, count=5)
    barrier = threading.Barrier(2)
    class Session:
        def __init__(self, port, profile, options, control, report, wait_operator):
            self.options, self.wait_operator = options, wait_operator
        def login(self, person):
            pass
        def open_exam(self, person):
            pass
        def answer_exam(self, index):
            barrier.wait(5)
            self.wait_operator("人工检查模拟成绩")
            return {"score": self.options.score_min, "total": 100}
    monkeypatch.setattr(worker_module, "MiniProgramSession", Session)
    thread = threading.Thread(target=worker.run)
    thread.start()
    try:
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            with worker._pause_lock:
                ready = len(worker._waiting) == 2
            if ready:
                break
            time.sleep(.01)
        assert ready and worker.control.paused.is_set()
        worker.control.after_current.set()
        worker.control.resume.set()
        thread.join(6)
        assert not thread.is_alive()
        assert not worker.control.paused.is_set()
        assert [r.status for r in worker.store.records] == ["completed", "completed", "pending", "pending", "pending"]
    finally:
        worker.control.stop.set()
        thread.join(6)
