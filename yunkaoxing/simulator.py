"""An interactive native mini-program simulator and a Qt-thread-safe input port."""
import random
import re
import threading
import time
from dataclasses import replace

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QPushButton, QRadioButton, QScrollArea, QVBoxLayout, QWidget,
)

from .models import Candidate
from .knowledge import KnowledgeIndex
from .page import Node, PageError, Snapshot
from .practice import grade, make_paper


EXAM_TITLE = "法治理论知识模拟考试"
DISTRICTS = ["两江新区", "璧山区", "合川区", "云阳县", "石柱县", "涪陵区", "江津区", "綦江区", "丰都县", "城口县", "北碚区", "大足区", "巴南区", "永川区", "大渡口区", "秀山县", "垫江县", "荣昌区", "长寿区", "九龙坡区", "黔江区", "铜梁区", "渝中区", "沙坪坝区", "南岸区", "潼南区", "忠县", "武隆区", "万州区", "彭水县", "奉节县", "梁平区", "巫山县", "开州区", "巫溪县", "酉阳县", "万盛经开区"]

SIM_STYLE = """
QMainWindow { background: #f7f5f3; }
QWidget { color: #292d2b; font-family: 'Microsoft YaHei UI'; font-size: 14px; }
QLabel#simHeader { font-size: 18px; font-weight: 700; padding: 12px; background: white; }
QLabel#simBanner { background: #f9e7c6; color: #805713; padding: 7px; font-size: 12px; }
QLabel#simTitle { font-size: 22px; font-weight: 700; margin-top: 12px; margin-bottom: 10px; }
QLabel#score { font-size: 60px; font-weight: 700; color: #98252f; }
QFrame#simCard { background: white; border: 1px solid #e9e1dc; border-radius: 10px; }
QPushButton { background: white; border: 1px solid #e2dcd6; border-radius: 8px; padding: 13px; text-align: left; }
QPushButton:hover { background: #f5e9e7; }
QPushButton#simPrimary { background: #98232c; color: white; font-weight: 700; text-align: center; border: 0; }
QLineEdit { background: #faf9f7; border: 1px solid #dcd7d1; border-radius: 7px; padding: 13px; }
QRadioButton, QCheckBox { padding: 12px; background: #faf8f6; border-radius: 6px; spacing: 9px; }
QScrollArea { border: 0; background: transparent; }
"""


