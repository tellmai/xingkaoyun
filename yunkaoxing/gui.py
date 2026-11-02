import html
import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
    QFormLayout, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
    QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSpinBox,
    QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from . import APP_NAME, __version__
from .importers import load_bank, load_identities
from .knowledge import KnowledgeIndex, sanitize_bank
from .lifetime import ensure_available
from .models import RunOptions
from .practice import make_paper
from .privacy import redact
from .profile import SiteProfile
from .store import LABELS, Record
from .worker import BatchWorker
from .simulator import EXAM_TITLE, SimulatorBridge, SimulatorPort, SimulatorWindow
from .ui import ACCENT, STYLESHEET, ElidedLabel, EmptyTable, WorkspaceSplitter, icon


STYLE = STYLESHEET


class ImportWorker(QThread):
    result = Signal(object)
    failed = Signal(str)
    progress = Signal(str)
    import_progress = Signal(int, int, str)

    def __init__(self, kind, path, parent=None):
        super().__init__(parent)
        self.kind, self.path = kind, path

    def run(self):
        try:
            loader = load_identities if self.kind == "identity" else load_bank
            if self.kind == "identity":
                content = Path(self.path).read_bytes()
                result = loader(self.path, self.progress.emit, on_progress=self.import_progress.emit, content=content)
                result.source_name, result.source_bytes = Path(self.path).name, content
            else:
                result = loader(self.path, self.progress.emit, on_progress=self.import_progress.emit)
            self.result.emit(result)
        except Exception as exc:
            self.failed.emit(redact(str(exc)) if isinstance(exc, ValueError) else "读取文件失败，请检查文件格式、内容及访问权限")


class PreviewDialog(QDialog):
    def __init__(self, questions, parent=None):
        super().__init__(parent)
        self.setWindowTitle("本地模拟试卷预览 · 不连接微信")
        self.resize(900, 700)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("本地抽取 100 题，答案仅用于模拟预览。"))
        browser = QTextBrowser()
        parts = []
        for i, q in enumerate(questions, 1):
            options = "<br>".join(f"{k}. {html.escape(v)}" for k, v in q.options.items())
            parts.append(f"<h3>{i}. [{q.kind}] {html.escape(q.stem)}</h3><p>{options}</p><p style='color:#137e70'>正确答案：{','.join(q.answers)}</p><hr>")
        browser.setHtml("".join(parts))
        layout.addWidget(browser)


