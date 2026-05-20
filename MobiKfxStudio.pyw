import os
import re
import subprocess
import sys
import json
import locale
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRect, QSize, Qt, QThread, Signal, QTimer
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QSizePolicy,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


APP_DIR = Path(__file__).resolve().parent
if getattr(sys, "frozen", False):
    APP_DIR = Path(sys._MEIPASS)
USER_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else APP_DIR
PIPELINE = APP_DIR / "MobiToKfxPipeline.ps1"
OUTPUT_DIR = Path(r"D:\漫画")
CONFIG_FILE = USER_DIR / "ui_settings.json"
SUPPORTED_EXTENSIONS = {".mobi", ".epub", ".pdf"}
DEFAULT_SETTINGS = {
    "theme": "晨雾奶油",
    "kcc_path": r"C:\Kindle Previewer 3\lib\fc\bin\KCC_10.1.3.exe",
    "image_output_dir": r"E:\Maga_Output",
    "cbz_output_dir": r"E:\Maga_Output\cbz",
    "kfx_output_dir": r"D:\漫画",
}


def is_supported_input(path):
    p = Path(path)
    return p.is_dir() or p.suffix.lower() in SUPPORTED_EXTENSIONS


class GlowBar(QProgressBar):
    def __init__(self):
        super().__init__()
        self._shine = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(28)
        self.setTextVisible(False)
        self.setFixedHeight(14)

    def _tick(self):
        self._shine = (self._shine + 4) % 220
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.value() <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        width = int(self.width() * self.value() / max(1, self.maximum()))
        x = int(width * self._shine / 220) - 40
        color = QColor(255, 255, 255, 55)
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(QRect(x, 0, 70, self.height()), 7, 7)


class DropPanel(QFrame):
    filesDropped = Signal(list)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setObjectName("dropPanel")
        self.normal_text = "把 MOBI / EPUB / PDF / 图片文件夹拖到这里"
        self.label = QLabel(self.normal_text)
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setObjectName("dropTitle")
        self.label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.sub = QLabel("支持多本批量排队 · 文件夹会直接交给 KCC，不会被清理")
        self.sub.setAlignment(Qt.AlignCenter)
        self.sub.setObjectName("dropSub")
        self.sub.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.addStretch(1)
        lay.addWidget(self.label)
        lay.addWidget(self.sub)
        lay.addStretch(1)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            paths = [u.toLocalFile() for u in event.mimeData().urls()]
            if any(is_supported_input(p) for p in paths):
                event.acceptProposedAction()
                self.setProperty("active", True)
                self.style().unpolish(self)
                self.style().polish(self)
                self.label.setText("松手，加入队列")

    def dragLeaveEvent(self, event):
        self._reset()

    def dropEvent(self, event: QDropEvent):
        paths = [u.toLocalFile() for u in event.mimeData().urls()]
        files = [p for p in paths if is_supported_input(p)]
        if files:
            self.filesDropped.emit(files)
        self._reset()

    def mouseReleaseEvent(self, event):
        window = self.window()
        if hasattr(window, "pick_files"):
            window.pick_files()

    def _reset(self):
        self.setProperty("active", False)
        self.style().unpolish(self)
        self.style().polish(self)
        self.label.setText(self.normal_text)


