from dataclasses import replace

import pytest

import yunkaoxing.worker as worker_module
from yunkaoxing.engine import LoginError, Stopped
from yunkaoxing.models import RunOptions
from yunkaoxing.page import PageError, Snapshot
from yunkaoxing.profile import SiteProfile
from yunkaoxing.store import StoreError
from yunkaoxing.worker import BatchWorker


class Port:
    def __init__(self, fail=False):
        self.fail, self.closed = fail, False

    def connect(self):
        if self.fail:
            raise PageError("小程序窗口暂时不可访问")

    def snapshot(self):
        return Snapshot(())

    def close(self):
        self.closed = True


def make_worker(tmp_path, candidate, **options):
    people = [candidate, replace(candidate, row=3, name="合成下一人", id_number="110101199001010029")]
    return BatchWorker(SiteProfile(), RunOptions(mode="login", background=True, **options), people, [], [], str(tmp_path), port_factory=lambda _: Port())


def test_failed_login_is_redacted_and_next_person_completes(tmp_path, candidate, monkeypatch):
    calls, recoveries = [], []
    class Session:
        def __init__(self, *args):
            pass
        def login(self, person):
            calls.append(person.row)
            if person.row == 2:
                raise LoginError(f"{person.name} {person.id_number}：当前考区未报名")
        def recover_login(self):
            recoveries.append(True)
    monkeypatch.setattr(worker_module, "MiniProgramSession", Session)
    worker = make_worker(tmp_path, candidate)
    worker.run()
    first, second = worker.store.records
    assert calls == [2, 3] and len(recoveries) == 1
    assert first.status == "failed" and "未报名" in first.reason
    assert candidate.name not in first.reason and candidate.id_number not in first.reason
    assert second.status == "completed"


def test_failed_connection_retries_for_the_next_person(tmp_path, candidate, monkeypatch):
    created, calls = [], []
    def factory(_):
        port = Port(fail=not created)
        created.append(port)
        return port
    class Session:
        def __init__(self, *args):
            pass
        def login(self, person):
            calls.append(person.row)
    monkeypatch.setattr(worker_module, "MiniProgramSession", Session)
    worker = make_worker(tmp_path, candidate)
    worker.port_factory = factory
    worker.run()
    assert len(created) == 2 and all(port.closed for port in created)
    assert [r.status for r in worker.store.records] == ["failed", "completed"]
    assert calls == [3]


def test_failed_recovery_reconnects_before_next_identity(tmp_path, candidate, monkeypatch):
    created = []
    def factory(_):
        port = Port()
        created.append(port)
        return port
    class Session:
        def __init__(self, *args):
            pass
        def login(self, person):
            if person.row == 2:
                raise LoginError("身份信息不匹配")
        def recover_login(self):
            raise PageError("恢复到登录页失败")
    monkeypatch.setattr(worker_module, "MiniProgramSession", Session)
    worker = make_worker(tmp_path, candidate)
    worker.port_factory = factory
    worker.run()
    assert len(created) == 2
    assert [r.status for r in worker.store.records] == ["failed", "completed"]


@pytest.mark.parametrize("failure, expected", [(Stopped("用户停止"), "stopped"), (StoreError("统计写入失败"), "failed")])
def test_stop_and_storage_errors_do_not_move_to_the_next_person(tmp_path, candidate, monkeypatch, failure, expected):
    calls = []
    class Session:
        def __init__(self, *args):
            pass
        def login(self, person):
            calls.append(person.row)
            raise failure
    monkeypatch.setattr(worker_module, "MiniProgramSession", Session)
    worker = make_worker(tmp_path, candidate)
    worker.run()
    assert calls == [2]
    assert [r.status for r in worker.store.records] == [expected, "pending"]


def test_exam_errors_are_not_treated_as_skippable_login_failures(tmp_path, candidate, bank, monkeypatch):
    calls = []
    class Session:
        def __init__(self, *args):
            pass
        def login(self, person):
            calls.append(person.row)
        def open_exam(self, person):
            raise PageError("交卷状态无法确认")
    monkeypatch.setattr(worker_module, "MiniProgramSession", Session)
    worker = make_worker(tmp_path, candidate)
    worker.options = replace(worker.options, mode="exam", exam_title="合成考试")
    worker.questions = bank
    worker.run()
    assert calls == [2]
    assert [r.status for r in worker.store.records] == ["failed", "pending"]
