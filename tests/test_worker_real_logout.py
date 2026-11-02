"""Real-provider branch only; no desktop windows or personal data are accessed."""
from dataclasses import replace

import yunkaoxing.worker as worker_module
from yunkaoxing.engine import Stopped
from yunkaoxing.models import RunOptions
from yunkaoxing.page import PageError, Snapshot
from yunkaoxing.profile import SiteProfile
from yunkaoxing.worker import BatchWorker


def real_worker(tmp_path, candidate, monkeypatch, *, exit_error=None):
    events, ports = [], []
    people = [candidate, replace(candidate, row=3, name="合成下一人", id_number="110101199001010029")]

    class DesktopPort:
        def __init__(self, profile, before_input, background):
            self.closed = False
            ports.append(self)
        def connect(self):
            events.append(("connect", len(ports)))
        def snapshot(self):
            return Snapshot(())
        def close(self):
            self.closed = True
            events.append(("close", ports.index(self) + 1))

    class Session:
        def __init__(self, *args):
            self.person = None
        def login(self, person):
            self.person = person
            events.append(("login", person.row))
        def logout(self):
            events.append(("logout", self.person.row))
            if exit_error is not None and self.person.row == 2:
                raise exit_error

    monkeypatch.setattr(worker_module, "DesktopUIA", DesktopPort)
    monkeypatch.setattr(worker_module, "MiniProgramSession", Session)
    worker = BatchWorker(SiteProfile(), RunOptions(mode="login", background=True), people, [], [], str(tmp_path))
    assert not worker.simulated
    return worker, events, ports


def test_real_login_worker_exits_each_success_before_the_next_person(tmp_path, candidate, monkeypatch):
    worker, events, ports = real_worker(tmp_path, candidate, monkeypatch)
    worker.run()
    assert [event for event in events if event[0] in {"login", "logout"}] == [
        ("login", 2), ("logout", 2), ("login", 3), ("logout", 3)]
    assert len(ports) == 1 and ports[0].closed
    assert [record.status for record in worker.store.records] == ["completed", "completed"]
    assert all("已退出当前考生" in record.reason for record in worker.store.records)


def test_exit_failure_preserves_verified_success_and_reconnects_for_next_person(tmp_path, candidate, monkeypatch):
    worker, events, ports = real_worker(tmp_path, candidate, monkeypatch, exit_error=PageError("退出入口暂时不可用"))
    worker.run()
    assert [record.status for record in worker.store.records] == ["completed", "completed"]
    assert "退出异常" in worker.store.records[0].reason and "已核对考生首页" in worker.store.records[0].reason
    assert "已退出当前考生" in worker.store.records[1].reason
    assert events == [("connect", 1), ("login", 2), ("logout", 2), ("close", 1),
                      ("connect", 2), ("login", 3), ("logout", 3), ("close", 2)]
    assert all(port.closed for port in ports)


def test_stop_during_exit_prevents_the_next_person_from_starting(tmp_path, candidate, monkeypatch):
    worker, events, ports = real_worker(tmp_path, candidate, monkeypatch, exit_error=Stopped("用户停止"))
    worker.run()
    assert [record.status for record in worker.store.records] == ["stopped", "pending"]
    assert ("login", 3) not in events and all(port.closed for port in ports)
