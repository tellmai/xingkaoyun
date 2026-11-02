"""Shared presentation for the desktop workspace; no workflow state lives here."""

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QLabel, QSplitter, QSplitterHandle, QTableWidget


ACCENT = "#087f73"
UI_ASSETS = Path(__file__).resolve().parent / "assets/ui"


def icon(name, color="#536b70"):
    """Small vector icons rendered at native and high DPI sizes."""
    result = QIcon()
    for size in (16, 20, 24, 32, 40, 48, 64):
        pixmap = QPixmap(size * 2, size * 2)
        pixmap.setDevicePixelRatio(2)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.scale(size / 24, size / 24)
        painter.setPen(QPen(QColor(color), 1.7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))

        def line(x1, y1, x2, y2):
            painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        if name == "brand":
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(ACCENT))
            painter.drawRoundedRect(QRectF(0, 0, 24, 24), 6, 6)
            painter.setPen(QPen(QColor("#ffffff"), 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(QRectF(6, 5, 12, 14), 2, 2)
            line(9, 9, 15, 9)
            line(9, 13, 11, 15)
            line(11, 15, 15, 11.5)
        elif name == "play":
            path = QPainterPath(QPointF(9, 5))
            path.lineTo(18, 12)
            path.lineTo(9, 19)
            path.closeSubpath()
            painter.setBrush(QColor(color))
            painter.drawPath(path)
        elif name == "stop":
            painter.setBrush(QColor(color))
            painter.drawRoundedRect(QRectF(6, 6, 12, 12), 2, 2)
        elif name == "folder":
            path = QPainterPath(QPointF(3, 7))
            path.lineTo(3, 19)
            path.lineTo(21, 19)
            path.lineTo(21, 8)
            path.lineTo(12, 8)
            path.lineTo(10, 5)
            path.lineTo(3, 5)
            path.closeSubpath()
            painter.drawPath(path)
        elif name in ("upload", "export"):
            line(12, 16, 12, 4)
            line(8, 8, 12, 4)
            line(16, 8, 12, 4)
            line(4, 14, 4, 20)
            line(4, 20, 20, 20)
            line(20, 20, 20, 14)
        elif name == "inspect":
            painter.drawEllipse(QRectF(4, 3, 13, 13))
            line(15, 15, 21, 21)
            line(8, 9, 10, 11)
            line(10, 11, 14, 7)
        elif name == "connection":
            painter.drawEllipse(QRectF(3, 3, 18, 18))
            painter.drawEllipse(QRectF(8, 3, 8, 18))
            line(3, 12, 21, 12)
        elif name == "sliders":
            for y, x in ((6, 9), (12, 16), (18, 7)):
                line(3, y, x - 2, y)
                line(x + 2, y, 21, y)
                painter.drawEllipse(QRectF(x - 2, y - 2, 4, 4))
        elif name == "log":
            painter.drawRoundedRect(QRectF(4, 3, 16, 18), 2, 2)
            for y in (8, 12, 16):
                line(8, y, 16, y)
        painter.end()
        result.addPixmap(pixmap)
    return result


class ElidedLabel(QLabel):
    """Keep paths/status messages on one line, with the full text in a tooltip."""

    def __init__(self, text="", parent=None):
        self._full_text = ""
        super().__init__(parent)
        self.setMinimumWidth(0)
        self.setText(text)

    def setText(self, text):
        self._full_text = text
        self.setToolTip(text)
        self.setAccessibleName(text)
        self._elide()

    def text(self):
        return self._full_text

    def _elide(self):
        super().setText(self.fontMetrics().elidedText(self._full_text, Qt.ElideRight, max(0, self.width())))

    def minimumSizeHint(self):
        size = super().minimumSizeHint()
        size.setWidth(0)
        return size

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()


class EmptyTable(QTableWidget):
    def __init__(self, message, columns=6):
        super().__init__(0, columns)
        self.empty_hint = QLabel(message, self.viewport())
        self.empty_hint.setObjectName("emptyHint")
        self.empty_hint.setAlignment(Qt.AlignCenter)
        self.empty_hint.setWordWrap(True)
        self.empty_hint.setAttribute(Qt.WA_TransparentForMouseEvents)

    def setRowCount(self, count):
        super().setRowCount(count)
        self.empty_hint.setVisible(count == 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.empty_hint.setGeometry(self.viewport().rect().adjusted(12, 4, -12, -4))


class GripHandle(QSplitterHandle):
    def __init__(self, orientation, parent):
        super().__init__(orientation, parent)
        self.setAttribute(Qt.WA_Hover)
        self.setToolTip("拖动调整区域比例，松开后自动保存")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(ACCENT if self.underMouse() else "#b8c9c8"))
        center = self.rect().center()
        for offset in (-7, 0, 7):
            x = center.x() + (offset if self.orientation() == Qt.Vertical else 0)
            y = center.y() + (offset if self.orientation() == Qt.Horizontal else 0)
            painter.drawRoundedRect(QRectF(x - 1.5, y - 1.5, 3, 3), 1.5, 1.5)


class WorkspaceSplitter(QSplitter):
    def createHandle(self):
        return GripHandle(self.orientation(), self)


STYLESHEET = """
QMainWindow, QDialog, QWidget#workspace { background: #f2f6f6; }
QScrollArea#workspaceScroll, QScrollArea#workspaceScroll > QWidget > QWidget { background: transparent; border: none; }
QWidget { color: #23383d; font-size: 13px; }
QLabel { background: transparent; border: none; }
QLabel#brandTitle { font-size: 26px; font-weight: 700; color: #183c3b; }
QLabel#brandCaption { color: #73878b; font-size: 12px; }
QLabel#versionBadge { background: #e7eeee; color: #6d8286; padding: 5px 10px; border-radius: 10px; font-size: 11px; }
QLabel#activityBadge { background: #e4f3ef; color: #08786d; padding: 6px 12px; border-radius: 11px; font-size: 12px; }
QLabel#activityBadge[state="busy"] { background: #e8f0fc; color: #396baa; }
QLabel#activityBadge[state="waiting"] { background: #e9eeee; color: #687f82; }
QLabel[role="heading"] { font-size: 14px; font-weight: 600; color: #203f41; }
QLabel[role="muted"] { color: #76888c; font-size: 12px; }
QLabel[role="eyebrow"] { color: #637b7e; font-size: 12px; font-weight: 600; }
QLabel[role="count"] { background: #edf5f3; color: #13796e; padding: 3px 9px; border-radius: 8px; font-size: 12px; }
QLabel#emptyHint { color: #91a1a5; font-size: 12px; }
QLabel#statusMessage { color: #61797d; font-size: 12px; }
QLabel#currentPerson { color: #74878a; font-size: 12px; }
QFrame[role="card"], QFrame#actionCard { background: white; border: 1px solid #dfe8e8; border-radius: 12px; }
QFrame#settingsDivider { background: #e6eded; border: none; }
QPushButton { background: white; border: 1px solid #d6e2e2; border-radius: 7px; padding: 6px 12px; min-height: 18px; font-weight: 500; }
QPushButton:hover { background: #f0f8f6; border-color: #8dbfb5; color: #08786d; }
QPushButton:pressed { background: #dfefeb; }
QPushButton:focus { border: 1px solid #087f73; }
QPushButton:disabled { background: #f4f7f7; border-color: #e8eeee; color: #9baaad; }
QPushButton[role="import"] { color: #08786d; background: #eff8f5; border-color: #d4e9e3; }
QPushButton[role="import"]:hover { background: #e1f2ec; border-color: #8dbfb5; }
QPushButton[role="import"]:disabled { background: #f4f7f7; color: #9baaad; border-color: #e8eeee; }
QPushButton#startLogin { background: #087f73; color: white; border: 1px solid #087f73; font-weight: 600; padding: 9px 20px; }
QPushButton#startLogin:hover { background: #066d63; border-color: #066d63; }
QPushButton#startLogin:pressed { background: #07574f; }
QPushButton#startLogin:focus { border: 1px solid #054c45; }
QPushButton#startLogin:disabled { background: #b4ceca; border-color: #b4ceca; color: #f9fdfc; }
QPushButton#stopLogin { color: #b25252; border-color: #ecdada; }
QPushButton#stopLogin:hover { background: #fcf1f1; border-color: #d8a0a0; }
QPushButton#stopLogin:disabled { color: #adb7b9; background: #f7f9f9; border-color: #e8eeee; }
QPushButton#toggleLog:checked { background: #e8f4f0; border-color: #b4d6cc; color: #08786d; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox { background: #f8fafa; color: #2b464b; border: 1px solid #dae5e5; border-radius: 6px; padding: 5px 9px; min-height: 20px; selection-background-color: #d5eee6; selection-color: #174e45; }
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover { border-color: #a6c7c1; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus { border-color: #087f73; background: white; }
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled { background: #f2f5f5; color: #a1afb1; border-color: #e6ecec; }
QSpinBox::up-button, QDoubleSpinBox::up-button { subcontrol-origin: border; subcontrol-position: top right; width: 17px; border-left: 1px solid #e0e9e7; border-top-right-radius: 6px; }
QSpinBox::down-button, QDoubleSpinBox::down-button { subcontrol-origin: border; subcontrol-position: bottom right; width: 17px; border-left: 1px solid #e0e9e7; border-bottom-right-radius: 6px; }
QSpinBox::up-button:hover, QSpinBox::down-button:hover, QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover { background: #e6f1ed; }
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { image: url(@ASSETS@/chevron-up.svg); width: 10px; height: 10px; }
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { image: url(@ASSETS@/chevron-down.svg); width: 10px; height: 10px; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox::down-arrow { image: url(@ASSETS@/chevron-down.svg); width: 12px; height: 12px; }
QComboBox QAbstractItemView { background: white; border: 1px solid #d4e3df; selection-background-color: #e1f3eb; selection-color: #136456; padding: 5px; }
QCheckBox { spacing: 7px; color: #365157; }
QCheckBox:disabled { color: #a0afb1; }
QCheckBox::indicator { width: 16px; height: 16px; background: white; border: 1px solid #b4c8c3; border-radius: 4px; }
QCheckBox::indicator:hover { border-color: #087f73; background: #f0f8f5; }
QCheckBox::indicator:checked { background: #087f73; border-color: #087f73; image: url(@ASSETS@/check.svg); }
QCheckBox::indicator:disabled { background: #e4ece9; border-color: #d1deda; }
QCheckBox:focus { color: #087f73; }
QTableWidget, QListWidget, QPlainTextEdit, QTextBrowser { background: white; border: 1px solid #e2eaea; border-radius: 7px; padding: 0; outline: 0; selection-background-color: #e0f2ec; selection-color: #16574b; alternate-background-color: #f7fafa; }
QTableWidget { gridline-color: transparent; }
QTableWidget::item { border: none; padding: 5px 8px; }
QTableWidget::item:selected { background: #e0f2ec; color: #16574b; }
QTableWidget::item:hover { background: #f0f8f5; }
QHeaderView { background: #f4f8f7; border: none; }
QHeaderView::section { background: #f4f8f7; color: #6a8185; font-size: 12px; font-weight: 500; padding: 7px 8px; border: none; border-bottom: 1px solid #e4eceb; }
QListWidget { background: #f8fbfa; }
QListWidget::item { border: none; border-radius: 5px; padding: 7px 8px; margin: 2px 4px; color: #587176; }
QListWidget::item:selected { background: #dff1e9; color: #086e60; }
QListWidget::item:hover { background: #edf5f1; }
QPlainTextEdit#questionPreview { background: #fbfdfc; padding: 9px; color: #3c595f; }
QTextBrowser#questionPreview { background: #fbfdfc; padding: 9px; color: #3c595f; }
QPlainTextEdit#runLog { background: #f8fbfa; color: #61797c; font-size: 12px; padding: 8px; }
QTabWidget::pane { background: white; border: 1px solid #dfe8e8; border-radius: 9px; top: -1px; }
QTabBar::tab { color: #7a8e91; background: transparent; border: none; border-bottom: 2px solid transparent; padding: 8px 14px; margin-right: 8px; font-size: 12px; }
QTabBar::tab:selected { color: #087f73; border-bottom: 2px solid #087f73; font-weight: 600; }
QTabBar::tab:hover { color: #087f73; }
QTabWidget#settingsTabs::pane { border-radius: 12px; }
QTabWidget#resultTabs::pane { border: none; }
QTabWidget#resultTabs QTabBar::tab { padding: 6px 13px; margin: 0 5px 5px 0; border: none; border-radius: 6px; }
QTabWidget#resultTabs QTabBar::tab:selected { background: #e4f3ed; color: #087768; }
QProgressBar { background: #eaf1ef; border: none; border-radius: 3px; min-height: 4px; max-height: 4px; }
QProgressBar::chunk { background: #379a86; border-radius: 3px; }
QScrollBar:vertical { background: transparent; width: 9px; margin: 3px 1px; }
QScrollBar::handle:vertical { background: #cfddda; min-height: 24px; border-radius: 3px; margin: 0 2px; }
QScrollBar::handle:vertical:hover { background: #94b7ad; }
QScrollBar:horizontal { background: transparent; height: 9px; margin: 1px 3px; }
QScrollBar::handle:horizontal { background: #cfddda; min-width: 24px; border-radius: 3px; margin: 2px 0; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QSplitter::handle { background: transparent; }
QToolTip { background: #244a45; color: white; border: none; padding: 7px 10px; }
""".replace("@ASSETS@", UI_ASSETS.as_posix())