class PipelineWorker(QThread):
    line = Signal(str)
    progress = Signal(int, str)
    finishedOk = Signal()
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, files, config_path):
        super().__init__()
        self.files = files
        self.config_path = config_path
        self.proc = None
        self._stopping = False

    def run(self):
        cmd = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-STA",
            "-File",
            str(PIPELINE),
            "-NoMessageBox",
            "-ConfigPath",
            str(self.config_path),
            *self.files,
        ]
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        flags = subprocess.CREATE_NO_WINDOW if sys.platform.startswith("win") else 0
        try:
            self.proc = subprocess.Popen(
                cmd,
                cwd=str(USER_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=False,
                creationflags=flags,
                env=env,
            )
            for raw in self.proc.stdout:
                text = self._decode_line(raw).rstrip()
                self.line.emit(text)
                self._map_progress(text)
            code = self.proc.wait()
            if self._stopping:
                self.progress.emit(0, "已中断")
                self.cancelled.emit()
            elif code == 0:
                self.progress.emit(100, "全部完成")
                self.finishedOk.emit()
            else:
                self.failed.emit(f"转换失败，退出码 {code}")
        except Exception as exc:
            if self._stopping:
                self.cancelled.emit()
            else:
                self.failed.emit(str(exc))

    def _decode_line(self, raw):
        for enc in ("utf-8-sig", locale.getpreferredencoding(False), "gb18030"):
            try:
                return raw.decode(enc)
            except Exception:
                pass
        return raw.decode("utf-8", errors="replace")

    def stop(self):
        self._stopping = True
        if self.proc and self.proc.poll() is None:
            if sys.platform.startswith("win"):
                subprocess.run(
                    ["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            else:
                self.proc.terminate()

    def _map_progress(self, text):
        rules = [
            ("收到", 4, "读取队列"),
            ("开始提取", 8, "批量提取图片"),
            ("开始图片提取", 14, "提取图片"),
            ("直接使用图片文件夹", 22, "整理图片文件夹"),
            ("图片提取完成", 28, "图片提取完成"),
            ("KCC 批量加入", 34, "KCC 批量加入队列"),
            ("KCC 加入队列", 42, "KCC 批量加入队列"),
            ("KCC 开始批量", 48, "KCC 正在批量生成 CBZ"),
            ("CBZ 输出完成", 62, "CBZ 已生成"),
            ("KFX 批量任务", 68, "KFX 批量任务已建立"),
            ("kckfxgen 批量参数", 72, "KFX 正在批量生成"),
            ("kckfxgen batch complete", 92, "KFX 批量生成完成"),
            ("KFX 输出完成", 95, "KFX 已生成"),
            ("删除中间", 97, "清理中间文件"),
            ("完成：", 100, "完成"),
        ]
        for key, value, label in rules:
            if key in text:
                self.progress.emit(value, label)
                return


class TitleBar(QFrame):
    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("titleBar")
        self._drag_pos = None
        self.setFixedHeight(42)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 6, 8, 6)
        lay.setSpacing(8)

        self.caption = QLabel("MOBI → KFX Studio")
        self.caption.setObjectName("windowCaption")
        lay.addWidget(self.caption, 1)

        self.min_btn = QPushButton("—")
        self.max_btn = QPushButton("□")
        self.close_btn = QPushButton("×")
        for btn in (self.min_btn, self.max_btn, self.close_btn):
            btn.setObjectName("windowButton")
            btn.setFixedSize(34, 28)
            btn.setFocusPolicy(Qt.NoFocus)
            lay.addWidget(btn)

        self.min_btn.clicked.connect(parent.showMinimized)
        self.max_btn.clicked.connect(parent.toggle_max_restore)
        self.close_btn.clicked.connect(parent.close)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.window().frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons() & Qt.LeftButton:
            if self.window().isMaximized():
                self.window().showNormal()
                self._drag_pos = QPoint(self.window().width() // 2, 20)
            self.window().move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.window().toggle_max_restore()


class MainWindow(QMainWindow):
    THEMES = {
        "晨雾奶油": {
            "bg": "#f6f4ee", "panel": "#fffdf8", "panel2": "#eef4f2", "text": "#263238",
            "muted": "#75858a", "border": "#d7dfdc", "accent": "#5ab8a7", "accent2": "#f0c66b",
            "accent3": "#91c7b1", "button": "#fffaf0", "button_hover": "#edf7f3", "log": "#fbfaf6",
        },
        "克莱因蓝": {
            "bg": "#f5f7ff", "panel": "#ffffff", "panel2": "#eef2ff", "text": "#172033",
            "muted": "#65718a", "border": "#cbd7ff", "accent": "#2248ff", "accent2": "#72d6ff",
            "accent3": "#7b8cff", "button": "#eef2ff", "button_hover": "#e2e9ff", "log": "#fbfcff",
        },
        "薄荷海盐": {
            "bg": "#eff8f5", "panel": "#fbfffd", "panel2": "#e2f3ed", "text": "#20332f",
            "muted": "#648178", "border": "#c5ddd5", "accent": "#36b892", "accent2": "#94d8c3",
            "accent3": "#f3d98b", "button": "#f6fffb", "button_hover": "#e5f7f0", "log": "#fbfffd",
        },
        "樱花白桃": {
            "bg": "#fff5f7", "panel": "#fffefe", "panel2": "#fde9ef", "text": "#382a31",
            "muted": "#8a6f79", "border": "#f0cdd7", "accent": "#e986a7", "accent2": "#ffc4a8",
            "accent3": "#b9a7ff", "button": "#fff8fa", "button_hover": "#ffedf2", "log": "#fffefe",
        },
        "鼠尾草绿": {
            "bg": "#f3f6ef", "panel": "#fffffb", "panel2": "#e8eee2", "text": "#2f372d",
            "muted": "#697764", "border": "#cfd9c7", "accent": "#7fa36d", "accent2": "#c7d59f",
            "accent3": "#e5c678", "button": "#fbfff7", "button_hover": "#edf4e8", "log": "#fffffb",
        },
        "薰衣草云": {
            "bg": "#f7f4ff", "panel": "#ffffff", "panel2": "#efe9fb", "text": "#2e2938",
            "muted": "#766d89", "border": "#d8cdec", "accent": "#9a7bd8", "accent2": "#d4b8ff",
            "accent3": "#7bc9c0", "button": "#fbf8ff", "button_hover": "#f0eaff", "log": "#ffffff",
        },
        "晴空蓝": {
            "bg": "#f0f8ff", "panel": "#ffffff", "panel2": "#e2f1fb", "text": "#1f3140",
            "muted": "#60798b", "border": "#c6ddea", "accent": "#4aa3df", "accent2": "#9dd7f0",
            "accent3": "#ffd37a", "button": "#f8fcff", "button_hover": "#e8f6ff", "log": "#ffffff",
        },
        "杏仁拿铁": {
            "bg": "#f8f1e8", "panel": "#fffaf3", "panel2": "#efe2d2", "text": "#352b23",
            "muted": "#7d6c5d", "border": "#dfcfbd", "accent": "#c49262", "accent2": "#efd0a4",
            "accent3": "#82b6a1", "button": "#fff7ed", "button_hover": "#f4e5d3", "log": "#fffaf3",
        },
    }

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAcceptDrops(True)
        self.setWindowTitle("MOBI → KFX Studio")
        self.resize(980, 680)
        self.files = []
        self.worker = None
        self.settings = self.load_settings()
        self.current_theme = self.settings.get("theme", "晨雾奶油")
        if self.current_theme not in self.THEMES:
            self.current_theme = "晨雾奶油"
        self._build()
        self._style()
        self._intro()

    def _build(self):
        root = QWidget()
        root.setAcceptDrops(True)
        self.setCentralWidget(root)
        shell = QVBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        self.title_bar = TitleBar(self)
        shell.addWidget(self.title_bar)

        content = QWidget()
        content.setObjectName("content")
        main = QVBoxLayout(content)
        main.setContentsMargins(28, 18, 28, 24)
        main.setSpacing(16)
        shell.addWidget(content, 1)

        top = QHBoxLayout()
        title_box = QVBoxLayout()
        self.title = QLabel("Manga → KFX Studio")
        self.title.setObjectName("title")
        self.subtitle = QLabel("支持 MOBI、EPUB、PDF 和图片文件夹，批量生成 KFX 后清理中间 CBZ")
        self.subtitle.setObjectName("subtitle")
        title_box.addWidget(self.title)
        title_box.addWidget(self.subtitle)
        top.addLayout(title_box, 1)

        self.theme_btn = QPushButton()
        self.theme_btn.setObjectName("themeButton")
        self.theme_btn.setFixedHeight(34)
        self.theme_btn.setMinimumWidth(116)
        self.theme_menu = QMenu(self)
        self.theme_btn.clicked.connect(self.show_theme_menu)
        for name in self.THEMES:
            action = self.theme_menu.addAction(name)
            action.triggered.connect(lambda checked=False, n=name: self.set_theme(n))
        top.addWidget(self.theme_btn)
        main.addLayout(top)

        nav = QHBoxLayout()
        nav.setSpacing(8)
        self.convert_tab = QPushButton("转换")
        self.settings_tab = QPushButton("设置")
        for btn in (self.convert_tab, self.settings_tab):
            btn.setObjectName("tabButton")
            btn.setFixedHeight(34)
            btn.setFocusPolicy(Qt.NoFocus)
            nav.addWidget(btn)
        nav.addStretch(1)
        self.convert_tab.clicked.connect(lambda: self.switch_page(0))
        self.settings_tab.clicked.connect(lambda: self.switch_page(1))
        main.addLayout(nav)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_convert_page())
        self.pages.addWidget(self._build_settings_page())
        main.addWidget(self.pages, 1)
        self.switch_page(0)

    def _build_convert_page(self):
        page = QWidget()
        main = QVBoxLayout(page)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(16)

        actions = QHBoxLayout()
        self.pick_btn = QPushButton("选择文件")
        self.pick_btn.clicked.connect(self.pick_files)
        self.pick_folder_btn = QPushButton("选择图片文件夹")
        self.pick_folder_btn.clicked.connect(self.pick_folder)
        self.start_btn = QPushButton("开始转换")
        self.start_btn.clicked.connect(self.start)
        self.stop_btn = QPushButton("中断转换")
        self.stop_btn.setObjectName("dangerButton")
        self.stop_btn.clicked.connect(self.stop_conversion)
        self.stop_btn.setDisabled(True)
        self.clear_btn = QPushButton("清空")
        self.clear_btn.clicked.connect(self.clear)
        for btn in (self.pick_btn, self.pick_folder_btn, self.start_btn, self.stop_btn, self.clear_btn):
            actions.addWidget(btn)
        actions.addStretch(1)
        main.addLayout(actions)

        self.drop = DropPanel()
        self.drop.filesDropped.connect(self.add_files)
        main.addWidget(self.drop)

        body = QHBoxLayout()
        body.setSpacing(16)
        left = QVBoxLayout()
        queue_title = QLabel("队列")
        queue_title.setObjectName("sectionTitle")
        self.queue = QListWidget()
        self.queue.setObjectName("queue")
        left.addWidget(queue_title)
        left.addWidget(self.queue)
        body.addLayout(left, 2)

        right = QVBoxLayout()
        status_title = QLabel("状态")
        status_title.setObjectName("sectionTitle")
        self.status = QLabel("等待文件")
        self.status.setObjectName("status")
        self.progress = GlowBar()
        self.progress.setRange(0, 100)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setObjectName("log")
        right.addWidget(status_title)
        right.addWidget(self.status)
        right.addWidget(self.progress)
        right.addWidget(self.log, 1)
        body.addLayout(right, 3)
        main.addLayout(body, 1)

        self.footer = QLabel()
        self.footer.setObjectName("footer")
        main.addWidget(self.footer)
        self.update_footer()
        return page

    def _build_settings_page(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(14)

        panel = QFrame()
        panel.setObjectName("settingsPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        header = QLabel("路径设置")
        header.setObjectName("sectionTitle")
        layout.addWidget(header)

        self.kcc_edit = self._add_path_row(layout, "KCC 程序", "kcc_path", True)
        self.image_output_edit = self._add_path_row(layout, "漫画图片提取文件夹 (Output)", "image_output_dir", False)
        self.cbz_output_edit = self._add_path_row(layout, "CBZ 漫画放置点", "cbz_output_dir", False)
        self.kfx_output_edit = self._add_path_row(layout, "最终 KFX 输出目录", "kfx_output_dir", False)

        btns = QHBoxLayout()
        self.save_paths_btn = QPushButton("保存设置")
        self.save_paths_btn.clicked.connect(self.save_settings_from_fields)
        self.default_paths_btn = QPushButton("恢复默认")
        self.default_paths_btn.clicked.connect(self.restore_default_paths)
        btns.addStretch(1)
        btns.addWidget(self.default_paths_btn)
        btns.addWidget(self.save_paths_btn)
        layout.addLayout(btns)
        outer.addWidget(panel)
        outer.addStretch(1)
        return page

    def _add_path_row(self, layout, label_text, key, is_file):
        row = QHBoxLayout()
        label = QLabel(label_text)
        label.setObjectName("pathLabel")
        label.setFixedWidth(190)
        edit = QLineEdit(self.settings.get(key, DEFAULT_SETTINGS[key]))
        edit.setObjectName("pathInput")
        browse = QPushButton("浏览")
        browse.setObjectName("smallButton")
        browse.clicked.connect(lambda checked=False, e=edit, f=is_file, k=key: self.browse_path(e, f, k))
        row.addWidget(label)
        row.addWidget(edit, 1)
        row.addWidget(browse)
        layout.addLayout(row)
        return edit

    def _style(self, theme_name=None):
        if theme_name:
            self.current_theme = theme_name
        t = self.THEMES.get(self.current_theme, self.THEMES["晨雾奶油"])
        if hasattr(self, "theme_btn"):
            self.theme_btn.setText(f"{self.current_theme}  ▾")
        self.setStyleSheet(
            f"""
            QWidget {{ background: {t['bg']}; color: {t['text']}; font-family: "Microsoft YaHei UI"; font-size: 14px; }}
            #content {{ background: {t['bg']}; }}
            #titleBar {{ background: {t['panel']}; border-bottom: 1px solid {t['border']}; }}
            #windowCaption {{ color: {t['muted']}; font-weight: 700; }}
            #windowButton {{
                background: {t['button']}; border: 1px solid {t['border']}; border-radius: 6px;
                padding: 0; color: {t['text']}; font-size: 14px; font-weight: 700;
            }}
            #windowButton:hover {{ background: {t['button_hover']}; border-color: {t['accent']}; }}
            #title {{ font-size: 30px; font-weight: 800; letter-spacing: 0; color: {t['text']}; }}
            #subtitle, #footer {{ color: {t['muted']}; }}
            QPushButton {{
                background: {t['button']}; border: 1px solid {t['border']}; border-radius: 8px;
                padding: 10px 16px; color: {t['text']}; font-weight: 600;
            }}
            QPushButton:hover {{ background: {t['button_hover']}; border-color: {t['accent']}; }}
            QPushButton:pressed {{ background: {t['panel2']}; }}
            QPushButton:disabled {{ color: {t['muted']}; background: {t['panel2']}; border-color: {t['border']}; }}
            #dangerButton {{
                color: #9b3145;
                border-color: #efb9c6;
                background: #fff4f6;
            }}
            #dangerButton:hover {{
                color: #7d1f32;
                border-color: #dc7891;
                background: #ffe7ec;
            }}
            #themeButton {{
                min-width: 116px; padding: 6px 10px; border-radius: 8px;
                color: {t['accent']}; font-weight: 800; text-align: center;
            }}
            QMenu {{
                background: {t['panel']}; color: {t['text']}; border: 1px solid {t['border']};
                border-radius: 8px; padding: 6px;
            }}
            QMenu::item {{
                padding: 7px 22px 7px 12px; border-radius: 6px;
            }}
            QMenu::item:selected {{
                background: {t['panel2']}; color: {t['accent']};
            }}
            #dropPanel {{
                border: 2px dashed {t['border']}; border-radius: 10px;
                background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {t['panel']}, stop:1 {t['panel2']});
                min-height: 150px;
            }}
            #dropPanel[active="true"] {{ border-color: {t['accent']}; background: {t['panel2']}; }}
            #dropTitle {{ font-size: 24px; font-weight: 800; color: {t['accent']}; }}
            #dropSub {{ color: {t['muted']}; margin-top: 8px; }}
            #sectionTitle {{ color: {t['text']}; font-weight: 700; }}
            #queue, #log {{
                background: {t['log']}; border: 1px solid {t['border']}; border-radius: 8px;
                padding: 8px;
            }}
            QListWidget::item {{ padding: 10px; border-radius: 6px; color: {t['text']}; }}
            QListWidget::item:selected {{ background: {t['panel2']}; color: {t['text']}; }}
            #status {{ font-size: 20px; font-weight: 800; color: {t['accent']}; }}
            #tabButton {{
                padding: 7px 18px; border-radius: 8px; color: {t['muted']};
                background: {t['button']}; border: 1px solid {t['border']};
            }}
            #tabButton[active="true"] {{
                color: {t['accent']}; background: {t['panel2']}; border-color: {t['accent']};
            }}
            #settingsPanel {{
                background: {t['panel']}; border: 1px solid {t['border']}; border-radius: 10px;
            }}
            #pathLabel {{ color: {t['muted']}; font-weight: 700; }}
            #pathInput {{
                background: {t['log']}; border: 1px solid {t['border']}; border-radius: 8px;
                padding: 9px 10px; color: {t['text']};
            }}
            #pathInput:focus {{ border-color: {t['accent']}; background: {t['panel']}; }}
            #smallButton {{ padding: 8px 12px; border-radius: 8px; }}
            QProgressBar {{
                background: {t['panel2']}; border: 1px solid {t['border']}; border-radius: 7px;
            }}
            QProgressBar::chunk {{
                border-radius: 7px;
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {t['accent']}, stop:0.55 {t['accent2']}, stop:1 {t['accent3']});
            }}
            QTextEdit {{ color: {t['text']}; selection-background-color: {t['accent2']}; }}
            """
        )

    def show_theme_menu(self):
        self.theme_menu.adjustSize()
        pos = self.theme_btn.mapToGlobal(self.theme_btn.rect().bottomLeft())
        button_center = self.theme_btn.mapToGlobal(self.theme_btn.rect().center())
        screen = QApplication.screenAt(button_center) or QApplication.screenAt(pos) or QApplication.primaryScreen()
        if screen:
            area = screen.availableGeometry()
            size = self.theme_menu.sizeHint()
            x = min(max(pos.x(), area.left()), area.right() - size.width())
            y = pos.y()
            if y + size.height() > area.bottom():
                y = self.theme_btn.mapToGlobal(self.theme_btn.rect().topLeft()).y() - size.height()
            y = min(max(y, area.top()), area.bottom() - size.height())
            pos = QPoint(x, y)
        self.theme_menu.popup(pos)

    def set_theme(self, name):
        self._style(name)
        self.save_settings()

    def switch_page(self, index):
        self.pages.setCurrentIndex(index)
        for btn, active in ((self.convert_tab, index == 0), (self.settings_tab, index == 1)):
            btn.setProperty("active", active)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def load_settings(self):
        data = DEFAULT_SETTINGS.copy()
        try:
            if CONFIG_FILE.exists():
                loaded = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    data.update({k: v for k, v in loaded.items() if v})
        except Exception:
            pass
        return data

    def save_settings(self):
        try:
            if hasattr(self, "kcc_edit"):
                self.settings.update({
                    "kcc_path": self.kcc_edit.text().strip(),
                    "image_output_dir": self.image_output_edit.text().strip(),
                    "cbz_output_dir": self.cbz_output_edit.text().strip(),
                    "kfx_output_dir": self.kfx_output_edit.text().strip(),
                })
            self.settings["theme"] = self.current_theme
            CONFIG_FILE.write_text(json.dumps(self.settings, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            if hasattr(self, "log"):
                self.log.append(f"保存设置失败：{exc}")

    def save_settings_from_fields(self):
        self.save_settings()
        self.update_footer()
        if hasattr(self, "status"):
            self.status.setText("设置已保存")

    def restore_default_paths(self):
        self.kcc_edit.setText(DEFAULT_SETTINGS["kcc_path"])
        self.image_output_edit.setText(DEFAULT_SETTINGS["image_output_dir"])
        self.cbz_output_edit.setText(DEFAULT_SETTINGS["cbz_output_dir"])
        self.kfx_output_edit.setText(DEFAULT_SETTINGS["kfx_output_dir"])
        self.save_settings_from_fields()

    def browse_path(self, edit, is_file, key):
        start = edit.text().strip() or str(Path.home())
        if is_file:
            path, _ = QFileDialog.getOpenFileName(self, "选择 KCC 程序", start, "程序 (*.exe);;所有文件 (*.*)")
        else:
            path = QFileDialog.getExistingDirectory(self, "选择文件夹", start)
        if path:
            edit.setText(path)
            if key == "image_output_dir" and self.cbz_output_edit.text().strip() in ("", self.settings.get("cbz_output_dir"), DEFAULT_SETTINGS["cbz_output_dir"]):
                self.cbz_output_edit.setText(str(Path(path) / "cbz"))
            self.save_settings_from_fields()

    def update_footer(self):
        if hasattr(self, "footer"):
            out = self.settings.get("kfx_output_dir", DEFAULT_SETTINGS["kfx_output_dir"])
            cbz = self.settings.get("cbz_output_dir", DEFAULT_SETTINGS["cbz_output_dir"])
            self.footer.setText(f"最终输出：{out}    CBZ 中间目录：{cbz}")

    def _intro(self):
        effect = QGraphicsOpacityEffect(self.drop)
        self.drop.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", self)
        anim.setDuration(520)
        anim.setStartValue(0.15)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.start()
        self._intro_anim = anim

    def pick_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择漫画文件",
            str(Path.home()),
            "支持的文件 (*.mobi *.epub *.pdf);;MOBI 文件 (*.mobi);;EPUB 文件 (*.epub);;PDF 文件 (*.pdf)",
        )
        self.add_files(files)

    def pick_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择图片文件夹", str(Path.home()))
        if folder:
            self.add_files([folder])

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            paths = [u.toLocalFile() for u in event.mimeData().urls()]
            if any(is_supported_input(p) for p in paths):
                event.acceptProposedAction()
                self.drop.setProperty("active", True)
                self.drop.style().unpolish(self.drop)
                self.drop.style().polish(self.drop)

    def dragLeaveEvent(self, event):
        self.drop._reset()

    def dropEvent(self, event: QDropEvent):
        paths = [u.toLocalFile() for u in event.mimeData().urls()]
        self.add_files([p for p in paths if is_supported_input(p)])
        self.drop._reset()

    def add_files(self, files):
        added = 0
        for f in files:
            path = str(Path(f))
            if is_supported_input(path) and path not in self.files:
                self.files.append(path)
                suffix = "文件夹" if Path(path).is_dir() else Path(path).suffix.upper().lstrip(".")
                item = QListWidgetItem(f"{Path(path).name}    [{suffix}]")
                item.setToolTip(path)
                self.queue.addItem(item)
                added += 1
        if added:
            self.status.setText(f"已加入 {len(self.files)} 个项目")
            self.progress.setValue(0)

    def clear(self):
        if self.worker and self.worker.isRunning():
            return
        self.files.clear()
        self.queue.clear()
        self.log.clear()
        self.progress.setValue(0)
        self.status.setText("等待文件")

    def start(self):
        if not self.files:
            QMessageBox.information(self, "Manga → KFX Studio", "先拖入或选择 mobi / epub / pdf / 图片文件夹。")
            return
        if self.worker and self.worker.isRunning():
            return
        self.log.clear()
        self.progress.setValue(1)
        self.status.setText("启动转换")
        self.save_settings()
        self._set_busy(True)
        self.worker = PipelineWorker(self.files[:], CONFIG_FILE)
        self.worker.line.connect(self.append_log)
        self.worker.progress.connect(self.set_progress)
        self.worker.finishedOk.connect(self.done)
        self.worker.failed.connect(self.fail)
        self.worker.cancelled.connect(self.cancelled)
        self.worker.start()

    def stop_conversion(self):
        if not (self.worker and self.worker.isRunning()):
            return
        self.status.setText("正在中断")
        self.log.append("正在中断转换，清理后台进程...")
        self.stop_btn.setDisabled(True)
        self.worker.stop()

    def append_log(self, text):
        clean = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), text)
        self.log.append(clean)

    def set_progress(self, value, label):
        self.progress.setValue(value)
        self.status.setText(label)

    def done(self):
        self._set_busy(False)
        self.status.setText("转换完成")
        self.progress.setValue(100)

    def fail(self, message):
        self._set_busy(False)
        self.status.setText("转换失败")
        self.log.append(message)
        QMessageBox.critical(self, "转换失败", message)

    def cancelled(self):
        self._set_busy(False)
        self.status.setText("已中断")
        self.progress.setValue(0)
        self.log.append("转换已中断。")

    def _set_busy(self, busy):
        self.pick_btn.setDisabled(busy)
        self.pick_folder_btn.setDisabled(busy)
        self.start_btn.setDisabled(busy)
        self.clear_btn.setDisabled(busy)
        self.settings_tab.setDisabled(busy)
        self.drop.setDisabled(busy)
        self.stop_btn.setDisabled(not busy)

    def toggle_max_restore(self):
        if self.isMaximized():
            self.showNormal()
            self.title_bar.max_btn.setText("□")
        else:
            self.showMaximized()
            self.title_bar.max_btn.setText("❐")

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(1500)
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei UI", 10))
    window = MainWindow()
    window.show()
    if len(sys.argv) > 1:
        window.add_files(sys.argv[1:])
    sys.exit(app.exec())