class SimulatorWindow(QMainWindow):
    def __init__(self, questions, candidates=(), parent=None):
        super().__init__(parent)
        self.setWindowTitle("云考星 · 本地小程序模拟器")
        self.resize(430, 830)
        self.setMinimumSize(390, 650)
        self.setStyleSheet(SIM_STYLE)
        self.bank = list(questions)
        self.districts = list(dict.fromkeys(DISTRICTS + [c.district for c in candidates]))
        self.stage, self.district, self.candidate = "region", "", None
        self.personal, self.logout_confirm, self.submit_confirm = False, False, False
        self.paper, self.answers, self.number, self.result = [], {}, 0, None
        self.error, self.history, self.started = "", [], None
        self.token, self.registry, self.active = 0, {}, False
        self.exam_title = EXAM_TITLE
        self.render()

    def configure(self, questions, candidates):
        if self.active:
            raise PageError("模拟器正在执行，请先停止批次")
        self.bank = list(questions)
        self.districts = list(dict.fromkeys(DISTRICTS + [c.district for c in candidates]))
        self.stage, self.candidate, self.result = "region", None, None
        self.personal = self.logout_confirm = self.submit_confirm = False
        self.error = ""
        self.render()

    def add_label(self, text, style="", box=None):
        box = box or self.box
        widget = QLabel(text)
        widget.setWordWrap(True)
        if style:
            widget.setObjectName(style)
        box.addWidget(widget)
        self.register(widget, "Text", text)
        return widget

    def register(self, widget, kind, name):
        key = str(len(self.registry))
        widget.setAccessibleName(name)
        self.registry[key] = (widget, kind, name)
        return widget

    def add_button(self, name, handler, primary=False, box=None, visible_text=None):
        box = box or self.box
        widget = QPushButton(visible_text or name)
        if primary:
            widget.setObjectName("simPrimary")
        widget.clicked.connect(handler)
        box.addWidget(widget)
        self.register(widget, "Button", name)
        return widget

    def go(self, stage):
        self.stage, self.error = stage, ""
        self.personal = self.logout_confirm = self.submit_confirm = False
        self.render()

    def render(self):
        self.token += 1
        self.registry = {}
        old = self.takeCentralWidget()
        if old:
            old.deleteLater()
        root = QWidget()
        main = QVBoxLayout(root)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)
        header = QLabel({"region": "选择区县", "login": "考生登录", "home": "考试服务", "details": "考试须知", "question": "在线模拟考试", "result": "考试成绩", "history": "历史成绩"}[self.stage])
        header.setObjectName("simHeader")
        header.setAlignment(Qt.AlignCenter)
        main.addWidget(header)
        self.register(header, "Text", header.text())
        banner = QLabel("本地模拟环境 · 不连接微信及真实考试系统")
        banner.setObjectName("simBanner")
        banner.setAlignment(Qt.AlignCenter)
        main.addWidget(banner)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        self.box = QVBoxLayout(body)
        self.box.setContentsMargins(18, 18, 18, 20)
        self.box.setSpacing(12)
        scroll.setWidget(body)
        main.addWidget(scroll, 1)
        self.setCentralWidget(root)
        if self.stage == "region":
            self.add_label("重庆市法治理论知识考试系统", "simTitle")
            self.add_label("请选择报名时所在的区县")
            search = QLineEdit()
            search.setPlaceholderText("搜索区县名称或拼音")
            self.box.addWidget(search)
            self.register(search, "Edit", "搜索区县名称或拼音")
            grid = QGridLayout()
            self.box.addLayout(grid)
            buttons = []
            for i, district in enumerate(self.districts):
                button = QPushButton(district + "  ›")
                button.clicked.connect(lambda checked=False, d=district: self.choose_district(d))
                grid.addWidget(button, i // 2, i % 2)
                self.register(button, "Button", "选择" + district)
                buttons.append((district, button))
            search.textChanged.connect(lambda value: [b.setVisible(value.strip() in d) for d, b in buttons])
        elif self.stage == "login":
            self.add_label("当前考区")
            self.add_label(self.district)
            self.add_button("重新选择 ›", lambda: self.go("region"))
            self.add_label("验证考生身份", "simTitle")
            self.add_label("模拟登录仅校验格式。请使用合成身份数据。")
            self.name_edit = QLineEdit()
            self.name_edit.setPlaceholderText("请输入真实姓名")
            self.box.addWidget(self.name_edit)
            self.register(self.name_edit, "Edit", "请输入真实姓名")
            self.id_edit = QLineEdit()
            self.id_edit.setPlaceholderText("请输入15位或18位身份证号")
            self.box.addWidget(self.id_edit)
            self.register(self.id_edit, "Edit", "请输入15位或18位身份证号")
            self.agreement = QCheckBox("同意用户服务协议和隐私政策")
            self.box.addWidget(self.agreement)
            self.register(self.agreement, "CheckBox", self.agreement.text())
            self.add_button("安全登录", self.login, True)
        elif self.stage in {"home", "history"}:
            self.add_label(self.candidate.name, "simTitle")
            self.add_label(self.district + "/模拟单位/模拟部门")
            self.add_button("退出", self.ask_logout)
            if self.stage == "home":
                self.add_label("年度网考", "simTitle")
                self.add_button(self.exam_title, lambda: self.go("details"))
                self.add_label("已安排 · 100 题 / 满分 100 分")
                self.add_button("历史成绩 ›", lambda: self.go("history"))
                self.add_button("人 个人资料 ›", self.open_personal)
            else:
                for result in self.history:
                    if result["id"] == self.candidate.id_number:
                        self.add_label(f"{self.exam_title}\n得分：{result['score']} 分")
                self.add_button("返回考生首页", lambda: self.go("home"))
        elif self.stage == "details":
            self.add_label(self.exam_title, "simTitle")
            self.add_label("本次模拟试卷共 100 题，包含单选、多选、判断题，每题 1 分。")
            self.add_label("题目和选项会随机排序；多选题少选、多选均不得分。交卷前会检查漏答。")
            self.add_button("开始考试", self.start_exam, True)
            self.add_button("返回考生首页", lambda: self.go("home"))
        elif self.stage == "question":
            q = self.paper[self.number]
            self.add_label(self.exam_title)
            self.add_label(f"{self.number + 1}/{len(self.paper)}")
            self.add_label(q.kind + "题")
            self.add_label(q.stem, "simTitle")
            self.option_group = QButtonGroup(body)
            for label, text in q.options.items():
                option = QCheckBox(f"{label}. {text}") if q.kind == "多选" else QRadioButton(f"{label}. {text}")
                if q.kind != "多选":
                    self.option_group.addButton(option)
                option.setChecked(label in self.answers.get(self.number, set()))
                option.toggled.connect(lambda checked, key=label: self.answer_changed(key, checked))
                self.box.addWidget(option)
                self.register(option, "CheckBox" if q.kind == "多选" else "RadioButton", option.text())
            self.add_button("上一题", lambda: self.move(-1)).setEnabled(self.number > 0)
            self.add_button("下一题", lambda: self.move(1)).setEnabled(self.number < len(self.paper) - 1)
            self.add_button("交卷", self.ask_submit, True)
        elif self.stage == "result":
            self.add_label(self.exam_title, "simTitle")
            self.add_label("考试完成")
            self.add_label(f"{self.result['score']}", "score")
            self.add_label(f"考试成绩：{self.result['score']} 分")
            self.add_label(f"正确题数 {self.result['score']}/100 · 用时 {self.result['elapsed_seconds']:.1f} 秒")
            self.add_label("模拟成绩，仅保存在本次模拟器会话中。")
            self.add_button("返回考生首页", lambda: self.go("home"), True)
            self.add_button("退出", self.ask_logout)
        if self.error:
            label = self.add_label(self.error)
            label.setStyleSheet("color:#a12630")
        if self.personal:
            self.add_label("个人资料", "simTitle")
            self.add_label("当前登录考生信息")
            self.add_label(self.candidate.name)
            self.add_label(self.candidate.id_number[:6] + "********" + self.candidate.id_number[-4:])
            self.add_label(self.district)
            self.add_button("关闭个人资料", self.close_personal)
        if self.logout_confirm:
            self.add_label("确定退出当前考生？")
            self.add_button("确定退出", self.logout)
            self.add_button("取消退出", self.cancel_logout)
        if self.submit_confirm:
            self.add_label("请确认交卷，交卷后将独立计算模拟成绩。")
            self.add_button("确认交卷", self.submit, True)
            self.add_button("继续检查", self.cancel_submit)
        self.box.addStretch()

    def choose_district(self, district):
        self.district = district
        self.go("login")

    def login(self):
        name, identity = self.name_edit.text().strip(), self.id_edit.text().strip().upper()
        if not name or not re.fullmatch(r"(?:\d{15}|\d{17}[\dX])", identity) or not self.agreement.isChecked():
            self.error = "登录失败：请填写姓名、有效格式证件号，并勾选协议"
            self.render()
            return
        self.candidate = Candidate(0, self.district, name, identity)
        self.go("home")

    def open_personal(self):
        self.personal = True
        self.render()

    def close_personal(self):
        self.personal = False
        self.render()

    def ask_logout(self):
        self.logout_confirm = True
        self.render()

    def cancel_logout(self):
        self.logout_confirm = False
        self.render()

    def logout(self):
        self.candidate = None
        self.go("region")

    def start_exam(self):
        paper = make_paper(self.bank)
        index = KnowledgeIndex(self.bank)
        self.paper = []
        # The bank's letters are deliberately rearranged to exercise text-based matching.
        for q in paper:
            if not index.can_relabel(q.kind, q.stem, q.options):
                self.paper.append(q)
                continue
            old_labels = list(q.options)
            random.SystemRandom().shuffle(old_labels)
            mapping = {old: chr(65 + i) for i, old in enumerate(old_labels)}
            self.paper.append(replace(q, options={mapping[k]: q.options[k] for k in old_labels}, answers=tuple(sorted(mapping[a] for a in q.answers))))
        self.answers, self.number, self.started, self.result = {}, 0, time.monotonic(), None
        self.go("question")

    def answer_changed(self, label, selected):
        answers = self.answers.setdefault(self.number, set())
        if selected:
            answers.add(label)
        else:
            answers.discard(label)
        self.token += 1

    def move(self, delta):
        self.number = max(0, min(len(self.paper) - 1, self.number + delta))
        self.render()

    def ask_submit(self):
        missing = sum(not self.answers.get(i) for i in range(len(self.paper)))
        if missing:
            self.error = f"仍有 {missing} 题未作答，未交卷"
        else:
            self.submit_confirm = True
        self.render()

    def cancel_submit(self):
        self.submit_confirm = False
        self.render()

    def submit(self):
        if self.result is None:
            score = grade(self.paper, {i: tuple(self.answers.get(i, ())) for i in range(len(self.paper))})
            self.result = {"id": self.candidate.id_number, "score": score, "elapsed_seconds": time.monotonic() - self.started}
            self.history.append(dict(self.result))
        self.go("result")

    def snapshot(self):
        self.token += 1
        nodes = []
        for key, (widget, kind, name) in self.registry.items():
            if widget.isHidden():
                continue
            value = widget.text() if kind == "Edit" else ""
            selected = widget.isChecked() if kind in {"RadioButton", "CheckBox"} else None
            nodes.append(Node(key, kind, name, value, selected, widget.isEnabled()))
        return Snapshot(tuple(nodes), self.token)

    def perform(self, operation, snapshot=None, node=None, value=None):
        if operation == "snapshot":
            return self.snapshot()
        if snapshot.token != self.token or node.key not in self.registry:
            raise PageError("模拟器页面已变化，请重新读取")
        widget, kind, name = self.registry[node.key]
        if not widget.isEnabled():
            raise PageError("目标模拟器控件不可用")
        self.token += 1
        if operation == "click" and kind == "Button":
            widget.click()
        elif operation == "edit" and kind == "Edit":
            widget.setText(value)
        elif operation == "select" and kind in {"CheckBox", "RadioButton"}:
            if widget.isChecked() != value:
                widget.click()
        else:
            raise PageError("不支持的模拟器控件操作")

    def closeEvent(self, event):
        # Hiding keeps the active simulation and bridge alive; reopen from the workbench.
        if self.active:
            self.hide()
            event.ignore()
        else:
            event.accept()


class SimulatorBridge(QObject):
    requested = Signal(object)

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.requested.connect(self.execute, Qt.QueuedConnection)

    @Slot(object)
    def execute(self, request):
        try:
            if request.get("cancelled"):
                return
            request["result"] = self.window.perform(**request["arguments"])
        except Exception as exc:
            request["error"] = exc
        finally:
            request["done"].set()


class SimulatorPort:
    def __init__(self, bridge, before_input=lambda: None, background=False):
        self.bridge, self.before_input = bridge, before_input
        self.background = background

    def _pace(self, operation):
        if not self.background and operation != "snapshot":
            # Leave each page transition visible in foreground runs; no pacing
            # overhead is imposed on hidden simulations or offline self-tests.
            time.sleep(.04 if operation == "click" else .015)
            self.before_input()

    def connect(self):
        pass

    def _request(self, **arguments):
        self.before_input()
        if QThread.currentThread() == self.bridge.thread():
            result = self.bridge.window.perform(**arguments)
            self._pace(arguments["operation"])
            return result
        request = {"arguments": arguments, "done": threading.Event()}
        self.bridge.requested.emit(request)
        try:
            deadline = time.monotonic() + 20
            while not request["done"].wait(.05):
                self.before_input()
                if time.monotonic() > deadline:
                    raise PageError("模拟器界面未响应")
        except BaseException:
            request["cancelled"] = True
            raise
        if "error" in request:
            raise request["error"]
        self._pace(arguments["operation"])
        return request.get("result")

    def snapshot(self):
        return self._request(operation="snapshot")

    def click(self, snapshot, node):
        self._request(operation="click", snapshot=snapshot, node=node)

    def edit(self, snapshot, node, value):
        self._request(operation="edit", snapshot=snapshot, node=node, value=value)

    def select(self, snapshot, node, selected):
        self._request(operation="select", snapshot=snapshot, node=node, value=selected)

    def close(self):
        pass
