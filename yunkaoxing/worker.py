from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
import random
import threading
import time

from PySide6.QtCore import QThread, Signal

from .desktop import DesktopUIA
from .engine import Control, MiniProgramSession, Stopped
from .knowledge import KnowledgeIndex
from .practice import run_practice
from .privacy import redact
from .page import PageError
from .store import BatchStore, StoreError, now


class BatchWorker(QThread):
    message = Signal(str)
    started_batch = Signal(object)
    record_changed = Signal(int, object)
    needs_operator = Signal(str)
    completed = Signal(str)

    def __init__(self, profile, options, candidates, rejected, questions, output,
                 check_only=False, parent=None, port_factory=None, slot_port_factory=None):
        super().__init__(parent)
        self.profile, self.options, self.candidates = profile, options, candidates
        self.rejected, self.questions, self.output = rejected, questions, output
        self.control = Control()
        self.check_only = check_only
        self.port_factory, self.slot_port_factory = port_factory, slot_port_factory
        self.store = None
        self.secrets = tuple(v for c in candidates for v in (c.name, c.id_number))
        self._local = threading.local()
        self._store_lock = threading.RLock()
        self._dispatch_lock = threading.Lock()
        self._plan_lock = threading.Lock()
        self._fatal_lock = threading.Lock()
        self._pause_lock = threading.Lock()
        self._pending, self._active = deque(), set()
        self._waiting, self._pause_epoch = set(), 0
        self._fatal_error = None
        self._score_counts, self._score_reserved = {}, Counter()
        self._last_reserved = None
        self._mini_program_closed = False
        self._mini_program_close_error = None
        self._admitted_people = 0
        self.finished_all_people = False

    @property
    def current(self):
        return getattr(self._local, "current", None)

    @current.setter
    def current(self, value):
        self._local.current = value

    @property
    def simulated(self):
        return self.port_factory is not None or self.slot_port_factory is not None

    def log(self, text):
        self.message.emit(redact(text, self.secrets))

    def _fatal(self, error):
        with self._fatal_lock:
            if self._fatal_error is None:
                self._fatal_error = error
        self.control.stop.set()

    def update(self, **changes):
        index = self.current
        if self.store is None or index is None:
            return
        with self._store_lock:
            try:
                self.store.update(index, **changes)
            except StoreError as error:
                self._fatal(error)
                raise
            finally:
                # Freeze each queued UI notification rather than sharing a record
                # that a different session may change before the signal arrives.
                self.record_changed.emit(index, replace(self.store.records[index]))

    def _mark_ended(self, status, reason):
        index = self.current
        if self.store is None or index is None:
            return
        try:
            self.update(status=status, ended=now(), reason=reason)
        except StoreError:
            # Retain the outcome for Re-export after a disk or Excel-lock failure.
            with self._store_lock:
                record = self.store.records[index]
                record.status, record.ended = status, now()
                record.reason = redact(reason, self.secrets)
                self.record_changed.emit(index, replace(record))

    def before_input(self):
        self.control.check()
        while self.control.paused.is_set():
            with self._pause_lock:
                if self.current in self._waiting:
                    break
            self.control.sleep(.05)
            self.control.check()

    def wait_operator(self, reason):
        index = self.current
        with self._pause_lock:
            if not self._waiting:
                self.control.resume.clear()
            epoch = self._pause_epoch
            self._waiting.add(index)
            self.control.paused.set()
        self.update(status="waiting", reason=reason)
        row = self.store.records[index].candidate.row if index is not None else "?"
        self.needs_operator.emit(redact(f"身份表第 {row} 行：{reason}；点击继续核对将继续当前等待的会话", self.secrets))
        try:
            while True:
                self.control.check()
                signalled = self.control.resume.wait(.1)
                with self._pause_lock:
                    if epoch != self._pause_epoch:
                        break
                    if signalled:
                        self._pause_epoch += 1
                        self._waiting.clear()
                        self.control.resume.clear()
                        self.control.paused.clear()
                        break
            self.control.check()
        finally:
            with self._pause_lock:
                self._waiting.discard(index)
                if not self._waiting:
                    self.control.paused.clear()
        self.update(status="running", reason="已继续，重新读取小程序页面")

    def connect_port(self, slot_index=0):
        if self.slot_port_factory is not None:
            port = self.slot_port_factory(slot_index, self.before_input)
        elif self.port_factory is not None:
            port = self.port_factory(self.before_input)
        else:
            port = DesktopUIA(self.profile, self.before_input, background=self.options.background)
        try:
            port.connect()
            deadline = time.monotonic() + self.options.timeout_seconds
            while True:
                self.control.check()
                try:
                    snapshot = port.snapshot()
                    break
                except PageError:
                    # A newly created WeChat window precedes its business page
                    # and accessible AppID document. Do not fail the first person
                    # merely because that document is still loading.
                    if self.simulated or time.monotonic() >= deadline:
                        raise
                    self.control.sleep(.2)
        except BaseException:
            self.close_port(port)
            raise
        target = "本地小程序模拟器" if self.simulated else self.profile.window_title
        self.log(f"会话 {slot_index + 1} 已连接{target}；当前页面提供 {len(snapshot.nodes)} 个可读控件")
        return port

    @staticmethod
    def close_port(port):
        if port is not None:
            try:
                port.close()
            except Exception:
                pass

    def error_reason(self, exc):
        detail = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else f"小程序连接或控件操作失败（{type(exc).__name__}）"
        return redact(detail, self.secrets)

    def _take_next(self):
        while True:
            self.control.check()
            if self.control.paused.is_set():
                self.control.sleep(.05)
                continue
            with self._dispatch_lock:
                if self.control.stop.is_set() or self.control.after_current.is_set() or not self._pending:
                    return None
                if self.control.dispatch_paused.is_set() or self.control.paused.is_set():
                    continue
                index = self._pending.popleft()
                self._active.add(index)
                self._admitted_people += 1
                return index

    def _reserve_plan(self):
        maximum = self.options.minimum_seconds if self.options.maximum_seconds is None else self.options.maximum_seconds
        duration = 0 if self.options.fastest else random.SystemRandom().uniform(self.options.minimum_seconds, maximum)
        options = replace(self.options, minimum_seconds=duration, maximum_seconds=duration)
        target = None
        if self.options.mode == "practice" or self.options.simulate_scores:
            with self._plan_lock:
                eligible = [score for score in self._score_counts if score != self._last_reserved] or list(self._score_counts)
                minimum = min(self._score_counts[score] + self._score_reserved[score] for score in eligible)
                target = random.SystemRandom().choice([score for score in eligible if self._score_counts[score] + self._score_reserved[score] == minimum])
                self._score_reserved[target] += 1
                self._last_reserved = target
            options = replace(options, score_min=target, score_max=target)
        return options, target

    def _release_plan(self, target, score=None):
        with self._plan_lock:
            if target is not None:
                self._score_reserved[target] -= 1
            if score in self._score_counts:
                self._score_counts[score] += 1

    def _has_pending_people(self):
        with self._dispatch_lock:
            return bool(self._pending)

    def _all_people_finished(self):
        if (not self._admitted_people or self.check_only
                or self.control.stop.is_set() or self.control.after_current.is_set()
                or self._fatal_error is not None or self.store is None):
            return False
        with self._dispatch_lock:
            if self._pending or self._active:
                return False
        return all(record.status in {"completed", "failed"} for record in self.store.records)

    def _close_finished_mini_program(self, port):
        if self.simulated or self.options.mode == "practice" or not self._all_people_finished():
            return
        try:
            if port is None:
                raise PageError("没有可核对的已连接窗口，未重新打开小程序来关闭")
            close_window = getattr(port, "close_mini_program", None)
            if close_window is None:
                raise PageError("当前连接未提供小程序窗口关闭接口")
            close_window()
        except Stopped:
            self.control.stop.set()
        except Exception as exc:
            self._mini_program_close_error = self.error_reason(exc)
            self.log("人员处理已结束，但小程序窗口未确认关闭：" + self._mini_program_close_error)
        else:
            self._mini_program_closed = True
            self.log("所有人员处理结束，已确认关闭小程序窗口")

    def _slot_loop(self, slot_index, knowledge):
        port = None
        try:
            while True:
                index = self._take_next()
                if index is None:
                    return
                self.current = index
                person = self.store.records[index].candidate
                target, successful_score = None, None
                try:
                    stage = "本地模拟" if self.options.mode == "practice" else "考生登录"
                    if self.simulated:
                        stage = "模拟" + stage
                    self.update(status="running", stage=stage, started=now())
                    self.log(f"会话 {slot_index + 1} 开始处理身份表第 {person.row} 行")
                    options, target = self._reserve_plan()
                    report = lambda text, row=person.row: self.log(f"第 {row} 行：{text}")
                    if self.options.mode == "practice":
                        outcome = run_practice(self.questions, options, self.control, report)
                    else:
                        session = None
                        try:
                            if port is None:
                                port = self.connect_port(slot_index)
                            session = MiniProgramSession(port, self.profile, options, self.control, report, self.wait_operator)
                            session.login(person)
                        except (Stopped, StoreError):
                            raise
                        except Exception as exc:
                            self.control.check()
                            reason = "登录异常：" + self.error_reason(exc)
                            self.update(status="failed", ended=now(), reason=reason)
                            self.log(f"身份表第 {person.row} 行：{reason}；继续下一人")
                            self.current = None
                            if not self.control.after_current.is_set():
                                try:
                                    if session is not None:
                                        session.recover_login()
                                except Stopped:
                                    raise
                                except Exception as recovery_error:
                                    self.log("当前登录页面未恢复：" + self.error_reason(recovery_error) + "；下名考生将重新连接并核对")
                                    if self.simulated or self._has_pending_people():
                                        self.close_port(port)
                                        port = None
                            continue
                        outcome = {"reason": "已核对考生首页及身份信息"}
                        if self.options.mode == "login" and not self.simulated:
                            self.update(stage="退出当前考生")
                            try:
                                session.logout()
                                outcome["reason"] += "；已退出当前考生"
                            except Stopped:
                                raise
                            except Exception as exc:
                                # Identity verification remains a success. The
                                # next fresh session must still log out before
                                # entering another person's information.
                                detail = self.error_reason(exc)
                                outcome["reason"] += "；退出异常：" + detail
                                self.log("登录已成功，但退出未完成：" + detail + "；下名考生将重新连接核对")
                                if self._has_pending_people():
                                    self.close_port(port)
                                    port = None
                        if self.options.mode == "exam":
                            self.update(stage="模拟小程序考试" if self.simulated else "小程序考试")
                            session.open_exam(person)
                            outcome = session.answer_exam(knowledge)
                            if self.simulated:
                                outcome["reason"] = "模拟小程序独立评分，非真实考试成绩"
                    with self._store_lock:
                        if self.control.stop.is_set():
                            raise Stopped("批次已停止，当前结果未确认")
                        self.update(status="completed", ended=now(), **outcome)
                    successful_score = outcome.get("score")
                except Stopped:
                    self._mark_ended("stopped", "用户停止或批次中断，当前小程序页面保留；实际结果需核对")
                    if not self.control.stop.is_set():
                        self.control.stop.set()
                    return
                except Exception as exc:
                    self._fatal(exc)
                    self._mark_ended("failed", self.error_reason(exc))
                    return
                finally:
                    self._release_plan(target, successful_score)
                    self.current = None
                    with self._dispatch_lock:
                        self._active.discard(index)
        except Stopped:
            return
        except Exception as exc:
            self._fatal(exc)
        finally:
            self._close_finished_mini_program(port)
            self.close_port(port)
            self.current = None

    def run(self):
        port = None
        try:
            if not self.check_only:
                self.options.validate()
            concurrency = self.options.concurrency
            if self.options.mode != "practice" and not self.simulated and concurrency != 1:
                raise ValueError("真实微信仅有一个当前考生会话，并发数量必须为 1")
            if self.options.mode != "practice" and concurrency > 1 and self.slot_port_factory is None:
                raise ValueError("并发模拟需要为每个名额提供独立模拟会话")
            if self.options.simulate_scores and self.options.mode == "exam" and not self.simulated:
                raise ValueError("模拟得分计划只能用于本地小程序模拟器")
            if self.check_only:
                port = self.connect_port()
                self.completed.emit("环境检测通过：本地模拟器已连接" if self.simulated else "环境检测通过：小程序进程、AppID 和可访问控件已核对")
                return
            knowledge = KnowledgeIndex(self.questions) if self.options.mode in {"exam", "practice"} else None
            store_mode = "simulator_" + self.options.mode if self.simulated else self.options.mode
            self.store = BatchStore(self.output, self.candidates, self.rejected, store_mode, self.options)
            self.store.persist()
            self.started_batch.emit(self.store)
            self._pending = deque(i for i, record in enumerate(self.store.records) if record.status == "pending")
            self._score_counts = {score: 0 for score in range(max(self.options.minimum_score, self.options.score_min), self.options.score_max + 1)}
            count = min(concurrency, len(self._pending))
            if count == 1:
                self._slot_loop(0, knowledge)
            elif count > 1:
                with ThreadPoolExecutor(max_workers=count, thread_name_prefix="mini-session") as executor:
                    futures = [executor.submit(self._slot_loop, slot, knowledge) for slot in range(count)]
                    for future in as_completed(futures):
                        try:
                            future.result()
                        except Exception as exc:
                            self._fatal(exc)
            if self._fatal_error is not None:
                raise self._fatal_error
            counts = {status: sum(record.status == status for record in self.store.records) for status in ("completed", "failed", "pending", "stopped")}
            self.finished_all_people = self._all_people_finished()
            if self.control.stop.is_set():
                self.completed.emit(f"已停止自动操作：完成 {counts['completed']} 人，失败 {counts['failed']} 人，中止 {counts['stopped']} 人，未开始 {counts['pending']} 人。当前小程序页面保留。")
            else:
                window_result = "已关闭小程序窗口。" if self._mini_program_closed else ""
                if self._mini_program_close_error:
                    window_result = "小程序窗口未确认关闭：" + self._mini_program_close_error + "。"
                self.completed.emit(f"批次结束：完成 {counts['completed']} 人，失败 {counts['failed']} 人，未开始 {counts['pending']} 人。统计已保存。" + window_result)
        except Stopped:
            self.completed.emit("已停止自动操作，当前小程序页面保留；未继续接入人员")
        except Exception as exc:
            reason = self.error_reason(exc)
            self.log(reason)
            self.completed.emit("批次已停止：" + reason)
        finally:
            self.close_port(port)
            self.control.paused.clear()