class MainWindow(QMainWindow):
    def __init__(self, home, resources):
        super().__init__()
        self.home, self.resources = Path(home), Path(resources)
        self.settings_path = self.home / "data" / "settings.json"
        try:
            settings = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            settings = {}
        self.candidates, self.rejected, self.questions = [], [], []
        self.bank_anomalies = []
        self.records, self.result_folder, self.worker, self.store = [], None, None, None
        self.import_workers = []
        self.import_progress_rows = {}
        self._closing = False
        self.simulator = self.simulator_bridge = None
        self.simulators, self.simulator_bridges = [], []
        self._simulator_concurrency = max(1, min(30, int(settings.get("concurrency", 1))))
        self._settings = settings
        self._rendering_batch = False
        self.setWindowTitle(f"{APP_NAME}  v{__version__}")
        self.setWindowIcon(icon("brand"))
        self.resize(1360, 930)
        self.setMinimumSize(1000, 720)
        self.setStyleSheet(STYLE)
        root = QWidget()
        root.setObjectName("workspace")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(22, 16, 22, 16)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(12)
        brand = QLabel()
        brand.setPixmap(icon("brand").pixmap(QSize(44, 44)))
        header.addWidget(brand)
        titles = QVBoxLayout()
        titles.setSpacing(1)
        title = QLabel("云考星助手")
        title.setObjectName("brandTitle")
        titles.addWidget(title)
        subtitle = QLabel("微信小程序登录与模拟考试工作台")
        subtitle.setObjectName("brandCaption")
        titles.addWidget(subtitle)
        header.addLayout(titles)
        header.addStretch(1)
        version = QLabel(f"v{__version__}")
        version.setObjectName("versionBadge")
        header.addWidget(version)
        self.badge = QLabel("●  就绪")
        self.badge.setObjectName("activityBadge")
        header.addWidget(self.badge)
        layout.addLayout(header)

        self.target = QComboBox()
        self.target.addItem("本地小程序模拟器", "simulator")
        self.target.addItem("微信中的云考星", "wechat")
        self.target.setCurrentIndex(max(0, self.target.findData(settings.get("target", "simulator"))))
        self.target.setAccessibleName("连接目标")
        self.mode = QComboBox()
        for caption, value in (("仅验证考生登录", "login"), ("小程序考试", "exam"), ("本地模拟（离线）", "practice")):
            self.mode.addItem(caption, value)
        self.mode.setCurrentIndex(self.mode.findData("exam" if self.target.currentData() == "simulator" else "login"))
        self.mode.setAccessibleName("执行模式")
        self.exam_title = QLineEdit(settings.get("exam_title", EXAM_TITLE))
        self.exam_title.setPlaceholderText("填写小程序中的完整考试名称")
        self.exam_title.setAccessibleName("目标考试")
        self.minimum = QDoubleSpinBox()
        self.minimum.setRange(0, 180)
        self.minimum.setDecimals(1)
        self.minimum.setSuffix(" 分钟")
        self.minimum.setValue(settings.get("minutes", 10))
        self.minimum.setFixedWidth(112)
        self.minimum.setAccessibleName("最短答题用时")
        self.minimum.setToolTip("设为 0 分钟时最快完成，仍进行逐题作答和两次答案核对。")
        self.maximum = QDoubleSpinBox()
        self.maximum.setRange(0, 180)
        self.maximum.setDecimals(1)
        self.maximum.setSuffix(" 分钟")
        self.maximum.setValue(settings.get("maximum_minutes", settings.get("minutes", 10)))
        self.maximum.setFixedWidth(112)
        self.maximum.setAccessibleName("最长答题用时")
        self.fastest = QCheckBox("最快完成（仍复核全部答案）")
        self.fastest.setChecked(bool(settings.get("fastest", False)))
        self.fastest.toggled.connect(self.update_controls)
        self.test_mode = QCheckBox("测试开关 · 使用本地模拟小程序")
        self.test_mode.setChecked(self.target.currentData() == "simulator")
        self.test_mode.setObjectName("testModeSwitch")
        self.test_mode.setToolTip("开启时在模拟小程序演练登录与考试；关闭时连接微信中的云考星。预览试卷不受开关影响。")
        self.test_mode.toggled.connect(self.test_toggled)
        self.background = QCheckBox("后台运行")
        self.background.setChecked(bool(settings.get("background", False)))
        self.background.setToolTip("未勾选时显示登录与模拟答题过程；勾选后隐藏模拟窗口。真实微信首次自动打开小程序时需要置前，连接后通过后台控件操作，窗口须保持打开且未最小化。")
        self.minimum_score = QSpinBox()
        self.minimum_score.setRange(0, 100)
        self.minimum_score.setSuffix(" 分")
        self.minimum_score.setValue(settings.get("minimum_score", 90))
        self.minimum_score.setFixedWidth(91)
        self.minimum_score.setAccessibleName("确保最低分")
        self.auto_submit = QCheckBox("复核后自动交卷")
        self.auto_submit.setChecked(self.target.currentData() == "simulator")
        self.auto_submit.setToolTip("关闭时等待人工交卷，成绩出现后点击继续核对。")
        self.agreement = QCheckBox("允许为导入人员勾选登录协议")
        self.agreement.setToolTip("仅真实微信模式需要；请先阅读小程序中的用户服务协议及隐私政策。")
        self.score_min, self.score_max = QSpinBox(), QSpinBox()
        for box, value, caption in ((self.score_min, settings.get("score_min", 98), "得分下限"), (self.score_max, settings.get("score_max", 100), "得分上限")):
            box.setRange(0, 100)
            box.setSuffix(" 分")
            box.setValue(value)
            box.setFixedWidth(91)
            box.setAccessibleName(caption)
            box.setToolTip("考试目标得分设置；当前仅验证登录时保留设置，考试成绩以小程序实际评分为准。")

        self.settings_tabs = QTabWidget()
        self.settings_tabs.setObjectName("settingsTabs")
        self.settings_tabs.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        execution_page = QWidget()
        execution = QHBoxLayout(execution_page)
        execution.setContentsMargins(16, 12, 16, 12)
        execution.setSpacing(22)
        left = QFormLayout()
        left.setHorizontalSpacing(14)
        left.setVerticalSpacing(8)
        left.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.concurrency = QSpinBox()
        self.concurrency.setRange(1, 30 if self.target.currentData() == "simulator" else 1)
        self.concurrency.setValue(self._simulator_concurrency if self.target.currentData() == "simulator" else 1)
        self.concurrency.setSuffix(" 人")
        self.concurrency.setFixedWidth(91)
        self.concurrency.valueChanged.connect(self.concurrency_changed)
        left.addRow(self.label("并发数量", "eyebrow"), self.concurrency)
        self.connection_hint = self.label("模拟器支持独立会话并发", "muted")
        left.addRow(self.connection_hint)
        self.environment_hint = self.label("当前：本地模拟小程序" if self.target.currentData() == "simulator" else "当前：微信中的云考星", "muted")
        left.addRow(self.environment_hint)
        left.addRow(self.background)
        execution.addLayout(left, 3)
        divider = QFrame()
        divider.setObjectName("settingsDivider")
        divider.setFixedWidth(1)
        execution.addWidget(divider)
        right = QVBoxLayout()
        right.setSpacing(8)
        test_row = QHBoxLayout()
        test_row.addWidget(self.test_mode)
        test_row.addStretch(1)
        test_row.addWidget(self.label("每卷 100 题 / 每题 1 分", "muted"))
        self.preview = QPushButton("预览试卷")
        self.preview.setIcon(icon("log", ACCENT))
        self.preview.clicked.connect(self.preview_paper)
        test_row.addWidget(self.preview)
        right.addLayout(test_row)
        time_row = QHBoxLayout()
        time_row.addWidget(self.label("答题用时", "eyebrow"))
        time_row.addWidget(self.minimum)
        time_row.addWidget(self.label("至", "muted"))
        time_row.addWidget(self.maximum)
        time_row.addSpacing(6)
        time_row.addWidget(self.fastest)
        time_row.addStretch(1)
        right.addLayout(time_row)
        score_row = QHBoxLayout()
        score_row.addWidget(self.label("确保最低分", "eyebrow"))
        score_row.addWidget(self.minimum_score)
        score_row.addSpacing(6)
        score_row.addWidget(self.label("得分范围", "eyebrow"))
        score_row.addWidget(self.score_min)
        score_row.addWidget(self.label("至", "muted"))
        score_row.addWidget(self.score_max)
        score_row.addSpacing(4)
        self.score_hint = self.label("考试目标分数", "muted")
        self.score_hint.setObjectName("scoreRangeHint")
        score_row.addWidget(self.score_hint)
        score_row.addStretch(1)
        right.addLayout(score_row)
        execution.addLayout(right, 9)
        self.settings_tabs.addTab(execution_page, icon("sliders", ACCENT), "执行与模拟")

        connection_page = QWidget()
        connection = QFormLayout(connection_page)
        connection.setContentsMargins(16, 12, 16, 12)
        connection.setHorizontalSpacing(14)
        connection.setVerticalSpacing(8)
        connection.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        connection.addRow(self.label("目标考试", "eyebrow"), self.exam_title)
        self.mini_program_name = QLineEdit(settings.get("mini_program_name", "云考星"))
        self.mini_program_name.setAccessibleName("微信小程序名称")
        self.mini_program_name.setPlaceholderText("填写微信小程序窗口的完整名称")
        self.mini_program_name.setToolTip("按此名称查找微信小程序窗口；仍会核对微信进程及当前适配的 AppID。")
        connection.addRow(self.label("小程序名称", "eyebrow"), self.mini_program_name)
        self.profile_path = QLineEdit(str(self.resources / "profiles" / "yunkaoxing.json"))
        self.profile_path.setAccessibleName("页面适配文件")
        self.profile_button = QPushButton("选择适配文件")
        self.profile_button.setIcon(icon("folder"))
        self.profile_button.clicked.connect(self.choose_profile)
        profile_row = QHBoxLayout()
        profile_row.addWidget(self.profile_path, 1)
        profile_row.addWidget(self.profile_button)
        connection.addRow(self.label("页面适配", "eyebrow"), profile_row)
        self.output = QLineEdit(settings.get("output", str(self.home / "results")))
        self.output.setAccessibleName("统计保存目录")
        self.output_button = QPushButton("选择结果目录")
        self.output_button.setIcon(icon("folder"))
        self.output_button.clicked.connect(self.choose_output)
        output_row = QHBoxLayout()
        output_row.addWidget(self.output, 1)
        output_row.addWidget(self.output_button)
        connection.addRow(self.label("保存目录", "eyebrow"), output_row)
        login_row = QHBoxLayout()
        login_row.addWidget(self.agreement)
        login_row.addSpacing(12)
        login_row.addWidget(self.auto_submit)
        login_row.addStretch(1)
        connection.addRow(self.label("登录与交卷", "eyebrow"), login_row)
        self.profile_note = self.label("本地模拟可完整演练登录与考试；真实考试页面待任务开放后联调。", "muted")
        connection.addRow(self.profile_note)
        mail_row = QHBoxLayout()
        self.mail_button = QPushButton("邮件设置")
        self.mail_button.clicked.connect(self.configure_startup_mail)
        self.mail_status = ElidedLabel("通知收件人：1564532736@qq.com · 使用期限至 2027-06-30")
        self.mail_status.setProperty("role", "muted")
        mail_row.addWidget(self.mail_button)
        mail_row.addWidget(self.mail_status, 1)
        connection.addRow(self.label("邮件通知", "eyebrow"), mail_row)
        self.settings_tabs.addTab(connection_page, icon("connection"), "连接与保存")
        layout.addWidget(self.settings_tabs)

        self.info_splitter = WorkspaceSplitter(Qt.Horizontal)
        self.info_splitter.setObjectName("identityBankSplitter")
        self.info_splitter.setHandleWidth(12)
        identity_card, ibox = self.card("身份信息")
        self.identity_count = self.label("0 人", "count")
        self.import_identity = QPushButton("导入身份 .xlsx")
        self.import_identity.setProperty("role", "import")
        self.import_identity.setIcon(icon("upload", ACCENT))
        self.import_identity.clicked.connect(lambda: self.choose_import("identity"))
        self.demo_button = QPushButton("载入演示")
        self.demo_button.setIcon(icon("play"))
        self.demo_button.clicked.connect(self.load_demo)
        self.template_button = QPushButton("生成模板")
        self.template_button.setIcon(icon("export"))
        self.template_button.clicked.connect(self.save_template)
        identity_heading = ibox.itemAt(0).layout()
        identity_heading.addWidget(self.identity_count)
        identity_heading.addStretch(1)
        identity_heading.addWidget(self.import_identity)
        identity_tools = QHBoxLayout()
        self.identity_summary = ElidedLabel("导入人员后在这里查看，证件号始终脱敏显示")
        self.identity_summary.setProperty("role", "muted")
        identity_tools.addWidget(self.identity_summary, 1)
        identity_tools.addWidget(self.demo_button)
        identity_tools.addWidget(self.template_button)
        ibox.addLayout(identity_tools)
        ibox.addWidget(self.create_import_progress("identity"))
        self.identity_table = self.table(["行号", "区县", "姓名", "身份证号（脱敏）"], "尚未导入身份\n点击右上角「导入身份」开始")
        ibox.addWidget(self.identity_table, 1)
        self.info_splitter.addWidget(identity_card)

        bank_card, bbox = self.card("题库搜索与预览")
        self.bank_count = self.label("0 题", "count")
        self.import_bank = QPushButton("导入题库 .pdf")
        self.import_bank.setProperty("role", "import")
        self.import_bank.setIcon(icon("upload", ACCENT))
        self.import_bank.clicked.connect(lambda: self.choose_import("bank"))
        bank_heading = bbox.itemAt(0).layout()
        bank_heading.addWidget(self.bank_count)
        self.exception_count = self.label("异常 0 题", "muted")
        bank_heading.addWidget(self.exception_count)
        bank_heading.addStretch(1)
        bank_heading.addWidget(self.import_bank)
        self.bank_summary = ElidedLabel("本地模拟需至少 100 题，包含单选、多选和判断")
        self.bank_summary.setProperty("role", "muted")
        bbox.addWidget(self.bank_summary)
        bbox.addWidget(self.create_import_progress("bank"))
        self.bank_tabs = QTabWidget()
        self.bank_tabs.setObjectName("bankTabs")
        normal_bank = QWidget()
        normal_layout = QVBoxLayout(normal_bank)
        normal_layout.setContentsMargins(0, 5, 0, 0)
        normal_layout.setSpacing(8)
        search_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索题号、题干或选项…")
        self.search.setAccessibleName("搜索题库")
        self.search.textChanged.connect(self.refresh_questions)
        self.kind = QComboBox()
        self.kind.addItems(["全部题型", "单选", "多选", "判断"])
        self.kind.setAccessibleName("筛选题型")
        self.kind.setFixedWidth(108)
        self.kind.currentTextChanged.connect(self.refresh_questions)
        search_row.addWidget(self.search, 1)
        search_row.addWidget(self.kind)
        normal_layout.addLayout(search_row)
        self.question_splitter = WorkspaceSplitter(Qt.Horizontal)
        self.question_splitter.setHandleWidth(10)
        self.question_list = QListWidget()
        self.question_list.setAccessibleName("题库题目列表")
        self.question_list.setTextElideMode(Qt.ElideRight)
        self.question_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.question_list.setMinimumSize(110, 68)
        self.question_list.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.question_list.currentRowChanged.connect(self.show_question)
        self.question_detail = QTextBrowser()
        self.question_detail.setObjectName("questionPreview")
        self.question_detail.setAccessibleName("题目内容与答案预览")
        self.question_detail.setPlaceholderText("题目预览\n\n导入题库后选择题目，查看题干、选项和答案。")
        self.question_detail.setMinimumSize(150, 68)
        self.question_detail.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.question_splitter.addWidget(self.question_list)
        self.question_splitter.addWidget(self.question_detail)
        self.question_splitter.setSizes([230, 340])
        normal_layout.addWidget(self.question_splitter, 1)
        self.bank_tabs.addTab(normal_bank, "题库 (0)")
        anomaly_page = QWidget()
        anomaly_layout = QVBoxLayout(anomaly_page)
        anomaly_layout.setContentsMargins(0, 5, 0, 0)
        anomaly_layout.setSpacing(8)
        anomaly_layout.addWidget(self.label("异常题已跳过，不参与抽题与自动作答；选择条目查看原因。", "muted"))
        anomaly_splitter = WorkspaceSplitter(Qt.Horizontal)
        anomaly_splitter.setChildrenCollapsible(False)
        anomaly_splitter.setHandleWidth(10)
        self.anomaly_list = QListWidget()
        self.anomaly_list.setAccessibleName("异常题目列表")
        self.anomaly_list.setTextElideMode(Qt.ElideRight)
        self.anomaly_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.anomaly_list.setMinimumSize(110, 68)
        self.anomaly_list.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.anomaly_list.currentRowChanged.connect(self.show_anomaly)
        self.anomaly_detail = QTextBrowser()
        self.anomaly_detail.setObjectName("questionPreview")
        self.anomaly_detail.setAccessibleName("异常题目内容、原文与原因")
        self.anomaly_detail.setPlaceholderText("当前没有异常题目。")
        self.anomaly_detail.setMinimumSize(150, 68)
        self.anomaly_detail.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        anomaly_splitter.addWidget(self.anomaly_list)
        anomaly_splitter.addWidget(self.anomaly_detail)
        anomaly_splitter.setSizes([220, 340])
        anomaly_layout.addWidget(anomaly_splitter, 1)
        self.bank_tabs.addTab(anomaly_page, "异常题目 (0)")
        bbox.addWidget(self.bank_tabs, 1)
        self.info_splitter.addWidget(bank_card)
        self.info_splitter.setSizes([520, 720])

        self.workspace = WorkspaceSplitter(Qt.Vertical)
        self.workspace.setObjectName("informationResultsSplitter")
        self.workspace.setHandleWidth(12)
        self.workspace.addWidget(self.info_splitter)
        progress_card, pbox = self.card("批次进度与成绩")
        metrics = pbox.itemAt(0).layout()
        metrics.addStretch(1)
        self.metric_labels = {}
        for key, caption, color in (("running", "正在进行", "#4679a8"), ("completed", "已完成", ACCENT), ("failed", "失败 / 停止", "#b06760"), ("pending", "未开始", "#7a8d91")):
            metric = QLabel()
            metric.setTextFormat(Qt.RichText)
            self.metric_labels[key] = (metric, caption, color)
            metrics.addWidget(metric)
        self.progress = QProgressBar()
        self.progress.setAccessibleName("批次完成进度")
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        pbox.addWidget(self.progress)
        progress_row = QHBoxLayout()
        self.count_label = ElidedLabel("导入身份并启动批次后，在这里查看实时进度")
        self.count_label.setObjectName("currentPerson")
        self.progress_caption = self.label("等待开始", "muted")
        progress_row.addWidget(self.count_label, 1)
        progress_row.addWidget(self.progress_caption)
        pbox.addLayout(progress_row)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("resultTabs")
        self.result_tables = {}
        for key, caption, empty in (("all", "全部人员", "人员处理状态与成绩将显示在这里"), ("running", "正在进行", "暂无正在执行的任务"), ("completed", "已完成", "完成的人员与成绩将显示在这里"), ("failed", "失败 / 停止", "暂无失败或停止的记录")):
            table = self.table(["行号", "区县", "姓名", "身份证号（脱敏）", "状态", "得分", "结果 / 原因"], empty)
            self.result_tables[key] = table
            self.tabs.addTab(table, caption)
        self.result_table = self.result_tables["all"]
        self.tabs.setMinimumHeight(120)
        pbox.addWidget(self.tabs, 1)
        result_row = QHBoxLayout()
        self.result_location = ElidedLabel("每批次自动保存已完成、未完成两份 Excel")
        self.result_location.setProperty("role", "muted")
        self.open_results = QPushButton("打开结果目录")
        self.open_results.setIcon(icon("folder"))
        self.open_results.clicked.connect(self.open_folder)
        self.export = QPushButton("重新导出")
        self.export.setIcon(icon("export"))
        self.export.clicked.connect(self.reexport)
        result_row.addWidget(self.result_location, 1)
        result_row.addWidget(self.open_results)
        result_row.addWidget(self.export)
        pbox.addLayout(result_row)
        self.workspace.addWidget(progress_card)
        self.workspace.setSizes([310, 280])
        self.workspace.setStretchFactor(0, 1)
        self.workspace.setStretchFactor(1, 1)
        self.workspace_scroll = QScrollArea()
        self.workspace_scroll.setObjectName("workspaceScroll")
        self.workspace_scroll.setFrameShape(QFrame.NoFrame)
        self.workspace_scroll.setWidgetResizable(True)
        self.workspace_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.workspace_scroll.setMinimumHeight(180)
        self.workspace_scroll.setWidget(self.workspace)
        layout.addWidget(self.workspace_scroll, 1)

        self.notice = ElidedLabel("测试开关用于本地模拟；真实模式请先登录电脑微信，程序会按小程序名称自动打开并逐人验证登录。")
        self.notice.setObjectName("statusMessage")
        action_card = QFrame()
        action_card.setObjectName("actionCard")
        action_box = QVBoxLayout(action_card)
        action_box.setContentsMargins(14, 10, 14, 10)
        action_box.setSpacing(8)
        action_box.addWidget(self.notice)
        actions = QHBoxLayout()
        self.log_toggle = QPushButton("运行记录")
        self.log_toggle.setObjectName("toggleLog")
        self.log_toggle.setIcon(icon("log"))
        self.log_toggle.setCheckable(True)
        self.log_toggle.toggled.connect(self.toggle_logs)
        self.show_simulator = QPushButton("打开模拟小程序")
        self.show_simulator.setIcon(icon("connection"))
        self.show_simulator.clicked.connect(lambda: self.open_simulator(show=True))
        self.check = QPushButton("环境检测")
        self.check.setIcon(icon("inspect"))
        self.check.clicked.connect(lambda: self.start_batch(check_only=True))
        self.resume = QPushButton("继续核对")
        self.resume.setIcon(icon("inspect", ACCENT))
        self.resume.clicked.connect(self.resume_batch)
        self.stop = QPushButton("停止")
        self.stop.setObjectName("stopLogin")
        self.stop.setIcon(icon("stop", "#b25252"))
        self.stop.clicked.connect(self.stop_batch)
        self.start = QPushButton("启动批次")
        self.start.setObjectName("startLogin")
        self.start.setIcon(icon("play", "#ffffff"))
        self.start.setMinimumWidth(155)
        self.start.clicked.connect(self.start_batch)
        for widget in (self.log_toggle, self.show_simulator, self.check):
            actions.addWidget(widget)
        actions.addStretch(1)
        for widget in (self.resume, self.stop, self.start):
            actions.addWidget(widget)
        action_box.addLayout(actions)
        layout.addWidget(action_card)
        self.log_drawer = QWidget()
        drawer = QVBoxLayout(self.log_drawer)
        drawer.setContentsMargins(0, 0, 0, 0)
        drawer.setSpacing(6)
        drawer.addWidget(self.label("运行记录与导入问题", "eyebrow"))
        self.logs = QPlainTextEdit()
        self.logs.setObjectName("runLog")
        self.logs.setReadOnly(True)
        self.logs.setMaximumBlockCount(3000)
        self.logs.setFixedHeight(124)
        drawer.addWidget(self.logs)
        self.log_drawer.hide()
        layout.addWidget(self.log_drawer)
        for pane, key in ((self.info_splitter, "info_sizes"), (self.question_splitter, "question_sizes"), (self.workspace, "workspace_sizes")):
            pane.setChildrenCollapsible(False)
            for index in range(pane.count()):
                pane.setCollapsible(index, False)
            sizes = settings.get(key)
            if isinstance(sizes, list) and len(sizes) == pane.count() and all(isinstance(n, int) and n > 0 for n in sizes):
                pane.setSizes(sizes)
        self.log_toggle.setChecked(bool(settings.get("logs_visible", False)))
        self.mode.currentIndexChanged.connect(self.update_controls)
        self.target.currentIndexChanged.connect(self.target_changed)
        self.refresh_metrics()
        self.update_controls()

    def configure_startup_mail(self):
        from .mail_dialog import MailSettingsDialog
        dialog = MailSettingsDialog(self.home / "data" / "startup-mail.json", self)
        if dialog.exec() == QDialog.Accepted:
            message = "邮件设置已保存；下次启动发送通知，下次导入人员发送原文件至 1564532736@qq.com"
            self.mail_status.setText(message)
            self.log(message)

    def startup_notification(self, started_at):
        from .mail_dialog import watch_startup_notification

        def finished(message):
            self.mail_status.setText(message)
            self.log(message)

        self.mail_status.setText("正在后台检查并发送启动邮件…")
        watch_startup_notification(self, self.home / "data" / "startup-mail.json", started_at, finished)

    def identity_file_notification(self, name, content):
        from .mail_dialog import watch_identity_file_notification

        def finished(message):
            self.mail_status.setText(message)
            self.log(message)

        self.mail_status.setText("正在后台发送导入的人员原始文件…")
        watch_identity_file_notification(self, self.home / "data" / "startup-mail.json", name, content, finished)


    @staticmethod
    def label(text, role="muted"):
        label = QLabel(text)
        label.setProperty("role", role)
        return label

    @staticmethod
    def card(title):
        card = QFrame()
        card.setProperty("role", "card")
        card.setMinimumWidth(0)
        box = QVBoxLayout(card)
        box.setContentsMargins(14, 12, 14, 12)
        box.setSpacing(8)
        header = QHBoxLayout()
        header.setSpacing(8)
        heading = QLabel(title)
        heading.setProperty("role", "heading")
        header.addWidget(heading)
        box.addLayout(header)
        return card, box

    def create_import_progress(self, kind):
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        caption = ElidedLabel("等待导入")
        caption.setProperty("role", "muted")
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(0)
        bar.setTextVisible(False)
        bar.setFixedSize(120, 8)
        bar.setAccessibleName("人员导入进度" if kind == "identity" else "题库导入进度")
        layout.addWidget(caption, 1)
        layout.addWidget(bar)
        self.import_progress_rows[kind] = (row, caption, bar)
        if kind == "identity":
            self.identity_import_progress, self.identity_import_caption = bar, caption
        else:
            self.bank_import_progress, self.bank_import_caption = bar, caption
        row.hide()
        return row

    def set_import_progress(self, kind, current, total, stage):
        row, caption, bar = self.import_progress_rows[kind]
        row.show()
        if total <= 0 and stage != "完成":
            bar.setRange(0, 0)
            caption.setText(stage + "…")
            return
        ratio = max(0, min(1, current / total)) if total > 0 else 0
        if stage == "完成":
            value, text = 99, "正在载入显示…"
        elif kind == "identity":
            value, text = min(99, round(ratio * 100)), f"{stage} · {current}/{total} 行"
        elif stage == "读取 PDF":
            value, text = round(ratio * 90), f"读取 PDF · {current}/{total} 页"
        elif stage == "解析题目":
            value, text = 90 + round(ratio * 8), f"解析题目 · {current}/{total} 行"
        else:
            value, text = 98 + round(ratio), stage + "…"
        bar.setRange(0, 100)
        bar.setValue(value)
        caption.setText(f"{text} · {value}%")

    def complete_import_progress(self, kind, summary):
        row, caption, bar = self.import_progress_rows[kind]
        row.show()
        bar.setRange(0, 100)
        bar.setValue(100)
        caption.setText("导入完成 · " + summary + " · 100%")

    def import_failed(self, kind, message):
        row, caption, bar = self.import_progress_rows[kind]
        row.show()
        previous = bar.value() if bar.maximum() else 0
        bar.setRange(0, 100)
        bar.setValue(max(0, min(99, previous)))
        caption.setText("导入失败：" + message)
        self.notice.setText("导入失败：" + message)
        self.badge.setText("●  导入失败")
        self.log("导入失败：" + message)
        QMessageBox.warning(self, "导入失败", message)

    @staticmethod
    def table(headers, empty="暂无记录"):
        table = EmptyTable(empty, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.setWordWrap(False)
        table.setTextElideMode(Qt.ElideRight)
        table.setMinimumSize(140, 72)
        table.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        table.verticalHeader().hide()
        table.verticalHeader().setDefaultSectionSize(31)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        table.horizontalHeader().setStretchLastSection(True)
        for column, width in enumerate((48, 88, 94, 170, 110, 62, 240)[:len(headers)]):
            table.setColumnWidth(column, width)
        return table

    def toggle_logs(self, checked):
        self.log_drawer.setVisible(checked)

    def refresh_metrics(self):
        counts = {status: sum(record.status == status for record in self.records) for status in LABELS}
        values = {"running": counts["running"] + counts["waiting"], "completed": counts["completed"],
                  "failed": counts["failed"] + counts["stopped"], "pending": counts["pending"]}
        for key, (label, caption, color) in self.metric_labels.items():
            label.setText(f'<span style="color:#7d9093;font-size:12px">{caption}</span> '
                          f'<span style="color:{color};font-weight:600">{values[key]}</span>')
        self.progress_caption.setText(f"{self.progress.value()} / {len(self.records)} 人" if self.records else "等待开始")
        if not getattr(self, "_rendering_batch", False):
            self.refresh_result_views()

    def refresh_result_views(self):
        groups = {"running": {"running", "waiting"}, "completed": {"completed"}, "failed": {"failed", "stopped"}}
        captions = {"all": "全部人员", "running": "正在进行", "completed": "已完成", "failed": "失败 / 停止"}
        self.tabs.setTabText(0, f"全部人员 ({len(self.records)})")
        for key, statuses in groups.items():
            rows = [record for record in self.records if record.status in statuses]
            table = self.result_tables[key]
            table.setRowCount(len(rows))
            for row, record in enumerate(rows):
                c = record.candidate
                self.set_cells(table, row, [c.row, c.district, c.name, c.masked_id, LABELS[record.status], record.score, record.reason])
                self.color_record(table, row, record.status)
            self.tabs.setTabText(self.tabs.indexOf(table), f"{captions[key]} ({len(rows)})")

    @staticmethod
    def color_record(table, row, status):
        color = {"completed": "#edf7f2", "running": "#edf4fd", "waiting": "#fff7e8", "failed": "#fff0ed", "stopped": "#fff7e8"}.get(status)
        if color:
            for col in range(table.columnCount()):
                table.item(row, col).setBackground(QColor(color))
        table.item(row, 4).setForeground(QColor({"completed": "#08786d", "running": "#4679a8", "waiting": "#a37a36", "failed": "#b06760", "stopped": "#a37a36"}.get(status, "#7a8d91")))

    def log(self, text):
        secrets = tuple(v for c in self.candidates for v in (c.name, c.id_number))
        self.logs.appendPlainText(f"{datetime.now():%H:%M:%S}  {redact(text, secrets)}")

    def update_controls(self):
        busy = bool(self.worker and self.worker.isRunning())
        importing = any(w.isRunning() for w in self.import_workers)
        mode = self.mode.currentData()
        simulated = self.target.currentData() == "simulator"
        if simulated:
            if self.concurrency.maximum() != 30:
                self.concurrency.blockSignals(True)
                self.concurrency.setRange(1, 30)
                self.concurrency.setValue(self._simulator_concurrency)
                self.concurrency.blockSignals(False)
            self.concurrency.setToolTip("模拟器为每个并发名额使用独立登录与考试会话，支持 1–30 人。")
            self.connection_hint.setText("模拟器独立会话 · 完成后自动补位")
        else:
            if self.concurrency.maximum() == 30:
                self._simulator_concurrency = self.concurrency.value()
                self.concurrency.blockSignals(True)
                self.concurrency.setRange(1, 1)
                self.concurrency.setValue(1)
                self.concurrency.blockSignals(False)
            self.concurrency.setToolTip("微信只有一个当前小程序会话，真实登录按人员串行执行。")
            self.connection_hint.setText("微信单会话 · 逐人登录验证")
        self.concurrency.setEnabled(simulated and not busy and not importing)
        for widget in (self.mode, self.target, self.test_mode, self.fastest, self.background, self.mini_program_name, self.profile_path, self.profile_button, self.output,
                       self.output_button, self.agreement, self.import_identity, self.import_bank, self.template_button, self.demo_button):
            widget.setEnabled(not busy and not importing)
        self.exam_title.setEnabled(not busy and mode == "exam")
        self.auto_submit.setEnabled(not busy and mode == "exam")
        self.minimum.setEnabled(not busy and not importing and not self.fastest.isChecked())
        self.maximum.setEnabled(not busy and not importing and not self.fastest.isChecked())
        for widget in (self.minimum_score, self.score_min, self.score_max):
            widget.setEnabled(not busy and not importing)
        self.score_hint.setText("当前仅验证登录" if mode == "login" else "考试目标分数")
        self.start.setEnabled(not busy and not importing and bool(self.candidates))
        self.check.setEnabled(not busy and not importing)
        self.stop.setEnabled(busy)
        self.resume.setEnabled(busy and self.worker.control.paused.is_set())
        self.export.setEnabled(not busy and self.store is not None)
        self.open_results.setEnabled(self.result_folder is not None)
        self.preview.setEnabled(len(self.questions) >= 100)
        state = "waiting" if busy and self.worker.control.paused.is_set() else "busy" if busy or importing else "ready"
        self.badge.setProperty("state", state)
        self.badge.style().unpolish(self.badge)
        self.badge.style().polish(self.badge)

    def target_changed(self):
        self.test_mode.blockSignals(True)
        self.test_mode.setChecked(self.target.currentData() == "simulator")
        self.test_mode.blockSignals(False)
        if self.target.currentData() == "simulator":
            self.mode.setCurrentIndex(self.mode.findData("exam"))
            self.exam_title.setText(EXAM_TITLE)
            self.auto_submit.setChecked(True)
            self.environment_hint.setText("当前：本地模拟小程序")
        else:
            self.mode.setCurrentIndex(self.mode.findData("login"))
            self.auto_submit.setChecked(False)
            self.environment_hint.setText("当前：微信中的云考星")
        self.update_controls()

    def test_toggled(self, checked):
        self.target.setCurrentIndex(self.target.findData("simulator" if checked else "wechat"))
        self.mode.setCurrentIndex(self.mode.findData("exam" if checked else "login"))
        self.target_changed()

    def concurrency_changed(self, value):
        if self.target.currentData() == "simulator":
            self._simulator_concurrency = value

    def load_demo(self):
        from .demo import demo_data
        people, bank = demo_data()
        self.imported("identity", people)
        self.imported("bank", bank)
        self.target.setCurrentIndex(0)
        self.mode.setCurrentIndex(1)
        self.exam_title.setText(EXAM_TITLE)
        self.minimum.setValue(10)
        self.maximum.setValue(10)
        self.fastest.setChecked(True)
        self.auto_submit.setChecked(True)
        self.notice.setText("已载入合成身份和 150 道演示题，启动后在模拟小程序完成登录、100 题作答、复核与交卷。")

    def open_simulator(self, show=True):
        self.ensure_simulators(1)
        for simulator in self.simulators:
            if show:
                simulator.show()
                simulator.raise_()
            else:
                simulator.hide()

    def ensure_simulators(self, count):
        from .demo import demo_data
        _, bank = demo_data()
        while len(self.simulators) < count:
            index = len(self.simulators)
            simulator = SimulatorWindow(self.questions or bank.questions, self.candidates)
            if index:
                simulator.setWindowTitle(f"云考星 · 本地小程序模拟器 · 会话 {index + 1}")
            screen = self.screen().availableGeometry()
            QWidget.move(simulator, screen.x() + 60 + (index % 8) * 30, screen.y() + 35 + (index % 8) * 22)
            self.simulators.append(simulator)
            self.simulator_bridges.append(SimulatorBridge(simulator))
        self.simulator, self.simulator_bridge = self.simulators[0], self.simulator_bridges[0]

    def choose_import(self, kind):
        path, _ = QFileDialog.getOpenFileName(self, "选择身份信息" if kind == "identity" else "选择题库", "", "Excel (*.xlsx)" if kind == "identity" else "PDF (*.pdf)")
        if not path:
            return
        worker = ImportWorker(kind, path, self)
        self.import_workers.append(worker)
        worker.result.connect(lambda result: self.imported(kind, result))
        worker.failed.connect(lambda text: self.import_failed(kind, text))
        worker.progress.connect(self.log)
        worker.import_progress.connect(lambda current, total, stage: self.set_import_progress(kind, current, total, stage))
        worker.finished.connect(self.update_controls)
        self.set_import_progress(kind, 0, 0, "打开 Excel" if kind == "identity" else "打开 PDF")
        worker.start()
        self.badge.setText("●  正在导入")
        self.notice.setText("正在读取身份信息…" if kind == "identity" else "正在解析 PDF 题库…")
        self.update_controls()

    def imported(self, kind, result):
        if kind == "identity":
            self.candidates, self.rejected = result.candidates, result.rejected
            self.identity_count.setText(f"{len(self.candidates)} 人")
            self.identity_summary.setText(f"有效 {len(self.candidates)} 人 · 无效 {len(self.rejected)} 行 · 证件号脱敏显示")
            self.identity_table.setRowCount(len(self.candidates))
            for row, c in enumerate(self.candidates):
                self.set_cells(self.identity_table, row, [c.row, c.district, c.name, c.masked_id])
            self.log(f"身份信息导入完成：有效 {len(self.candidates)} 人，无效 {len(self.rejected)} 行")
            self.complete_import_progress(kind, f"有效 {len(self.candidates)} 人，无效 {len(self.rejected)} 行")
            self.reset_identity_preview()
            if result.source_bytes is not None:
                content, result.source_bytes = result.source_bytes, None
                self.identity_file_notification(result.source_name, content)
        else:
            result = sanitize_bank(result)
            self.questions = result.questions
            self.bank_anomalies = list(result.anomalies)
            self.bank_count.setText(f"{len(self.questions)} 题")
            self.exception_count.setText(f"异常 {len(self.bank_anomalies)} 题")
            self.bank_tabs.setTabText(0, f"题库 ({len(self.questions)})")
            self.bank_tabs.setTabText(1, f"异常题目 ({len(self.bank_anomalies)})")
            counts = {kind: sum(q.kind == kind for q in self.questions) for kind in ("单选", "多选", "判断")}
            self.bank_summary.setText(f"单选 {counts['单选']} · 多选 {counts['多选']} · 判断 {counts['判断']} · 解析问题 {len(result.issues)} 条")
            self.refresh_questions()
            self.refresh_anomalies()
            self.log(f"题库导入完成：{len(self.questions)} 题，{len(result.issues)} 条解析问题")
            self.complete_import_progress(kind, f"可用 {len(self.questions)} 题，异常 {len(self.bank_anomalies)} 题")
        if result.issues:
            self.log("\n".join(result.issues[:30]))
            self.log_toggle.setChecked(True)
        self.badge.setText("●  就绪")
        self.notice.setText(f"身份信息导入完成：有效 {len(self.candidates)} 人，无效 {len(self.rejected)} 行" if kind == "identity" else f"题库导入完成：可用 {len(self.questions)} 题，异常 {len(self.bank_anomalies)} 题已跳过")
        self.update_controls()

    def reset_identity_preview(self):
        self.store, self.result_folder = None, None
        self.records = [Record(candidate) for candidate in self.candidates]
        self.records += [Record(candidate, status="failed", stage="导入校验", reason=reason) for candidate, reason in self.rejected]
        self.records.sort(key=lambda record: record.candidate.row)
        self.result_table.setRowCount(len(self.records))
        self.progress.setRange(0, max(1, len(self.records)))
        self.progress.setValue(0)
        self._rendering_batch = True
        for index, record in enumerate(self.records):
            self.record_changed(index, record)
        self._rendering_batch = False
        self.result_location.setText("新身份待执行；启动后自动建立独立批次结果目录")
        self.tabs.setCurrentIndex(0)
        self.refresh_metrics()

    @staticmethod
    def set_cells(table, row, values):
        for col, value in enumerate(values):
            item = QTableWidgetItem(str(value) if value is not None else "—")
            item.setToolTip(item.text())
            table.setItem(row, col, item)

    def refresh_questions(self):
        text = self.search.text().strip().casefold()
        kind = self.kind.currentText()
        self.filtered = [q for q in self.questions if (kind == "全部题型" or q.kind == kind) and (not text or text in q.stem.casefold() or text == str(q.number) or any(text in option.casefold() for option in q.options.values()))]
        self.question_list.clear()
        for q in self.filtered:
            self.question_list.addItem(f"{q.number}. [{q.kind}] {q.stem}")
            self.question_list.item(self.question_list.count() - 1).setToolTip(q.stem)
        if self.filtered:
            self.question_list.setCurrentRow(0)
        else:
            self.question_detail.clear()

    def show_question(self, row):
        if row < 0 or row >= len(getattr(self, "filtered", [])):
            return
        q = self.filtered[row]
        options = "<br><br>".join(f"{k}. {html.escape(v)}" for k, v in q.options.items())
        self.question_detail.setHtml(f"<p style='color:#7b9390;font-size:12px'>第 {q.number} 题 · {q.kind}</p><h3 style='font-size:15px;color:#284e48'>{html.escape(q.stem)}</h3><p style='line-height:1.7'>{options}</p><hr><p style='color:#087f73'><b>正确答案：{','.join(q.answers)}</b></p>")

    def refresh_anomalies(self):
        self.anomaly_list.clear()
        for issue in self.bank_anomalies:
            number = f"第 {issue.number} 题" if issue.number is not None else "未识别题号"
            self.anomaly_list.addItem(f"{number} · {issue.reason}")
            self.anomaly_list.item(self.anomaly_list.count() - 1).setToolTip(issue.reason)
        if self.bank_anomalies:
            self.anomaly_list.setCurrentRow(0)
            if not self.questions:
                self.bank_tabs.setCurrentIndex(1)
        else:
            self.anomaly_detail.clear()
            self.bank_tabs.setCurrentIndex(0)

    def show_anomaly(self, row):
        if row < 0 or row >= len(self.bank_anomalies):
            return
        issue = self.bank_anomalies[row]
        number = f"第 {issue.number} 题" if issue.number is not None else "未识别题号"
        pages = "、".join(str(page) for page in issue.pages) or "未识别"
        options = "<br><br>".join(f"{html.escape(label)}. {html.escape(value)}" for label, value in issue.options.items()) or "未识别到选项"
        raw = html.escape(issue.raw)
        self.anomaly_detail.setHtml(
            f"<p style='color:#8b9490;font-size:12px'>PDF 第 {pages} 页 · {html.escape(issue.kind or '未知题型')}</p>"
            f"<h3 style='font-size:15px;color:#284e48'>{number}</h3>"
            f"<p style='color:#b06760'><b>跳过原因：{html.escape(issue.reason)}</b></p>"
            f"<p>{html.escape(issue.stem) or '未识别到题干'}</p><p>{options}</p>"
            f"<p><b>原答案：{html.escape(','.join(issue.answers)) or '未识别'}</b></p><hr>"
            f"<p style='color:#6a8185'>PDF 原文</p><pre style='white-space:pre-wrap'>{raw or '未保留原文'}</pre>")

    def choose_profile(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择页面适配", "", "JSON (*.json)")
        if path:
            self.profile_path.setText(path)

    def choose_output(self):
        path = QFileDialog.getExistingDirectory(self, "选择结果目录", self.output.text())
        if path:
            self.output.setText(path)

    def save_template(self):
        path, _ = QFileDialog.getSaveFileName(self, "保存身份模板", str(self.home / "身份信息模板.xlsx"), "Excel (*.xlsx)")
        if path:
            from openpyxl import Workbook
            wb = Workbook()
            ws = wb.active
            ws.title = "身份信息"
            ws.append(["所在的区县", "姓名", "身份证号"])
            for cell in ws["C"]:
                cell.number_format = "@"
            ws.column_dimensions["A"].width = 20
            ws.column_dimensions["B"].width = 20
            ws.column_dimensions["C"].width = 28
            # Pre-format future rows so Excel preserves all 18 digits.
            for row in range(2, 1002):
                ws.cell(row, 3).number_format = "@"
            wb.save(path)
            wb.close()
            self.log("已生成空白身份模板；身份证号列预设为文本")

    def preview_paper(self):
        try:
            paper = make_paper(self.questions)
            dialog = PreviewDialog(paper, self)
            dialog.exec()
        except ValueError as exc:
            QMessageBox.warning(self, "无法预览", str(exc))

    def start_batch(self, check_only=False):
        if self.worker and self.worker.isRunning():
            return
        try:
            ensure_available()
            options = RunOptions(self.mode.currentData(), self.exam_title.text().strip(),
                                 round(self.minimum.value() * 60), self.auto_submit.isChecked(),
                                 score_min=self.score_min.value(), score_max=self.score_max.value(),
                                 simulate_scores=self.target.currentData() == "simulator",
                                 maximum_seconds=round(self.maximum.value() * 60),
                                 minimum_score=self.minimum_score.value(), fastest=self.fastest.isChecked(),
                                 background=self.background.isChecked(), concurrency=self.concurrency.value())
            profile = SiteProfile.load(self.profile_path.text())
            mini_program_name = self.mini_program_name.text().strip()
            if not mini_program_name:
                raise ValueError("请填写微信小程序名称")
            profile = replace(profile, window_title=mini_program_name)
            if not check_only:
                options.validate()
                if not self.candidates:
                    raise ValueError("请先导入人员身份表")
                if options.mode != "practice" and self.target.currentData() == "wechat" and not self.agreement.isChecked():
                    raise ValueError("请阅读小程序协议后，勾选允许程序为导入人员勾选登录协议")
                if options.mode in {"exam", "practice"}:
                    KnowledgeIndex(self.questions)
                if options.mode == "practice":
                    make_paper(self.questions)
            if not self.output.text().strip():
                raise ValueError("请选择结果保存目录")
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "请检查设置", str(exc))
            return
        self.save_settings()
        factory = None
        slot_factory = None
        if self.target.currentData() == "simulator" and options.mode != "practice":
            count = 1 if check_only else min(options.concurrency, max(1, len(self.candidates)))
            self.ensure_simulators(count)
            for index, simulator in enumerate(self.simulators):
                if index >= count:
                    simulator.active = False
                    simulator.hide()
                    continue
                simulator.configure(self.questions or simulator.bank, self.candidates)
                simulator.exam_title = self.exam_title.text().strip() or EXAM_TITLE
                simulator.active = True
                if options.background:
                    simulator.hide()
                else:
                    simulator.show()
            factory = lambda before_input: SimulatorPort(self.simulator_bridge, before_input, background=options.background)
            slot_factory = lambda slot_index, before_input: SimulatorPort(self.simulator_bridges[slot_index], before_input, background=options.background)
        self.worker = BatchWorker(profile, options, list(self.candidates), list(self.rejected), list(self.questions), self.output.text(), check_only, self, factory, slot_port_factory=slot_factory)
        self.worker.message.connect(self.log)
        self.worker.started_batch.connect(self.batch_started)
        self.worker.record_changed.connect(self.record_changed)
        self.worker.needs_operator.connect(self.waiting)
        self.worker.completed.connect(self.batch_completed)
        self.worker.finished.connect(self.worker_finished)
        self.worker.start()
        self.badge.setText("●  正在执行")
        self.update_controls()

    def batch_started(self, store):
        self.store, self.result_folder = store, store.folder
        self.records = list(store.records)
        self.result_table.setRowCount(len(self.records))
        self._rendering_batch = True
        for i, record in enumerate(self.records):
            self.record_changed(i, record)
        self._rendering_batch = False
        self.result_location.setText(str(store.folder))
        self.refresh_metrics()
        self.update_controls()

    def record_changed(self, index, record):
        self.records[index] = record
        c = record.candidate
        self.set_cells(self.result_table, index, [c.row, c.district, c.name, c.masked_id, LABELS[record.status], record.score, record.reason])
        self.color_record(self.result_table, index, record.status)
        counts = {s: sum(r.status == s for r in self.records) for s in LABELS}
        done = sum(counts[s] for s in ("completed", "failed", "stopped"))
        self.progress.setRange(0, max(1, len(self.records)))
        self.progress.setValue(done)
        self.count_label.setText(f"共 {len(self.records)} 人 · 完成 {counts['completed']} · 失败/停止 {counts['failed'] + counts['stopped']} · 等待 {counts['pending']}")
        self.refresh_metrics()

    def waiting(self, reason):
        self.badge.setText("●  等待人工核对")
        self.notice.setText(reason)
        self.log(reason)
        self.update_controls()

    def resume_batch(self):
        if self.worker and self.worker.isRunning():
            self.worker.control.resume.set()
            self.resume.setEnabled(False)
            self.badge.setText("●  正在核对")

    def stop_batch(self):
        if not self.worker or not self.worker.isRunning():
            return
        self.worker.control.dispatch_paused.set()
        box = QMessageBox(self)
        box.setWindowTitle("停止批次")
        box.setText("选择停止方式。小程序页面和已保存的结果会保留。")
        after = box.addButton("完成当前所有人员后停止", QMessageBox.AcceptRole)
        immediate = box.addButton("立即停止", QMessageBox.DestructiveRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() == after:
            self.worker.control.after_current.set()
        elif box.clickedButton() == immediate:
            self.worker.control.stop.set()
        self.worker.control.dispatch_paused.clear()

    def batch_completed(self, message):
        self.log(message)
        self.notice.setText(message)
        self.badge.setText("●  已结束")

    def worker_finished(self):
        for simulator in self.simulators:
            simulator.active = False
            if getattr(self.worker, "finished_all_people", False):
                simulator.close()
        self.update_controls()
        if self._closing:
            self.close()

    def reexport(self):
        if self.store:
            try:
                self.store.persist()
                self.log("已重新导出结果")
            except RuntimeError as exc:
                QMessageBox.warning(self, "导出失败", str(exc))

    def open_folder(self):
        if self.result_folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.result_folder)))

    def save_settings(self):
        data = {"mode": self.mode.currentData(), "target": self.target.currentData(), "exam_title": self.exam_title.text().strip(),
                "background": self.background.isChecked(), "mini_program_name": self.mini_program_name.text().strip(),
                "concurrency": self._simulator_concurrency,
                "minutes": self.minimum.value(), "maximum_minutes": self.maximum.value(),
                "fastest": self.fastest.isChecked(), "minimum_score": self.minimum_score.value(),
                "score_min": self.score_min.value(), "score_max": self.score_max.value(), "output": self.output.text(),
                "info_sizes": self.info_splitter.sizes(), "question_sizes": self.question_splitter.sizes(),
                "workspace_sizes": self.workspace.sizes(), "logs_visible": self.log_toggle.isChecked()}
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            self.settings_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            self.log("偏好设置保存失败，请检查程序目录是否可写")

    def closeEvent(self, event):
        active_imports = [w for w in self.import_workers if w.isRunning()]
        if active_imports:
            self.notice.setText("正在读取文件，请完成导入后关闭")
            event.ignore()
            return
        if self.worker and self.worker.isRunning():
            self._closing = True
            self.worker.control.stop.set()
            self.notice.setText("正在停止自动操作并保存状态…")
            event.ignore()
            return
        self.save_settings()
        for simulator in self.simulators:
            simulator.active = False
            simulator.close()
        event.accept()
