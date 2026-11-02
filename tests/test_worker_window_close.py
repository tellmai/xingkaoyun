"""Batch-end mini-program lifetime policy with fake real-provider sessions."""
from dataclasses import replace

import pytest

import yunkaoxing.worker as worker_module
from yunkaoxing.engine import LoginError, Stopped
from yunkaoxing.models import RunOptions
from yunkaoxing.page import PageError, Snapshot
from yunkaoxing.profile import SiteProfile
from yunkaoxing.store import StoreError
from yunkaoxing.worker import BatchWorker


def make_worker(tmp_path, candidate, monkeypatch, *, actions=("ok", "ok"), close_error=None,
                recovery_error=None, check_only=False, simulated=False, connection_error=False):
    events, ports, messages, finished = [], [], [], []
    people = [replace(candidate, row=i + 2) for i in range(len(actions))]

    class Port:
        def __init__(self, *args, **kwargs):
            ports.append(self)
        def connect(self):
            events.append("connect")
            if connection_error:
                raise PageError("连接不可用")
        def snapshot(self):
            return Snapshot(())
        def close_mini_program(self):
            events.append("close-window")
            if close_error:
                raise close_error
        def close(self):
            events.append("release")

    class Session:
        def __init__(self, port, profile, options, control, *args):
            self.control, self.row = control, None
        def login(self, person):
            self.row = person.row
            events.append(("login", self.row))
            action = actions[self.row - 2]
            if action == "fail":
                raise LoginError("当前考区未报名")
            if action == "stop":
                self.control.stop.set()
                raise Stopped("用户停止")
            if action == "after-current":
                self.control.after_current.set()
            if action == "fatal":
                raise StoreError("统计写入失败")
        def recover_login(self):
            events.append(("recover", self.row))
            if recovery_error:
                raise recovery_error
        def logout(self):
            events.append(("logout", self.row))

    monkeypatch.setattr(worker_module, "DesktopUIA", Port)
    monkeypatch.setattr(worker_module, "MiniProgramSession", Session)
    factory = (lambda before_input: Port()) if simulated else None
    worker = BatchWorker(SiteProfile(), RunOptions(mode="login", background=True), people, [], [],
                         str(tmp_path), check_only=check_only, port_factory=factory)
    worker.message.connect(messages.append)
    worker.completed.connect(finished.append)
    return worker, events, ports, messages, finished


@pytest.mark.parametrize("actions", [("ok", "ok"), ("fail", "ok"), ("ok", "fail"), ("fail", "fail")])
def test_normal_real_batch_closes_window_once_after_all_people_and_recovery(tmp_path, candidate, monkeypatch, actions):
    worker, events, ports, _, finished = make_worker(tmp_path, candidate, monkeypatch, actions=actions)
    worker.run()
    assert worker.finished_all_people
    assert [record.status for record in worker.store.records] == ["failed" if action == "fail" else "completed" for action in actions]
    assert len(ports) == 1 and events.count("close-window") == 1
    assert events[-2:] == ["close-window", "release"]
    for index, action in enumerate(actions):
        if action == "fail":
            assert events.index(("recover", index + 2)) < events.index("close-window")
    assert "已关闭小程序窗口" in finished[-1]


def test_last_login_recovery_failure_does_not_discard_target_before_final_close(tmp_path, candidate, monkeypatch):
    worker, events, ports, messages, _ = make_worker(tmp_path, candidate, monkeypatch, actions=("fail",),
                                                    recovery_error=PageError("错误弹窗未消失"))
    worker.run()
    assert worker.finished_all_people and worker.store.records[0].status == "failed"
    assert len(ports) == 1 and events[-3:] == [("recover", 2), "close-window", "release"]
    assert any("错误弹窗未消失" in message for message in messages)


@pytest.mark.parametrize("actions", [("stop", "ok"), ("after-current", "ok"), ("after-current",), ("fatal", "ok")])
def test_stopped_or_fatal_batch_preserves_window(tmp_path, candidate, monkeypatch, actions):
    worker, events, _, _, finished = make_worker(tmp_path, candidate, monkeypatch, actions=actions)
    worker.run()
    assert not worker.finished_all_people and "close-window" not in events
    assert ("login", 3) not in events
    assert "release" in events
    assert "已关闭小程序窗口" not in finished[-1]


def test_environment_check_only_does_not_close_target(tmp_path, candidate, monkeypatch):
    worker, events, _, _, finished = make_worker(tmp_path, candidate, monkeypatch, check_only=True)
    worker.run()
    assert not worker.finished_all_people
    assert events == ["connect", "release"] and "环境检测通过" in finished[-1]


@pytest.mark.parametrize("only_rejected", [False, True])
def test_no_admitted_people_do_not_connect_or_close_a_window(tmp_path, candidate, monkeypatch, only_rejected):
    worker, events, _, _, _ = make_worker(tmp_path, candidate, monkeypatch, actions=())
    if only_rejected:
        worker.rejected = [(candidate, "导入资料不完整")]
    worker.run()
    assert not worker.finished_all_people and events == []


def test_window_close_failure_is_explicit_without_losing_success_statistics(tmp_path, candidate, monkeypatch):
    worker, events, _, messages, finished = make_worker(tmp_path, candidate, monkeypatch,
                                                       close_error=PageError("窗口关闭超时"))
    worker.run()
    assert worker.finished_all_people and [record.status for record in worker.store.records] == ["completed", "completed"]
    assert events.count("close-window") == 1 and events[-1] == "release"
    assert any("窗口未确认关闭" in message and "关闭超时" in message for message in messages)
    assert "小程序窗口未确认关闭" in finished[-1] and "窗口关闭超时" in finished[-1]


def test_failed_connections_do_not_reopen_an_app_just_to_close_it(tmp_path, candidate, monkeypatch):
    worker, events, ports, _, finished = make_worker(tmp_path, candidate, monkeypatch, connection_error=True)
    worker.run()
    assert worker.finished_all_people and len(ports) == 2
    assert events == ["connect", "release", "connect", "release"]
    assert all(record.status == "failed" for record in worker.store.records)
    assert "未重新打开小程序" in finished[-1]


def test_simulated_completion_exposes_flag_but_never_closes_real_window(tmp_path, candidate, monkeypatch):
    worker, events, _, _, _ = make_worker(tmp_path, candidate, monkeypatch, simulated=True)
    worker.run()
    assert worker.finished_all_people and "close-window" not in events
    assert events[-1] == "release"
