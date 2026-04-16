import sys
import logging
import threading
import re
import argparse
import difflib
import json
import os
import subprocess
import shutil
import html
import time
import math
from pathlib import Path
from datetime import datetime
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QTextEdit, QPushButton,
    QVBoxLayout, QWidget, QSystemTrayIcon, QMenu, QLabel,
    QComboBox, QHBoxLayout, QProgressBar, QTabWidget, QDoubleSpinBox, QStyle, QCheckBox, QLineEdit,
    QFrame, QGraphicsDropShadowEffect, QGroupBox, QFormLayout, QToolButton, QSizePolicy, QToolTip,
    QScrollArea, QSlider, QStackedWidget, QFileDialog, QInputDialog, QDialog, QMessageBox,
)

import sounddevice as sd

from PySide6.QtGui import QAction, QIcon, QPixmap, QPainter, QColor, QPen, QBrush, QCursor
from PySide6.QtCore import Signal, QObject, QTimer, QEvent, Qt, QPropertyAnimation, QEasingCurve, QRect

from src.jarvis.engine import JarvisEngine
from src.jarvis.key_store import ensure_keys_file, save_keys
from src.jarvis.app_scanner import merge_scanned_apps_into_config

logger = logging.getLogger(__name__)


class LogBus(QObject):
    log = Signal(str)


class NoWheelDoubleSpinBox(QDoubleSpinBox):
    """SpinBox, который не меняет значение колесом мыши."""

    def wheelEvent(self, event):  # noqa: N802 (Qt naming)
        event.ignore()


class OverlayStepperDoubleSpinBox(NoWheelDoubleSpinBox):
    """
    QDoubleSpinBox с оверлей-кнопками ▲/▼ внутри поля.
    Это обход Qt/Windows проблемы, когда нативные стрелки выглядят как "точки"
    или QSS-иконки не рендерятся стабильно.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        # резервируем место справа под кнопки, чтобы текст не залезал под них
        le = self.lineEdit()
        if le is not None:
            le.setTextMargins(0, 0, 20, 0)
        else:
            # на всякий случай: если lineEdit() недоступен
            self.setStyleSheet("padding-right: 20px;")

        self._btn_up = QToolButton(self)
        self._btn_up.setObjectName("spinStepUp")
        self._btn_up.setText("▴")
        self._btn_up.setCursor(Qt.CursorShape.ArrowCursor)
        self._btn_up.clicked.connect(self.stepUp)

        self._btn_down = QToolButton(self)
        self._btn_down.setObjectName("spinStepDown")
        self._btn_down.setText("▾")
        self._btn_down.setCursor(Qt.CursorShape.ArrowCursor)
        self._btn_down.clicked.connect(self.stepDown)

    def resizeEvent(self, event):  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        bw = 18
        pad = 2
        x = max(0, self.width() - bw - pad)
        h = max(10, self.height())
        half = max(8, (h - 2) // 2)
        self._btn_up.setGeometry(x, 1, bw, half)
        self._btn_down.setGeometry(x, 1 + half, bw, max(8, h - 2 - half))


class NoWheelComboBox(QComboBox):
    """ComboBox, который не меняет выбор колесом мыши."""

    def wheelEvent(self, event):  # noqa: N802 (Qt naming)
        event.ignore()


class NoWheelSlider(QSlider):
    """Slider, который не меняет значение колесом мыши."""

    def wheelEvent(self, event):  # noqa: N802 (Qt naming)
        event.ignore()


class MicSensitivitySlider(QWidget):
    """Один элемент: порог + живой уровень (как в Discord)."""

    thresholdChanged = Signal(float)  # 0..1

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(24)
        self.setMouseTracking(True)

        self._threshold = 0.2  # 0..1
        self._auto = False
        self._drag = False

        self._level_target = 0.0  # 0..1
        self._level_smooth = 0.0  # 0..1
        self._smooth_timer = QTimer(self)
        self._smooth_timer.timeout.connect(self._tick_smoothing)
        self._smooth_timer.start(30)

    def set_threshold(self, value: float, emit_signal: bool = False):
        v = max(0.0, min(1.0, float(value)))
        if abs(v - self._threshold) < 1e-6:
            return
        self._threshold = v
        self.update()
        if emit_signal:
            self.thresholdChanged.emit(self._threshold)

    def threshold(self) -> float:
        return float(self._threshold)

    def set_auto(self, enabled: bool):
        self._auto = bool(enabled)
        self.setEnabled(True)  # визуально оставляем видимым, но блокируем перетаскивание
        self.update()

    def is_auto(self) -> bool:
        return bool(self._auto)

    def set_level(self, value: float):
        self._level_target = max(0.0, min(1.0, float(value)))

    def _tick_smoothing(self):
        # Плавное сглаживание без резких скачков
        a = 0.22
        self._level_smooth = (1.0 - a) * self._level_smooth + a * self._level_target
        self.update()

    def _track_rect(self):
        pad_x = 10
        pad_y = 9
        return self.rect().adjusted(pad_x, pad_y, -pad_x, -pad_y)

    def _threshold_x(self) -> int:
        r = self._track_rect()
        return int(r.left() + self._threshold * max(1, r.width()))

    def _set_threshold_from_pos(self, x: int):
        r = self._track_rect()
        if r.width() <= 1:
            return
        v = (x - r.left()) / float(r.width())
        self.set_threshold(v, emit_signal=True)

    def mousePressEvent(self, event):  # noqa: N802 (Qt naming)
        if self._auto:
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = True
            self._set_threshold_from_pos(event.position().x())

    def mouseMoveEvent(self, event):  # noqa: N802 (Qt naming)
        if self._auto or not self._drag:
            return
        self._set_threshold_from_pos(event.position().x())

    def mouseReleaseEvent(self, event):  # noqa: N802 (Qt naming)
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = False

    def paintEvent(self, event):  # noqa: N802 (Qt naming)
        _ = event
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        r = self._track_rect()
        if r.width() <= 2:
            p.end()
            return

        # Базовая дорожка
        radius = r.height() // 2
        base = QColor("#1F2937")
        base.setAlpha(18)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(base))
        p.drawRoundedRect(r, radius, radius)

        # Зоны: слева до порога — жёлтая, справа — зелёная (как в Discord)
        th_x = self._threshold_x()
        left = r.adjusted(0, 0, -(r.right() - th_x), 0)
        right = r.adjusted(th_x - r.left(), 0, 0, 0)

        yellow = QColor("#F59E0B")
        yellow.setAlpha(90)
        green = QColor("#22C55E")
        green.setAlpha(90)
        if left.width() > 0:
            p.setBrush(QBrush(yellow))
            p.drawRoundedRect(left, radius, radius)
        if right.width() > 0:
            p.setBrush(QBrush(green))
            p.drawRoundedRect(right, radius, radius)

        # Живой уровень (более насыщенный поверх)
        lvl = max(0.0, min(1.0, float(self._level_smooth)))
        lvl_w = int(r.width() * lvl)
        if lvl_w > 0:
            lvl_rect = r.adjusted(0, 0, -(r.width() - lvl_w), 0)
            if lvl < self._threshold:
                fill = QColor("#D97706")  # темнее жёлтого
            else:
                fill = QColor("#16A34A")  # темнее зелёного
            fill.setAlpha(180)
            p.setBrush(QBrush(fill))
            p.drawRoundedRect(lvl_rect, radius, radius)

        # Ползунок порога
        handle_r = r.height() + 4
        hx = th_x
        hy = r.center().y()
        handle = QRect(int(hx - handle_r / 2), int(hy - handle_r / 2), handle_r, handle_r)
        p.setBrush(QBrush(QColor("#F1F5F9")))
        p.setPen(QPen(QColor("#111827"), 1))
        p.drawEllipse(handle)

        # В авто режиме подсветка приглушена
        if self._auto:
            veil = QColor("#FFFFFF")
            veil.setAlpha(80)
            p.setBrush(QBrush(veil))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(r, radius, radius)

        p.end()


class ScenarioStepRow(QWidget):
    def __init__(self, app_keys: list[str], on_remove, add_program_cb=None, parent=None):
        super().__init__(parent)
        self._types = [("app", "Программа"), ("url", "Сайт"), ("bat", "Файл .bat")]
        self._type_idx = 0
        self._on_remove = on_remove
        self._add_program_cb = add_program_cb

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.type_btn = QPushButton("→ прога")
        self.type_btn.setFixedWidth(86)
        self.type_btn.clicked.connect(self._cycle_type)
        layout.addWidget(self.type_btn)

        self.stack = QStackedWidget()

        app_page = QWidget()
        app_l = QHBoxLayout(app_page)
        app_l.setContentsMargins(0, 0, 0, 0)
        self.app_combo = QComboBox()
        self.app_combo.addItems(app_keys)
        self.app_combo.setEditable(False)
        app_browse_btn = QPushButton("...")
        app_browse_btn.setFixedWidth(36)
        app_browse_btn.setToolTip("Выбрать .exe и добавить программу в конфиг")
        app_browse_btn.clicked.connect(self._pick_and_add_program)
        app_l.addWidget(self.app_combo)
        app_l.addWidget(app_browse_btn)
        self.stack.addWidget(app_page)

        url_page = QWidget()
        url_l = QHBoxLayout(url_page)
        url_l.setContentsMargins(0, 0, 0, 0)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://example.com")
        url_l.addWidget(self.url_edit)
        self.stack.addWidget(url_page)

        bat_page = QWidget()
        bat_l = QHBoxLayout(bat_page)
        bat_l.setContentsMargins(0, 0, 0, 0)
        self.bat_edit = QLineEdit()
        self.bat_edit.setPlaceholderText("Выбери .bat/.cmd файл")
        browse_btn = QPushButton("...")
        browse_btn.setFixedWidth(36)
        browse_btn.clicked.connect(self._pick_script)
        bat_l.addWidget(self.bat_edit)
        bat_l.addWidget(browse_btn)
        self.stack.addWidget(bat_page)

        layout.addWidget(self.stack)
        self.remove_btn = QPushButton("−")
        self.remove_btn.setFixedSize(34, 34)
        self.remove_btn.setToolTip("Убрать этот шаг")
        self.remove_btn.clicked.connect(self._remove_self)
        layout.addWidget(self.remove_btn)
        self._refresh_type_ui()

    def _remove_self(self):
        if callable(self._on_remove):
            self._on_remove(self)

    def _cycle_type(self):
        self._type_idx = (self._type_idx + 1) % len(self._types)
        self._refresh_type_ui()

    def _refresh_type_ui(self):
        t, label = self._types[self._type_idx]
        self.type_btn.setText(f"→ {label}")
        if t == "app":
            self.stack.setCurrentIndex(0)
        elif t == "url":
            self.stack.setCurrentIndex(1)
        else:
            self.stack.setCurrentIndex(2)

    def _pick_script(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Выбери .bat/.cmd для сценария",
            str(Path.home()),
            "Scripts (*.bat *.cmd);;Все файлы (*.*)",
        )
        if file_path:
            self.bat_edit.setText(file_path)

    def _pick_and_add_program(self):
        if not callable(self._add_program_cb):
            return
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Выбери .exe программы",
            str(Path.home()),
            "Программы (*.exe);;Все файлы (*.*)",
        )
        if not file_path:
            return
        try:
            key = self._add_program_cb(file_path)
            if key:
                if self.app_combo.findText(key) < 0:
                    self.app_combo.addItem(key)
                self.app_combo.setCurrentText(key)
        except Exception:
            pass

    def to_action(self, copy_script_cb) -> str:
        t, _ = self._types[self._type_idx]
        if t == "app":
            app_key = (self.app_combo.currentText() or "").strip()
            if not app_key:
                raise ValueError("Выбери программу для шага app.")
            return f"open:{app_key}"
        if t == "url":
            url = (self.url_edit.text() or "").strip()
            if not url:
                raise ValueError("Укажи ссылку для шага url.")
            if not re.match(r"^https?://", url, flags=re.IGNORECASE):
                url = "https://" + url
            return f"url:{url}"
        file_path = (self.bat_edit.text() or "").strip()
        if not file_path:
            raise ValueError("Выбери .bat/.cmd для шага bat.")
        saved = copy_script_cb(file_path)
        return f"bat:{saved}"

    def apply_action(self, action: str):
        raw = str(action or "").strip()
        if raw.startswith("open:"):
            self._type_idx = 0
            self._refresh_type_ui()
            app_key = raw.split(":", 1)[1].strip()
            if app_key:
                if self.app_combo.findText(app_key) < 0:
                    self.app_combo.addItem(app_key)
                self.app_combo.setCurrentText(app_key)
            return
        if raw.startswith("url:"):
            self._type_idx = 1
            self._refresh_type_ui()
            self.url_edit.setText(raw.split(":", 1)[1].strip())
            return
        if raw.startswith("bat:"):
            self._type_idx = 2
            self._refresh_type_ui()
            self.bat_edit.setText(raw.split(":", 1)[1].strip())
            return


class ScenarioEditorDialog(QDialog):
    def __init__(
        self,
        app_keys: list[str],
        copy_script_cb,
        add_program_cb=None,
        initial_name: str = "",
        initial_actions: list[str] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._app_keys = app_keys
        self._copy_script_cb = copy_script_cb
        self._add_program_cb = add_program_cb
        self._rows: list[ScenarioStepRow] = []
        self._result_name = ""
        self._result_actions: list[str] = []

        self.setWindowTitle("Конструктор сценария")
        self.setModal(True)
        self.resize(760, 520)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("название")
        root.addWidget(self.name_edit)

        body = QHBoxLayout()
        body.setSpacing(10)

        self.rows_host = QWidget()
        self.rows_layout = QVBoxLayout(self.rows_host)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(8)
        self.rows_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        body.addWidget(self.rows_host, 1)
        body.setAlignment(self.rows_host, Qt.AlignmentFlag.AlignTop)
        self.rows_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)

        side = QVBoxLayout()
        add_btn = QPushButton("+")
        add_btn.setFixedSize(36, 36)
        add_btn.setToolTip("Добавить шаг")
        add_btn.clicked.connect(self.add_row)
        side.addWidget(add_btn)
        side.addStretch()
        side.setAlignment(Qt.AlignmentFlag.AlignTop)
        body.addLayout(side)

        root.addLayout(body, 1)

        footer = QHBoxLayout()
        footer.addStretch()
        save_btn = QPushButton("Сохранить")
        save_btn.setFixedWidth(180)
        save_btn.clicked.connect(self._on_save)
        footer.addWidget(save_btn)
        root.addLayout(footer)

        if initial_name:
            self.name_edit.setText(initial_name)
        seed_actions = initial_actions or []
        if seed_actions:
            for action in seed_actions:
                self.add_row(action)
        else:
            self.add_row()

    def add_row(self, initial_action: str = ""):
        row = ScenarioStepRow(self._app_keys, self.remove_row, self._add_program_cb, self)
        if initial_action:
            row.apply_action(initial_action)
        self._rows.append(row)
        self.rows_layout.addWidget(row)

    def remove_row(self, row: ScenarioStepRow | None = None):
        if len(self._rows) <= 1:
            return
        if row is None:
            row = self._rows[-1]
        if row not in self._rows:
            return
        self._rows.remove(row)
        self.rows_layout.removeWidget(row)
        row.deleteLater()

    def _on_save(self):
        name = (self.name_edit.text() or "").strip()
        if not name:
            QMessageBox.warning(self, "Сценарий", "Укажи название сценария.")
            return
        actions: list[str] = []
        try:
            for row in self._rows:
                actions.append(row.to_action(self._copy_script_cb))
        except Exception as err:
            QMessageBox.warning(self, "Сценарий", str(err))
            return
        if not actions:
            QMessageBox.warning(self, "Сценарий", "Добавь хотя бы один шаг.")
            return
        self._result_name = name
        self._result_actions = actions
        self.accept()

    def scenario_name(self) -> str:
        return self._result_name

    def scenario_actions(self) -> list[str]:
        return list(self._result_actions)


class ProgramItemRow(QWidget):
    def __init__(self, name: str = "", path: str = "", on_remove=None, parent=None):
        super().__init__(parent)
        self._on_remove = on_remove
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.name_edit = QLineEdit(name)
        self.name_edit.setPlaceholderText("Название программы")
        self.path_edit = QLineEdit(path)
        self.path_edit.setPlaceholderText("Путь к .exe")

        browse_btn = QPushButton("...")
        browse_btn.setFixedWidth(36)
        browse_btn.clicked.connect(self._browse_exe)

        remove_btn = QPushButton("−")
        remove_btn.setFixedWidth(36)
        remove_btn.clicked.connect(self._remove_self)

        layout.addWidget(self.name_edit, 2)
        layout.addWidget(self.path_edit, 4)
        layout.addWidget(browse_btn)
        layout.addWidget(remove_btn)

    def _browse_exe(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Выбери .exe программы",
            str(Path.home()),
            "Программы (*.exe);;Все файлы (*.*)",
        )
        if file_path:
            self.path_edit.setText(file_path)
            if not self.name_edit.text().strip():
                self.name_edit.setText(Path(file_path).stem)

    def _remove_self(self):
        if callable(self._on_remove):
            self._on_remove(self)


class SiteItemRow(QWidget):
    def __init__(self, name: str = "", url: str = "", on_remove=None, parent=None):
        super().__init__(parent)
        self._on_remove = on_remove
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.name_edit = QLineEdit(name)
        self.name_edit.setPlaceholderText("Название сайта")
        self.url_edit = QLineEdit(url)
        self.url_edit.setPlaceholderText("https://example.com")

        remove_btn = QPushButton("−")
        remove_btn.setFixedWidth(36)
        remove_btn.clicked.connect(self._remove_self)

        layout.addWidget(self.name_edit, 2)
        layout.addWidget(self.url_edit, 4)
        layout.addWidget(remove_btn)

    def _remove_self(self):
        if callable(self._on_remove):
            self._on_remove(self)


class ProgramsManagerDialog(QDialog):
    def __init__(self, programs: dict[str, str], parent=None):
        super().__init__(parent)
        self._rows: list[ProgramItemRow] = []
        self._result: dict[str, str] = {}
        self.setWindowTitle("Программы")
        self.setModal(True)
        self.resize(820, 520)

        root = QVBoxLayout(self)
        self.rows_host = QWidget()
        self.rows_layout = QVBoxLayout(self.rows_host)
        self.rows_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        root.addWidget(self.rows_host, 1)

        for name, path in (programs or {}).items():
            self.add_row(str(name), str(path))
        if not self._rows:
            self.add_row()

        controls = QHBoxLayout()
        add_btn = QPushButton("+")
        add_btn.setFixedWidth(36)
        add_btn.clicked.connect(lambda: self.add_row())
        save_btn = QPushButton("Сохранить")
        save_btn.setFixedWidth(170)
        save_btn.clicked.connect(self._on_save)
        controls.addWidget(add_btn)
        controls.addStretch()
        controls.addWidget(save_btn)
        root.addLayout(controls)

    def add_row(self, name: str = "", path: str = ""):
        row = ProgramItemRow(name, path, self.remove_row, self)
        self._rows.append(row)
        self.rows_layout.addWidget(row)

    def remove_row(self, row: ProgramItemRow):
        if row not in self._rows:
            return
        self._rows.remove(row)
        self.rows_layout.removeWidget(row)
        row.deleteLater()
        if not self._rows:
            self.add_row()

    def _on_save(self):
        result: dict[str, str] = {}
        for row in self._rows:
            name = (row.name_edit.text() or "").strip()
            path = (row.path_edit.text() or "").strip()
            if not name and not path:
                continue
            if not name or not path:
                QMessageBox.warning(self, "Программы", "У каждой строки должны быть и название, и путь.")
                return
            key = MainWindow._normalize_config_key(name)
            if not key:
                QMessageBox.warning(self, "Программы", f"Некорректное имя: {name}")
                return
            result[key] = path.replace("\\", "/")
        self._result = result
        self.accept()

    def programs(self) -> dict[str, str]:
        return dict(self._result)


class SitesManagerDialog(QDialog):
    def __init__(self, sites: dict[str, str], parent=None):
        super().__init__(parent)
        self._rows: list[SiteItemRow] = []
        self._result: dict[str, str] = {}
        self.setWindowTitle("Сайты")
        self.setModal(True)
        self.resize(820, 520)

        root = QVBoxLayout(self)
        self.rows_host = QWidget()
        self.rows_layout = QVBoxLayout(self.rows_host)
        self.rows_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        root.addWidget(self.rows_host, 1)

        for name, url in (sites or {}).items():
            self.add_row(str(name), str(url))
        if not self._rows:
            self.add_row()

        controls = QHBoxLayout()
        add_btn = QPushButton("+")
        add_btn.setFixedWidth(36)
        add_btn.clicked.connect(lambda: self.add_row())
        save_btn = QPushButton("Сохранить")
        save_btn.setFixedWidth(170)
        save_btn.clicked.connect(self._on_save)
        controls.addWidget(add_btn)
        controls.addStretch()
        controls.addWidget(save_btn)
        root.addLayout(controls)

    def add_row(self, name: str = "", url: str = ""):
        row = SiteItemRow(name, url, self.remove_row, self)
        self._rows.append(row)
        self.rows_layout.addWidget(row)

    def remove_row(self, row: SiteItemRow):
        if row not in self._rows:
            return
        self._rows.remove(row)
        self.rows_layout.removeWidget(row)
        row.deleteLater()
        if not self._rows:
            self.add_row()

    def _on_save(self):
        result: dict[str, str] = {}
        for row in self._rows:
            name = (row.name_edit.text() or "").strip()
            url = (row.url_edit.text() or "").strip()
            if not name and not url:
                continue
            if not name or not url:
                QMessageBox.warning(self, "Сайты", "У каждой строки должны быть и название, и ссылка.")
                return
            if not re.match(r"^https?://", url, flags=re.IGNORECASE):
                url = "https://" + url
            key = MainWindow._normalize_config_key(name)
            if not key:
                QMessageBox.warning(self, "Сайты", f"Некорректное имя: {name}")
                return
            result[key] = url
        self._result = result
        self.accept()

    def sites(self) -> dict[str, str]:
        return dict(self._result)


class ScenarioListRow(QWidget):
    def __init__(self, name: str, on_edit=None, on_remove=None, parent=None):
        super().__init__(parent)
        self.name = name
        self._on_edit = on_edit
        self._on_remove = on_remove
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.name_label = QLabel(name)
        edit_btn = QPushButton("Редактировать")
        edit_btn.clicked.connect(self._edit_self)
        remove_btn = QPushButton("−")
        remove_btn.setFixedWidth(36)
        remove_btn.clicked.connect(self._remove_self)

        layout.addWidget(self.name_label, 1)
        layout.addWidget(edit_btn)
        layout.addWidget(remove_btn)

    def _edit_self(self):
        if callable(self._on_edit):
            self._on_edit(self.name)

    def _remove_self(self):
        if callable(self._on_remove):
            self._on_remove(self.name)


class ScenariosManagerDialog(QDialog):
    def __init__(self, scenarios: dict[str, list], on_add=None, on_edit=None, on_remove=None, parent=None):
        super().__init__(parent)
        self._scenarios = scenarios or {}
        self._on_add = on_add
        self._on_edit = on_edit
        self._on_remove = on_remove

        self.setWindowTitle("Сценарии")
        self.setModal(True)
        self.resize(760, 520)

        root = QVBoxLayout(self)
        self.rows_host = QWidget()
        self.rows_layout = QVBoxLayout(self.rows_host)
        self.rows_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        root.addWidget(self.rows_host, 1)

        controls = QHBoxLayout()
        add_btn = QPushButton("+")
        add_btn.setFixedWidth(36)
        add_btn.clicked.connect(self._add_new)
        close_btn = QPushButton("Закрыть")
        close_btn.setFixedWidth(170)
        close_btn.clicked.connect(self.accept)
        controls.addWidget(add_btn)
        controls.addStretch()
        controls.addWidget(close_btn)
        root.addLayout(controls)

        self.refresh_rows()

    def refresh_rows(self):
        while self.rows_layout.count():
            item = self.rows_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        names = sorted([str(k).strip() for k in self._scenarios.keys() if str(k).strip()])
        if not names:
            self.rows_layout.addWidget(QLabel("Сценариев пока нет. Нажми + для добавления."))
            return
        for name in names:
            self.rows_layout.addWidget(ScenarioListRow(name, self._edit_name, self._remove_name, self))

    def _add_new(self):
        if callable(self._on_add) and self._on_add():
            self.refresh_rows()

    def _edit_name(self, name: str):
        if callable(self._on_edit) and self._on_edit(name):
            self.refresh_rows()

    def _remove_name(self, name: str):
        if callable(self._on_remove) and self._on_remove(name):
            self.refresh_rows()

    def set_scenarios(self, scenarios: dict[str, list]):
        self._scenarios = scenarios or {}
        self.refresh_rows()


class MainWindow(QMainWindow):
    def __init__(self, engine: JarvisEngine, bus: LogBus):
        super().__init__()
        self.engine = engine
        self.bus = bus
        self.tray: QSystemTrayIcon | None = None
        self._quit_requested = False
        self._ptt_hotkey = "f6"  # По умолчанию F6
        self._recording_key = False  # Флаг записи клавиши
        self._recorded_combo = []  # Комбинация клавиш во время записи
        self._openai_key_warn_shown = False
        self._pv_key_warn_shown = False
        self._runtime_log_path = Path.home() / ".jarvis" / "runtime.log"
        self._avatar_pulse_timer = QTimer(self)
        self._avatar_pulse_timer.setSingleShot(True)
        self._avatar_pulse_timer.timeout.connect(self._stop_avatar_animation)
        self._log_collapsed = False
        self._avatar_size_normal = 170
        self._avatar_inner_normal = 152
        self._avatar_size_focus_min = 260
        self._avatar_size_focus_max = 430
        self._avatar_state = "unknown"
        self._avatar_accent = QColor("#3B82F6")  # default: blue (waiting wake-word)
        self._avatar_shadow_base = QColor(59, 130, 246, 80)
        self._loading_anim_active = False
        self._mic_monitor_stream = None
        self._mic_monitor_active = False
        self._mic_level_stream = None
        self._mic_level_active = False
        self._mic_level_rms = 0.0  # raw RMS (0..1)
        self._mic_level_norm = 0.0  # normalized level (0..1)
        self._mic_auto_enabled = False
        self._mic_noise_est = 0.02
        self._mic_auto_last_save = 0.0
        self._mic_level_timer = QTimer(self)
        self._mic_level_timer.timeout.connect(self._update_mic_level_ui)
        self._mic_level_timer.start(50)

        self.setWindowTitle("Jarvis Assistant")
        self.resize(900, 700)
        # Фиксированный размер окна: основная вкладка "выжимается" в этот размер,
        # а в "Настройках" используется скролл.
        self.setFixedSize(self.size())
        self._apply_light_theme()

        # Навигация без вкладок: основной экран + экран настроек
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(10)

        self._stack = QStackedWidget()
        self.main_tab = QWidget()
        self.settings_tab = QWidget()
        self.setup_main_tab()
        self.setup_settings_tab()
        self._stack.addWidget(self.main_tab)
        self._stack.addWidget(self.settings_tab)

        root_layout.addWidget(self._stack)
        self.setCentralWidget(root)

        self.bus.log.connect(self.append_log)
        self.engine.set_asr_ready_callback(self._schedule_asr_ready_tray_notification)
        self.load_devices()
        self.load_audio_settings()
        
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_buttons)
        self.timer.start(100)

    def _show_settings(self):
        if hasattr(self, "_stack"):
            self._stack.setCurrentWidget(self.settings_tab)

    def _show_main(self):
        if hasattr(self, "_stack"):
            self._stack.setCurrentWidget(self.main_tab)

    def _startup_script_path(self) -> Path:
        startup_dir = Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        return startup_dir / "JarvisAssistantAutostart.cmd"

    def _is_autostart_enabled(self) -> bool:
        return self._startup_script_path().exists()

    def _set_autostart_enabled(self, enabled: bool):
        script_path = self._startup_script_path()

        if enabled:
            project_root = Path(__file__).resolve().parents[1]
            python_executable = Path(sys.executable)
            script_content = (
                "@echo off\n"
                f"cd /d \"{project_root}\"\n"
                f"\"{python_executable}\" -m gui.app --minimized\n"
            )
            script_path.parent.mkdir(parents=True, exist_ok=True)
            script_path.write_text(script_content, encoding="utf-8")
            return

        if script_path.exists():
            script_path.unlink()

    def set_tray(self, tray: QSystemTrayIcon):
        self.tray = tray

    def _schedule_asr_ready_tray_notification(self):
        if not self.tray:
            return
        QTimer.singleShot(0, self._show_asr_ready_tray_notification)

    def _show_asr_ready_tray_notification(self):
        if not self.tray:
            return
        self.tray.showMessage(
            "Jarvis Assistant",
            "Модель распознавания загружена. Можно нажать «Старт».",
            QSystemTrayIcon.MessageIcon.Information,
            4500,
        )

    def show_and_raise(self):
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def closeEvent(self, event):
        if self._quit_requested:
            event.accept()
            return

        event.ignore()
        self.hide()
        # На всякий случай останавливаем мониторинг микрофона, если он был включен.
        try:
            self._stop_mic_monitoring()
        except Exception:
            pass
        if self.tray:
            self.tray.showMessage(
                "Jarvis Assistant",
                "Приложение свернуто в трей и продолжает работать в фоне.",
                QSystemTrayIcon.MessageIcon.Information,
                2500,
            )

    def changeEvent(self, event):
        if event.type() == QEvent.Type.WindowStateChange and self.isMinimized():
            QTimer.singleShot(0, self.hide)
        super().changeEvent(event)
    
    def setup_main_tab(self):
        """Настройка основной вкладки"""
        layout = QVBoxLayout()

        # Центральный визуальный блок ассистента
        avatar_wrap = QFrame()
        avatar_wrap.setObjectName("avatarWrap")
        avatar_wrap.setSizePolicy(
            avatar_wrap.sizePolicy().horizontalPolicy(),
            avatar_wrap.sizePolicy().Policy.Expanding,
        )
        avatar_wrap_layout = QVBoxLayout(avatar_wrap)
        avatar_wrap_layout.setContentsMargins(0, 8, 0, 8)
        avatar_wrap_layout.setSpacing(8)

        self.avatar_label = QLabel()
        self.avatar_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._apply_avatar_size(self._avatar_size_normal, self._avatar_inner_normal)
        self.avatar_shadow = QGraphicsDropShadowEffect(self.avatar_label)
        self.avatar_shadow.setBlurRadius(20)
        self.avatar_shadow.setColor(QColor(104, 150, 255, 80))
        self.avatar_shadow.setOffset(0, 0)
        self.avatar_label.setGraphicsEffect(self.avatar_shadow)

        self.avatar_pulse_anim = QPropertyAnimation(self.avatar_shadow, b"blurRadius", self)
        self.avatar_pulse_anim.setDuration(760)
        self.avatar_pulse_anim.setStartValue(20)
        self.avatar_pulse_anim.setKeyValueAt(0.5, 68)
        self.avatar_pulse_anim.setEndValue(20)
        self.avatar_pulse_anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self.avatar_pulse_anim.setLoopCount(-1)

        self.user_text_label = QLabel("Скажи «Джарвис», затем команду.")
        self.user_text_label.setObjectName("heardText")
        self.user_text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.user_text_label.setWordWrap(True)
        self.user_text_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.user_text_label.setFixedHeight(42)

        avatar_wrap_layout.addWidget(self.avatar_label, alignment=Qt.AlignmentFlag.AlignCenter)
        avatar_wrap_layout.addWidget(self.user_text_label)
        layout.addWidget(avatar_wrap)
        
        # Логирование
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setObjectName("logView")
        log_header = QHBoxLayout()
        log_header.addWidget(QLabel("Лог:"))
        log_header.addStretch()
        self.btn_toggle_log = QPushButton("▾")
        self.btn_toggle_log.setToolTip("Свернуть/развернуть лог")
        self.btn_toggle_log.setFixedWidth(34)
        self.btn_toggle_log.clicked.connect(self.on_toggle_log)
        log_header.addWidget(self.btn_toggle_log)
        layout.addLayout(log_header)
        layout.addWidget(self.log_view)
        
        # Кнопка очистки лога
        self.clear_log_btn = QPushButton("🗑 Очистить лог")
        self.clear_log_btn.clicked.connect(self.on_clear_log)
        layout.addWidget(self.clear_log_btn)
        
        # Кнопки управления
        button_layout = QHBoxLayout()
        self.btn_start = QPushButton("▶ Старт")
        self.btn_stop = QPushButton("⏹ Стоп")
        self.btn_start.clicked.connect(self.engine.start)
        self.btn_stop.clicked.connect(self.engine.stop)
        self.btn_stop.setEnabled(False)

        self.btn_open_settings = QPushButton("⚙ Настройки")
        self.btn_open_settings.setToolTip("Открыть настройки")
        self.btn_open_settings.clicked.connect(self._show_settings)

        button_layout.addWidget(self.btn_start)
        button_layout.addWidget(self.btn_stop)
        button_layout.addWidget(self.btn_open_settings)
        layout.addLayout(button_layout)
        
        self.main_tab.setLayout(layout)
        self._update_avatar_for_state()

    def _apply_avatar_size(self, size: int, inner_size: int):
        self.avatar_label.setFixedSize(size, size)
        self.avatar_label.setPixmap(self._make_avatar_pixmap(inner_size))
        radius = size // 2
        border = self._avatar_accent.name() if hasattr(self, "_avatar_accent") else "#DDE6F2"
        self.avatar_label.setStyleSheet(
            f"border: 3px solid {border}; border-radius: {radius}px; background: #FFFFFF;"
        )

    def _set_avatar_state(self, state: str):
        """Меняет цвет центрального "крючка" по режиму."""
        state = (state or "").strip().lower() or "unknown"
        if state == self._avatar_state:
            return
        self._avatar_state = state

        if state == "armed":
            accent = QColor("#22C55E")  # green
            shadow = QColor(34, 197, 94, 90)
        elif state == "loading":
            accent = QColor("#F59E0B")  # orange
            shadow = QColor(245, 158, 11, 110)
        elif state == "idle":
            accent = QColor("#EF4444")  # red
            shadow = QColor(239, 68, 68, 90)
        else:
            accent = QColor("#3B82F6")  # blue (running/waiting wake-word)
            shadow = QColor(59, 130, 246, 90)

        self._avatar_accent = accent
        self._avatar_shadow_base = shadow

        # если прямо сейчас не "пульсируем", ставим базовый цвет подсветки
        if self.avatar_pulse_anim.state() != QPropertyAnimation.State.Running:
            self.avatar_shadow.setColor(self._avatar_shadow_base)
        self._update_avatar_for_state()

    def _set_loading_animation(self, active: bool):
        active = bool(active)
        if active == self._loading_anim_active:
            return
        self._loading_anim_active = active
        if active:
            # постоянная "пульсация" во время загрузки модели
            if self.avatar_pulse_anim.state() != QPropertyAnimation.State.Running:
                self.avatar_pulse_anim.start()
            self._avatar_pulse_timer.stop()  # не останавливаем по таймеру
        else:
            # выходим из "loading" — оставляем базовую подсветку по текущему состоянию
            self._stop_avatar_animation()

    def _update_avatar_for_state(self):
        """Адаптивный размер аватара: в визуальном режиме крупнее."""
        if not hasattr(self, "avatar_label"):
            return
        if not self._log_collapsed:
            self._apply_avatar_size(self._avatar_size_normal, self._avatar_inner_normal)
            return

        # В визуальном режиме пытаемся занять большую часть доступного места.
        w = int(self.main_tab.width() * 0.62)
        h = int(self.main_tab.height() * 0.48)
        size = max(self._avatar_size_focus_min, min(self._avatar_size_focus_max, min(w, h)))
        inner = max(120, size - 22)
        self._apply_avatar_size(size, inner)

    def on_toggle_log(self):
        self._log_collapsed = not self._log_collapsed
        self.log_view.setVisible(not self._log_collapsed)
        self.clear_log_btn.setVisible(not self._log_collapsed)
        self.btn_toggle_log.setText("▸" if self._log_collapsed else "▾")
        if self._log_collapsed:
            self.user_text_label.setText("Визуальный режим. Скажи «Джарвис», затем команду.")
        self._update_avatar_for_state()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # При изменении размера окна пересчитываем аватар в визуальном режиме.
        if getattr(self, "_log_collapsed", False):
            self._update_avatar_for_state()

    def _make_avatar_pixmap(self, size: int) -> QPixmap:
        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Внешнее кольцо
        accent = self._avatar_accent if hasattr(self, "_avatar_accent") else QColor("#7EA8FF")
        ring = QColor(accent)
        ring.setAlpha(190)
        painter.setPen(QPen(ring, 2))
        painter.setBrush(QBrush(QColor("#F7FAFF")))
        painter.drawEllipse(1, 1, size - 2, size - 2)

        # Внутренний диск
        pad = int(size * 0.14)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor("#EAF2FF")))
        painter.drawEllipse(pad, pad, size - 2 * pad, size - 2 * pad)

        # Точка-ядро
        core = int(size * 0.23)
        core_x = (size - core) // 2
        core_y = (size - core) // 2
        painter.setBrush(QBrush(accent))
        painter.drawEllipse(core_x, core_y, core, core)
        painter.end()
        return pix

    def _start_avatar_animation(self, duration_ms: int = 1800):
        if self.avatar_pulse_anim.state() != QPropertyAnimation.State.Running:
            self.avatar_pulse_anim.start()
        # более яркая подсветка тем же акцентом
        c = QColor(self._avatar_accent)
        c.setAlpha(160)
        self.avatar_shadow.setColor(c)
        self._avatar_pulse_timer.start(max(600, duration_ms))

    def _stop_avatar_animation(self):
        if self.avatar_pulse_anim.state() == QPropertyAnimation.State.Running:
            self.avatar_pulse_anim.stop()
        self.avatar_shadow.setBlurRadius(20)
        self.avatar_shadow.setColor(self._avatar_shadow_base if hasattr(self, "_avatar_shadow_base") else QColor(104, 150, 255, 80))

    def _apply_light_theme(self):
        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background: transparent;
                color: #1F2A37;
            }
            QMainWindow {
                background: #F6F8FC;
            }
            QTabWidget::pane {
                border: 1px solid #E4E8F0;
                border-radius: 8px;
                background: #FFFFFF;
            }
            QTabBar::tab {
                background: #EEF2FA;
                color: #334155;
                padding: 8px 14px;
                margin-right: 4px;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
            }
            QTabBar::tab:selected {
                background: #FFFFFF;
                color: #0F172A;
                border: 1px solid #E4E8F0;
                border-bottom: 1px solid #FFFFFF;
            }
            QPushButton {
                background: #EEF2FF;
                border: 1px solid #D9DEFF;
                border-radius: 8px;
                padding: 8px 12px;
                color: #3730A3;
                font-weight: 600;
                font-family: 'Segoe UI', 'Segoe UI Emoji';
            }
            QPushButton:hover {
                background: #E0E7FF;
            }
            QPushButton:disabled {
                color: #94A3B8;
                background: #F1F5F9;
                border: 1px solid #E2E8F0;
            }
            QComboBox, QLineEdit, QDoubleSpinBox, QTextEdit {
                background: #FFFFFF;
                border: 1px solid #DCE3ED;
                border-radius: 8px;
                padding: 6px;
                color: #1F2937;
            }
            QToolButton#spinStepUp, QToolButton#spinStepDown {
                border: none;
                background: transparent;
                color: #334155;
                padding: 0px;
                margin: 0px;
                font-size: 11px;
                font-weight: 800;
            }
            QToolButton#spinStepUp:hover, QToolButton#spinStepDown:hover {
                background: #EEF2FF;
                border-radius: 6px;
            }
            QToolButton#spinStepUp:pressed, QToolButton#spinStepDown:pressed {
                background: #E0E7FF;
                border-radius: 6px;
            }
            QCheckBox {
                spacing: 10px;
                color: #0F172A;
                font-weight: 600;
            }
            QCheckBox::indicator:unchecked {
                width: 18px;
                height: 18px;
                border-radius: 5px;
                border: 1px solid #CBD5E1;
                background: #FFFFFF;
            }
            QCheckBox::indicator:checked {
                width: 18px;
                height: 18px;
            }
            QGroupBox {
                background: #FFFFFF;
                border: 1px solid #CBD5E1;
                border-radius: 10px;
                margin-top: 0px;
                padding: 14px;
            }
            QLabel#sectionHeader {
                color: #0F172A;
                font-size: 12px;
                font-weight: 900;
                padding: 2px 2px 0px 2px;
            }
            QToolButton#helpBtn {
                border: 1px solid #DCE3ED;
                border-radius: 9px;
                width: 18px;
                height: 18px;
                margin-left: 8px;
                padding: 0px;
                background: #FFFFFF;
                color: #475569;
                font-weight: 800;
            }
            QToolButton#helpBtn:hover {
                background: #EEF2FF;
                border: 1px solid #C7D2FE;
                color: #3730A3;
            }
            QProgressBar {
                border: 1px solid #DCE3ED;
                border-radius: 7px;
                background: #F1F5F9;
                text-align: center;
            }
            QProgressBar::chunk {
                background: #8BB4FF;
                border-radius: 6px;
            }
            QFrame#avatarWrap {
                border: 1px solid #E7ECF4;
                border-radius: 12px;
                background: #FFFFFF;
            }
            QLabel#heardText {
                color: #334155;
                font-size: 13px;
                padding: 2px 12px 6px 12px;
            }
            QTextEdit#logView {
                background: #FCFDFF;
                border: 1px solid #E6EBF4;
                border-radius: 10px;
                font-family: Consolas, 'Cascadia Mono', monospace;
                font-size: 12px;
            }
            """
        )

    @staticmethod
    def _help_button(text: str) -> QToolButton:
        btn = QToolButton()
        btn.setObjectName("helpBtn")
        btn.setText("?")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip(text)
        btn.clicked.connect(lambda _=False, t=text, b=btn: QToolTip.showText(QCursor.pos(), t, b))
        btn.setAutoRaise(False)
        return btn

    def on_tab_changed(self, index):
        _ = index

    @staticmethod
    def _section_separator() -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Plain)
        line.setStyleSheet("color: #CBD5E1; background: #CBD5E1; min-height: 1px; max-height: 1px;")
        return line

    @staticmethod
    def _section_header(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("sectionHeader")
        return lbl
    
    def setup_settings_tab(self):
        """Настройка вкладки настроек"""
        # Делаем "Настройки" прокручиваемыми: много параметров не влезает в фиксированное окно.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(12)
        
        layout.addWidget(self._section_header("Микрофон"))
        mic_group = QGroupBox("")
        mic_form = QFormLayout(mic_group)
        mic_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        mic_form.setContentsMargins(10, 10, 10, 10)
        mic_form.setVerticalSpacing(10)

        mic_row = QHBoxLayout()
        mic_row.setSpacing(8)
        self.device_combo = NoWheelComboBox()
        self.device_combo.currentIndexChanged.connect(self.on_device_changed)
        mic_row.addWidget(self.device_combo, 1)
        mic_row.addSpacing(6)

        self.btn_refresh_devices = QPushButton("Обновить")
        self.btn_refresh_devices.setToolTip("Если переподключил микрофон — нажми, чтобы обновить список устройств.")
        self.btn_refresh_devices.clicked.connect(self.load_devices)
        mic_row.addWidget(self.btn_refresh_devices)

        self.btn_test_mic = QPushButton("Тест")
        self.btn_test_mic.setToolTip("Короткий тест микрофона (3 секунды).")
        self.btn_test_mic.clicked.connect(self.on_test_microphone)
        mic_row.addWidget(self.btn_test_mic)

        mic_form.addRow("Устройство:", mic_row)

        # Чувствительность (как в Discord): один компонент "уровень + порог"
        self.mic_auto_checkbox = QCheckBox("Автоматически определять чувствительность ввода")
        self.mic_auto_checkbox.setToolTip("Как в Discord: порог подбирается автоматически по уровню шума.")
        self.mic_auto_checkbox.stateChanged.connect(self.on_mic_auto_toggled)
        mic_form.addRow(self.mic_auto_checkbox)

        self.mic_sensitivity = MicSensitivitySlider()
        self.mic_sensitivity.thresholdChanged.connect(self.on_mic_threshold_changed_01)
        mic_form.addRow("", self.mic_sensitivity)

        self.mic_test_result = QLabel("")
        self.mic_test_result.setStyleSheet("color: #475569; font-size: 11px;")
        self.mic_test_result.setWordWrap(True)
        self.mic_test_result.setVisible(False)

        layout.addWidget(mic_group)
        
        layout.addWidget(self._section_header("Распознавание и активация"))
        asr_group = QGroupBox("")
        asr_form = QFormLayout(asr_group)
        asr_form.setContentsMargins(10, 10, 10, 10)
        asr_form.setVerticalSpacing(10)

        wake_row = QHBoxLayout()
        self.wake_engine_combo = NoWheelComboBox()
        self.wake_engine_combo.addItem("Vosk (текстовый)", "vosk_text")
        self.wake_engine_combo.addItem("Porcupine (Picovoice)", "porcupine")
        self.wake_engine_combo.currentIndexChanged.connect(self.on_wake_engine_changed)
        wake_row.addWidget(self.wake_engine_combo, 1)
        wake_row.addWidget(
            self._help_button(
                "Движок активации (wake-word):\n\n"
                "• Vosk (текстовый) — проще, без ключей, может быть менее точным.\n"
                "• Porcupine (Picovoice) — быстрый wake-word, обычно точнее, но нужен ключ Picovoice.\n\n"
                "Рекомендация: если есть ключ — Porcupine; если нет — Vosk."
            )
        )
        asr_form.addRow("Движок активации:", wake_row)

        self.wake_engine_combo.blockSignals(True)
        current_engine = getattr(self.engine, "wakeword_engine", "vosk_text")
        current_idx = self.wake_engine_combo.findData(current_engine)
        self.wake_engine_combo.setCurrentIndex(current_idx if current_idx >= 0 else 0)
        self.wake_engine_combo.blockSignals(False)
        
        timeout_row = QHBoxLayout()
        self.timeout_spinbox = OverlayStepperDoubleSpinBox()
        self.timeout_spinbox.setMinimum(1.0)
        self.timeout_spinbox.setMaximum(30.0)
        self.timeout_spinbox.setValue(6.0)
        self.timeout_spinbox.setSingleStep(0.5)
        self.timeout_spinbox.setDecimals(1)
        self.timeout_spinbox.setMinimumWidth(84)
        self.timeout_spinbox.setToolTip("Максимальное время ожидания одной команды.")
        self.timeout_spinbox.valueChanged.connect(self.on_phrase_timeout_changed)
        timeout_row.addWidget(self.timeout_spinbox)
        timeout_row.addWidget(
            self._help_button(
                "Таймаут фразы — максимальное время, которое ассистент ждёт одну команду.\n"
                "Если пользователь говорит дольше — команда может обрезаться."
            )
        )
        timeout_row.addStretch()
        asr_form.addRow("Таймаут фразы (сек):", timeout_row)
        
        silence_row = QHBoxLayout()
        self.silence_spinbox = OverlayStepperDoubleSpinBox()
        self.silence_spinbox.setMinimum(0.1)
        self.silence_spinbox.setMaximum(10.0)
        self.silence_spinbox.setValue(1.2)
        self.silence_spinbox.setSingleStep(0.1)
        self.silence_spinbox.setDecimals(1)
        self.silence_spinbox.setMinimumWidth(84)
        self.silence_spinbox.setToolTip("Через сколько секунд тишины считать команду законченной.")
        self.silence_spinbox.valueChanged.connect(self.on_silence_timeout_changed)
        silence_row.addWidget(self.silence_spinbox)
        silence_row.addWidget(
            self._help_button(
                "Таймаут молчания — сколько ждать тишину внутри команды.\n"
                "Меньше значение — быстрее завершает распознавание, но может обрезать паузы."
            )
        )
        silence_row.addStretch()
        asr_form.addRow("Таймаут молчания (сек):", silence_row)

        layout.addWidget(asr_group)

        layout.addWidget(self._section_header("Озвучка (TTS)"))
        tts_group = QGroupBox("")
        tts_form = QFormLayout(tts_group)
        tts_form.setContentsMargins(10, 10, 10, 10)
        tts_form.setVerticalSpacing(10)

        self.tts_enabled_checkbox = QCheckBox("Включить озвучку")
        self.tts_enabled_checkbox.stateChanged.connect(self.on_tts_enabled_changed)
        tts_form.addRow(self.tts_enabled_checkbox)

        # Ползунок скорости речи
        self.tts_rate_value = QLabel("182")
        self.tts_rate_value.setStyleSheet("color: #64748B;")
        self.tts_rate_slider = NoWheelSlider(Qt.Orientation.Horizontal)
        self.tts_rate_slider.setMinimum(120)
        self.tts_rate_slider.setMaximum(240)
        self.tts_rate_slider.setSingleStep(1)
        self.tts_rate_slider.setPageStep(5)
        self.tts_rate_slider.valueChanged.connect(self.on_tts_rate_changed)
        rate_row = QHBoxLayout()
        rate_row.addWidget(self.tts_rate_slider, stretch=1)
        rate_row.addWidget(self.tts_rate_value)
        rate_row.addWidget(self._help_button("Скорость речи: выше — быстрее, ниже — медленнее."))
        tts_form.addRow("Скорость речи:", rate_row)

        # Ползунок громкости
        self.tts_volume_value = QLabel("95%")
        self.tts_volume_value.setStyleSheet("color: #64748B;")
        self.tts_volume_slider = NoWheelSlider(Qt.Orientation.Horizontal)
        self.tts_volume_slider.setMinimum(0)
        self.tts_volume_slider.setMaximum(100)
        self.tts_volume_slider.setSingleStep(1)
        self.tts_volume_slider.setPageStep(5)
        self.tts_volume_slider.valueChanged.connect(self.on_tts_volume_changed)
        vol_row = QHBoxLayout()
        vol_row.addWidget(self.tts_volume_slider, stretch=1)
        vol_row.addWidget(self.tts_volume_value)
        vol_row.addWidget(self._help_button("Громкость озвучки (0–100%)."))
        tts_form.addRow("Громкость:", vol_row)

        self.post_tts_spin = OverlayStepperDoubleSpinBox()
        self.post_tts_spin.setMinimum(0.1)
        self.post_tts_spin.setMaximum(2.5)
        self.post_tts_spin.setSingleStep(0.05)
        self.post_tts_spin.setDecimals(2)
        self.post_tts_spin.setToolTip("Чтобы микрофон не ловил собственную озвучку")
        self.post_tts_spin.valueChanged.connect(self.on_post_tts_delay_changed)
        self.btn_test_tts = QPushButton("🔊 Тест озвучки")
        self.btn_test_tts.clicked.connect(self.on_test_tts)
        post_row = QHBoxLayout()
        post_row.addWidget(self.post_tts_spin)
        post_row.addWidget(self._help_button("Пауза после TTS: микрофон ждёт, чтобы не услышать собственный голос."))
        post_row.addStretch()
        tts_form.addRow("Пауза микрофона (сек):", post_row)
        tts_form.addRow(self.btn_test_tts)

        layout.addWidget(tts_group)
        
        layout.addWidget(self._section_header("Дополнительно"))
        misc_group = QGroupBox("")
        misc_layout = QVBoxLayout(misc_group)
        misc_layout.setContentsMargins(10, 10, 10, 10)
        misc_layout.setSpacing(10)

        autostart_layout = QHBoxLayout()
        self.autostart_checkbox = QCheckBox("Автозапуск Jarvis при входе в Windows (в трее)")
        self.autostart_checkbox.setToolTip("Запускать приложение автоматически в фоне с предзагрузкой модели")
        self.autostart_checkbox.stateChanged.connect(self.on_autostart_toggled)
        autostart_layout.addWidget(self.autostart_checkbox)
        autostart_layout.addStretch()
        misc_layout.addLayout(autostart_layout)

        self.autostart_checkbox.blockSignals(True)
        self.autostart_checkbox.setChecked(self._is_autostart_enabled())
        self.autostart_checkbox.blockSignals(False)
        
        ptt_layout = QHBoxLayout()
        self.ptt_checkbox = QCheckBox("Push-to-talk режим")
        self.ptt_checkbox.setToolTip("Удерживай горячую клавишу для записи команды без wake-word")
        self.ptt_checkbox.stateChanged.connect(self.on_ptt_toggled)
        ptt_layout.addWidget(self.ptt_checkbox)
        
        ptt_layout.addWidget(QLabel("Клавиша:"))
        self.ptt_key_input = QPushButton("F6")
        self.ptt_key_input.setMaximumWidth(120)
        self.ptt_key_input.setToolTip("Нажми для записи новой клавиши")
        self.ptt_key_input.clicked.connect(self.on_record_ptt_key)
        ptt_layout.addWidget(self.ptt_key_input)
        
        ptt_layout.addStretch()
        misc_layout.addLayout(ptt_layout)

        layout.addWidget(misc_group)

        layout.addWidget(self._section_header("AI (искусственный интеллект)"))
        ai_group = QGroupBox("")
        ai_layout = QVBoxLayout(ai_group)
        ai_layout.setContentsMargins(10, 10, 10, 10)
        ai_layout.setSpacing(10)

        ai_enable_layout = QHBoxLayout()
        self.ai_enabled_checkbox = QCheckBox("Включить AI для неизвестных команд")
        self.ai_enabled_checkbox.setToolTip("Если команда не распознана локально, вопрос отправляется в выбранный AI-провайдер")
        self.ai_enabled_checkbox.stateChanged.connect(self.on_ai_enabled_toggled)
        ai_enable_layout.addWidget(self.ai_enabled_checkbox)
        ai_enable_layout.addWidget(
            self._help_button(
                "AI используется только для неизвестных или свободных запросов.\n"
                "Локальные системные команды (таймеры, задачи, окна, системные действия) "
                "выполняются без AI.\n"
                "Ключи хранятся локально в keys.json (или через переменные окружения)."
            )
        )
        ai_enable_layout.addStretch()
        ai_layout.addLayout(ai_enable_layout)

        ai_model_layout = QHBoxLayout()
        ai_model_layout.addWidget(QLabel("Модель AI:"))
        self.ai_model_combo = NoWheelComboBox()
        self.ai_model_combo.addItem("gpt-4o-mini", "gpt-4o-mini")
        self.ai_model_combo.addItem("gpt-4.1-mini", "gpt-4.1-mini")
        self.ai_model_combo.addItem("gpt-4o", "gpt-4o")
        self.ai_model_combo.currentIndexChanged.connect(self.on_ai_model_changed)
        ai_model_layout.addWidget(self.ai_model_combo)

        self.ai_test_btn = QPushButton("🤖 Тест AI")
        self.ai_test_btn.setToolTip("Проверить, что выбранный AI-провайдер отвечает")
        self.ai_test_btn.clicked.connect(self.on_test_ai)
        ai_model_layout.addWidget(self.ai_test_btn)
        ai_layout.addLayout(ai_model_layout)

        chat_ctx_layout = QHBoxLayout()
        self.clear_ctx_btn = QPushButton("🧹 Очистить контекст")
        self.clear_ctx_btn.setToolTip("Очистить историю диалога для AI")
        self.clear_ctx_btn.clicked.connect(self.on_clear_chat_history)
        chat_ctx_layout.addWidget(self.clear_ctx_btn)
        chat_ctx_layout.addWidget(
            self._help_button(
                "Очищает историю сообщений для AI (chat context).\n"
                "Это полезно, если ассистент начал отвечать с учетом старой темы."
            )
        )
        chat_ctx_layout.addStretch()
        ai_layout.addLayout(chat_ctx_layout)

        layout.addWidget(ai_group)

        layout.addWidget(self._section_header("Ключи и конфигурация"))
        keys_group = QGroupBox("")
        keys_layout = QVBoxLayout(keys_group)
        keys_layout.setContentsMargins(10, 10, 10, 10)
        keys_layout.setSpacing(10)

        openai_key_layout = QHBoxLayout()
        openai_key_layout.addWidget(QLabel("Ключ OpenAI:"))
        self.openai_key_input = QLineEdit()
        self.openai_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.openai_key_input.setPlaceholderText("sk-...")
        openai_key_layout.addWidget(self.openai_key_input)

        openai_save_btn = QPushButton("💾 Сохранить OpenAI")
        openai_save_btn.clicked.connect(self.on_save_openai_key)
        openai_key_layout.addWidget(openai_save_btn)
        keys_layout.addLayout(openai_key_layout)

        self.openai_key_warning = QLabel("")
        self.openai_key_warning.setStyleSheet("color: #cc6600; font-size: 10px;")
        keys_layout.addWidget(self.openai_key_warning)

        pv_layout = QHBoxLayout()
        pv_layout.addWidget(QLabel("Ключ Porcupine (Picovoice):"))
        self.pv_key_input = QLineEdit()
        self.pv_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.pv_key_input.setPlaceholderText("picovoice-...")
        pv_layout.addWidget(self.pv_key_input)

        pv_save_btn = QPushButton("💾 Сохранить Porcupine")
        pv_save_btn.clicked.connect(self.on_save_pv_key)
        pv_layout.addWidget(pv_save_btn)
        keys_layout.addLayout(pv_layout)

        self.pv_key_warning = QLabel("")
        self.pv_key_warning.setStyleSheet("color: #cc6600; font-size: 10px;")
        keys_layout.addWidget(self.pv_key_warning)
        
        # Кнопки управления конфигом (в 2 строки, чтобы не перегружать интерфейс)
        config_buttons_layout = QVBoxLayout()
        config_row_1 = QHBoxLayout()
        config_row_2 = QHBoxLayout()

        open_config_btn = QPushButton("📄 Открыть config.json")
        open_config_btn.clicked.connect(self.on_open_config)
        config_row_1.addWidget(open_config_btn)
        
        reload_config_btn = QPushButton("🔄 Перезагрузить конфиг")
        reload_config_btn.clicked.connect(self.on_reload_config)
        config_row_1.addWidget(reload_config_btn)

        scan_apps_btn = QPushButton("🔎 Найти приложения")
        scan_apps_btn.setToolTip(
            "Ищет ~40 типовых программ (браузеры, IDE, мессенджеры, игры, Office…) и дописывает apps в config.json"
        )
        scan_apps_btn.clicked.connect(self.on_scan_apps)
        config_row_1.addWidget(scan_apps_btn)

        manage_apps_btn = QPushButton("📦 Программы")
        manage_apps_btn.setToolTip("Открыть список программ и управлять ими (+/-).")
        manage_apps_btn.clicked.connect(self.on_manage_programs)
        config_row_2.addWidget(manage_apps_btn)

        manage_sites_btn = QPushButton("🌐 Сайты")
        manage_sites_btn.setToolTip("Открыть список сайтов и управлять ими (+/-).")
        manage_sites_btn.clicked.connect(self.on_manage_sites)
        config_row_2.addWidget(manage_sites_btn)

        manage_scenarios_btn = QPushButton("🧩 Сценарии")
        manage_scenarios_btn.setToolTip("Открыть список сценариев и управлять ими (+/-).")
        manage_scenarios_btn.clicked.connect(self.on_manage_scenarios)
        config_row_2.addWidget(manage_scenarios_btn)

        config_buttons_layout.addLayout(config_row_1)
        config_buttons_layout.addLayout(config_row_2)

        layout.addLayout(config_buttons_layout)
        keys_layout.addLayout(config_buttons_layout)

        layout.addWidget(keys_group)
        
        layout.addStretch()
        scroll.setWidget(content)

        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        self.btn_back = QPushButton("← Назад")
        self.btn_back.setToolTip("Вернуться на главный экран")
        self.btn_back.setFixedWidth(120)
        self.btn_back.clicked.connect(self._show_main)
        header.addWidget(self.btn_back)
        header.addStretch()
        outer.addLayout(header)
        outer.addWidget(scroll)
        self.settings_tab.setLayout(outer)

    def refresh_buttons(self):
        # LOADING
        if getattr(self.engine, "is_loading", False):
            self._set_avatar_state("loading")
            self._set_loading_animation(True)
            self.user_text_label.setText("Загрузка модели распознавания…")
            self.btn_start.setEnabled(False)
            self.btn_stop.setEnabled(False)
            return

        # READY (после LOADING)
        if getattr(self.engine, "is_ready", False) and not getattr(self.engine, "is_running", False):
            self._set_loading_animation(False)
            self._set_avatar_state("idle")
            self.user_text_label.setText("Модель загружена. Нажми «Старт» или скажи «Джарвис».")
            self.btn_start.setEnabled(True)
            self.btn_stop.setEnabled(False)
            return

        # RUNNING
        if getattr(self.engine, "is_running", False):
            self._set_loading_animation(False)
            self._set_avatar_state("armed" if getattr(self.engine, "armed", False) else "running")
            self.btn_start.setEnabled(False)
            self.btn_stop.setEnabled(True)
            return

        # INIT / UNKNOWN
        self._set_loading_animation(False)
        self._set_avatar_state("idle")
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(False)



    def append_log(self, msg: str):
        raw = str(msg or "")
        recognized = re.search(r"Распознано:\s*(.+)$", raw)
        if recognized:
            self.user_text_label.setText(recognized.group(1).strip())
            self._start_avatar_animation(2200)
        elif "Скажи команду" in raw or "Слушаю следующую команду" in raw:
            self._start_avatar_animation(1200)
        elif "Не расслышал" in raw:
            self.user_text_label.setText("Не расслышал. Повтори команду.")
            self._stop_avatar_animation()

        formatted = self._format_log_message(msg)
        color = self._color_for_level(formatted["level"])
        escaped_text = html.escape(formatted["text"])
        line_html = (
            "<div style='margin:2px 0 2px 0; padding:6px 9px; "
            "border-radius:8px; background:#F4F7FD; border:1px solid #E6ECF7;'>"
            f"<span style='color:#64748B;'>[{formatted['timestamp']}]</span> "
            f"<span style='color:{color}; font-weight:700;'>[{formatted['level']}]</span> "
            f"<span style='color:#1F2937;'>{escaped_text}</span>"
            "</div>"
        )
        self.log_view.append(line_html)
        self._append_runtime_log_file(
            f'[{formatted["timestamp"]}] [{formatted["level"]}] {formatted["text"]}'
        )

        s = str(msg)
        if self.tray and (s.startswith("⏰ Напоминание:") or s.startswith("Напоминание:")):
            body = s.replace("⏰ ", "", 1).replace("Напоминание:", "", 1).strip()
            self.tray.showMessage(
                "Jarvis — Напоминание",
                body or "Время напоминания наступило.",
                QSystemTrayIcon.MessageIcon.Information,
                6000,
            )
        if self.tray and (s.startswith("⏱ Таймер: время вышло") or s.startswith("Таймер: время вышло")):
            body = s.replace("⏱ ", "", 1).replace("Таймер:", "", 1).strip()
            self.tray.showMessage(
                "Jarvis — Таймер",
                body or "Время таймера вышло.",
                QSystemTrayIcon.MessageIcon.Information,
                6000,
            )

        # Прогресс-бар убран: загрузка отображается анимацией/цветом аватара.

    @staticmethod
    def _strip_emoji(text: str) -> str:
        # Remove common emoji/pictographs while keeping regular text readable.
        emoji_pattern = re.compile(
            "["
            "\U0001F300-\U0001F5FF"
            "\U0001F600-\U0001F64F"
            "\U0001F680-\U0001F6FF"
            "\U0001F700-\U0001F77F"
            "\U0001F780-\U0001F7FF"
            "\U0001F800-\U0001F8FF"
            "\U0001F900-\U0001F9FF"
            "\U0001FA00-\U0001FA6F"
            "\U0001FA70-\U0001FAFF"
            "]+",
            flags=re.UNICODE,
        )
        return emoji_pattern.sub("", text or "")

    @staticmethod
    def _detect_level(raw_text: str) -> str:
        text_upper = (raw_text or "").upper()
        text_lower = (raw_text or "").lower()
        if "[DEBUG]" in text_upper:
            return "DEBUG"
        if "[ERROR]" in text_upper or "ошибка" in text_lower or "не удалось" in text_lower:
            return "ERROR"
        if "[WARNING]" in text_upper or "⚠" in raw_text or "warning" in text_lower:
            return "WARNING"
        return "INFO"

    def _format_log_message(self, raw_text: str) -> dict:
        timestamp = datetime.now().strftime("%H:%M:%S")
        level = self._detect_level(raw_text or "")

        text = (raw_text or "").strip()
        text = re.sub(r"\[(DEBUG|INFO|WARNING|ERROR)\]\s*", "", text, flags=re.IGNORECASE)
        text = self._strip_emoji(text)
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            text = "-"

        return {"timestamp": timestamp, "level": level, "text": text}

    @staticmethod
    def _color_for_level(level: str) -> str:
        palette = {
            "DEBUG": "#64748B",
            "INFO": "#334155",
            "WARNING": "#B45309",
            "ERROR": "#B91C1C",
        }
        return palette.get(level, "#FFFFFF")

    def _append_runtime_log_file(self, line: str):
        try:
            self._runtime_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._runtime_log_path.open("a", encoding="utf-8") as log_file:
                log_file.write(line + "\n")
        except Exception as error:
            logger.error(f"Не удалось записать runtime log: {error}")

    def load_devices(self):
        self.device_combo.blockSignals(True)
        self.device_combo.clear()

        devices = sd.query_devices()
        input_devices = []
        for i, d in enumerate(devices):
            if d.get("max_input_channels", 0) > 0:
                name = d.get("name", f"Device {i}")
                self.device_combo.addItem(f"{i}: {name}", i)
                input_devices.append(i)

        self.device_combo.blockSignals(False)

        if input_devices:
            # выберем дефолтный input, если он есть
            try:
                default_in = sd.default.device[0]
                idx = input_devices.index(default_in) if default_in in input_devices else 0
            except Exception:
                idx = 0

            self.device_combo.setCurrentIndex(idx)
            self.on_device_changed(idx)

    def on_device_changed(self, combo_index: int):
        if combo_index < 0:
            return
        device_index = self.device_combo.itemData(combo_index)

        if device_index == self.engine.device:
            return

        if getattr(self.engine, "is_loading", False) or getattr(self.engine, "is_running", False):
            return

        self.engine.set_device(device_index)
        self.save_audio_setting("microphone_name", self.device_combo.currentText())
        # Перезапускаем поток индикатора уровня под новое устройство
        try:
            self._stop_mic_level_stream()
        except Exception:
            pass

    def on_clear_log(self):
        self.log_view.clear()
        self.append_log("📋 Лог очищен")

    def on_open_config(self):
        config_path = Path(__file__).resolve().parents[1] / "src" / "jarvis" / "config.json"
        if config_path.exists():
            if sys.platform == "win32":
                os.startfile(config_path)
            elif sys.platform == "darwin":
                subprocess.run(["open", str(config_path)])
            else:
                subprocess.run(["xdg-open", str(config_path)])
            self.append_log(f"📄 Открыт config.json: {config_path}")
        else:
            self.append_log(f"❌ Файл не найден: {config_path}")

    def on_reload_config(self):
        try:
            self.engine.reload_config()
            self.load_audio_settings()
            self.append_log("🔄 Конфиг перезагружен")
        except Exception as e:
            self.append_log(f"❌ Ошибка при перезагрузке конфига: {e}")

    def on_scan_apps(self):
        if sys.platform != "win32":
            self.append_log("⚠ Автопоиск приложений поддерживается только в Windows.")
            return
        config_path = Path(__file__).resolve().parents[1] / "src" / "jarvis" / "config.json"
        if not config_path.exists():
            self.append_log(f"❌ Нет файла конфига: {config_path}")
            return
        try:
            result = merge_scanned_apps_into_config(config_path)
            self.engine.reload_config()
            updated = result.get("updated") or []
            syn_add = result.get("synonyms_added") or []
            discovered = result.get("discovered") or []
            kept = result.get("skipped_kept_user") or []

            if updated:
                self.append_log(f"🔎 Добавлено/исправлено в apps: {', '.join(updated)}")
            if syn_add:
                self.append_log(f"🔎 Добавлены синонимы ({len(syn_add)}): {', '.join(syn_add[:12])}{'…' if len(syn_add) > 12 else ''}")
            if not updated and not syn_add:
                if discovered:
                    hint = (
                        f"на диске найдено {len(discovered)} из списка сканера "
                        f"({', '.join(discovered[:8])}{'…' if len(discovered) > 8 else ''})"
                    )
                    if kept:
                        hint += f"; рабочие пути в config не меняли ({len(kept)}: {', '.join(kept[:6])}{'…' if len(kept) > 6 else ''})"
                    self.append_log(f"🔎 {hint.capitalize()}.")
                else:
                    self.append_log(
                        "🔎 В стандартных папках не найдено ни одной из ~40 программ из списка сканера. "
                        "Добавь .exe в apps вручную."
                    )
            self.append_log("🔄 Конфиг перезагружен")
        except Exception as e:
            self.append_log(f"❌ Ошибка автопоиска приложений: {e}")

    @staticmethod
    def _normalize_config_key(name: str) -> str:
        s = (name or "").strip().lower()
        s = re.sub(r"\s+", "_", s)
        s = re.sub(r"[^a-zа-яё0-9_]+", "", s)
        return s.strip("_")

    def _generate_synonyms_with_ai(self, kind: str, title: str, key: str, value: str) -> list[str]:
        """
        Генерация синонимов через AI (опционально).
        kind: app|site
        value: путь к exe или url
        """
        try:
            ex = getattr(self.engine, "ex", None)
            ai = getattr(ex, "_ai_client", None)
            if not ai or not ai.is_enabled():
                return []
            prompt = (
                "Верни строго JSON: {\"synonyms\":[\"...\", ...]} без дополнительного текста.\n"
                f"Тип: {kind}\n"
                f"Название: {title}\n"
                f"Ключ: {key}\n"
                f"Значение: {value}\n"
                "Нужно 6-12 полезных и реалистичных вариантов только на русском языке.\n"
                "Важно:\n"
                "- без английских слов, translit и технических терминов;\n"
                "- только то, что человек реально может сказать голосом;\n"
                "- включи: исходную форму, простые падежные формы, разговорные формы;\n"
                "- если название из 2+ слов, дай варианты и для каждого слова отдельно "
                "(особенно для первого слова), и для полной фразы;\n"
                "- добавь вероятные ошибки распознавания и похожие по звучанию формы, "
                "если они правдоподобны;\n"
                "- не добавляй фразы-команды (типа 'открой ...', 'запусти ...');\n"
                "- не добавляй статусные фразы (типа '... работает', '... открыта').\n"
                "Пример для Зона: зона, зону, зоны, зоне, сона, сону.\n"
                "Пример для Телеграм: телеграм, телеграма, телеграму, телеграме, телеграмм, телеграмма."
            )
            raw = ai.get_response(
                prompt,
                history=None,
                system_prompt=(
                    "Ты генератор русских синонимов для голосовых команд. "
                    "Только русский язык, без английских слов. "
                    "Нужны только полезные и реалистичные формы слова, без мусора. "
                    "Отвечай только JSON формата {\"synonyms\":[...]}."
                ),
                max_tokens=140,
                temperature=0.2,
            )
            if not raw:
                return []
            data = json.loads(raw)
            arr = data.get("synonyms") if isinstance(data, dict) else None
            if not isinstance(arr, list):
                return []
            out: list[str] = []
            title_parts = [p for p in re.split(r"[\s\-]+", (title or "").lower()) if p]
            banned_tokens = {
                "открой", "открыть", "запусти", "запустить", "включи", "включить",
                "зайди", "зайти", "найди", "найти", "покажи", "показать",
                "работает", "работать", "запущена", "запущен", "открыта", "открыт",
                "экране", "программа", "приложение", "сайт",
            }
            similarity_bases = []
            full_base = re.sub(r"[^а-яё0-9]", "", (title or "").lower())
            if full_base:
                similarity_bases.append(full_base)
            for tp in title_parts:
                clean_tp = re.sub(r"[^а-яё0-9]", "", tp)
                if clean_tp:
                    similarity_bases.append(clean_tp)
            fallback_base = re.sub(r"[^а-яё0-9]", "", (key or "").replace("_", " ").lower())
            if fallback_base:
                similarity_bases.append(fallback_base)
            for item in arr:
                s = str(item or "").strip().lower()
                s = re.sub(r"\s+", " ", s)
                # Только русские символы/цифры/пробел/дефис.
                # Отсекаем англ. варианты вроде "zone", "area" и т.п.
                if not re.fullmatch(r"[а-яё0-9][а-яё0-9\s\-]{0,39}", s):
                    continue
                if not s or len(s) > 40:
                    continue
                parts = [p for p in re.split(r"[\s\-]+", s) if p]
                # Оставляем только короткие "словарные" варианты (1-2 слова).
                # Фразы-команды и статусные фразы отбрасываем.
                if len(parts) == 0 or len(parts) > 2:
                    continue
                if any(p in banned_tokens for p in parts):
                    continue
                # Защита от «левых» слов: оставляем формы, похожие на название
                # целиком или на отдельные слова в названии.
                candidate = re.sub(r"[^а-яё0-9]", "", s)
                if similarity_bases and candidate:
                    ratio = max(
                        difflib.SequenceMatcher(None, base, candidate).ratio()
                        for base in similarity_bases
                    )
                    if ratio < 0.42:
                        continue
                out.append(s)

            # Локальная "человеческая" нормализация склеенных слов:
            # античит -> анти чит (частый вариант в речи/распознавании).
            expanded: list[str] = []
            for syn in out:
                expanded.append(syn)
                token_parts = [p for p in re.split(r"[\s\-]+", syn) if p]
                if len(token_parts) == 1:
                    token = token_parts[0]
                    if token.startswith("анти") and len(token) >= 7:
                        expanded.append(f"анти {token[4:]}")

            # уникальные, в исходном порядке
            uniq = list(dict.fromkeys(expanded))
            return uniq[:10]
        except Exception:
            return []

    def _config_path(self) -> Path:
        return Path(__file__).resolve().parents[1] / "src" / "jarvis" / "config.json"

    def _load_full_config(self) -> dict:
        path = self._config_path()
        if not path.exists():
            return {}
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}

    def _save_full_config(self, config: dict) -> None:
        path = self._config_path()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

    def _add_program_to_config(self, title: str, file_path: str) -> tuple[str, list[str]]:
        title = (title or "").strip()
        file_path = (file_path or "").strip()
        if not title:
            raise ValueError("Название программы пустое.")
        if not file_path:
            raise ValueError("Путь к программе пустой.")

        key = self._normalize_config_key(title)
        if not key:
            raise ValueError("Не удалось нормализовать имя программы.")

        cfg = self._load_full_config()
        apps = cfg.get("apps", {})
        synonyms = cfg.get("synonyms", {})
        if not isinstance(apps, dict):
            apps = {}
        if not isinstance(synonyms, dict):
            synonyms = {}

        apps[key] = file_path.replace("\\", "/")
        base_syns = [
            self._normalize_config_key(title).replace("_", " "),
            key,
        ]
        ai_syns = self._generate_synonyms_with_ai("app", title, key, apps[key])
        for syn in list(dict.fromkeys(base_syns + ai_syns)):
            if syn:
                synonyms[syn] = key

        cfg["apps"] = apps
        cfg["synonyms"] = synonyms
        self._save_full_config(cfg)
        self.engine.reload_config()
        return key, ai_syns

    def _add_program_from_scenario_path(self, file_path: str) -> str:
        title_default = Path(file_path).stem
        title, ok = QInputDialog.getText(
            self,
            "Добавить программу в сценарий",
            "Название программы:",
            text=title_default,
        )
        if not ok:
            return ""
        title = (title or "").strip()
        if not title:
            self.append_log("⚠ Название программы пустое.")
            return ""
        key, ai_syns = self._add_program_to_config(title, file_path)
        self.append_log(f"✅ Программа добавлена: {title} -> {key}")
        if ai_syns:
            self.append_log(f"🤖 Добавлены AI-синонимы: {', '.join(ai_syns[:8])}{'…' if len(ai_syns) > 8 else ''}")
        return key

    def _scenario_scripts_dir(self) -> Path:
        root = Path(__file__).resolve().parents[1]
        folder = root / "data" / "scenario_scripts"
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def _copy_scenario_script(self, src_path: str) -> str:
        src = Path(src_path)
        if not src.exists() or src.suffix.lower() not in {".bat", ".cmd"}:
            raise ValueError("Нужен существующий .bat/.cmd файл.")
        safe_name = self._normalize_config_key(src.stem) or "script"
        dst = self._scenario_scripts_dir() / f"{safe_name}_{int(time.time())}{src.suffix.lower()}"
        shutil.copy2(src, dst)
        return str(dst).replace("\\", "/")

    def _open_scenario_editor(
        self,
        app_names: list[str],
        initial_name: str = "",
        initial_actions: list[str] | None = None,
    ) -> tuple[str, list[str]] | None:
        dlg = ScenarioEditorDialog(
            app_names,
            self._copy_scenario_script,
            self._add_program_from_scenario_path,
            initial_name=initial_name,
            initial_actions=initial_actions or [],
            parent=self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        return dlg.scenario_name(), dlg.scenario_actions()

    def _remove_synonyms_by_targets(self, synonyms: dict, removed_targets: set[str]) -> dict:
        if not isinstance(synonyms, dict):
            return {}
        if not removed_targets:
            return dict(synonyms)
        return {k: v for k, v in synonyms.items() if str(v) not in removed_targets}

    def on_manage_programs(self):
        try:
            cfg = self._load_full_config()
            apps = cfg.get("apps", {})
            if not isinstance(apps, dict):
                apps = {}
            dlg = ProgramsManagerDialog(apps, self)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            new_apps = dlg.programs()
            old_apps = dict(apps)
            synonyms = cfg.get("synonyms", {})
            removed = set(old_apps.keys()) - set(new_apps.keys())
            synonyms = self._remove_synonyms_by_targets(synonyms, removed)
            for k in new_apps.keys():
                synonyms[k] = k
                synonyms[k.replace("_", " ")] = k
            cfg["apps"] = new_apps
            cfg["synonyms"] = synonyms
            self._save_full_config(cfg)
            self.engine.reload_config()
            self.append_log(f"✅ Список программ обновлён: {len(new_apps)} шт.")
        except Exception as e:
            self.append_log(f"❌ Не удалось обновить программы: {e}")

    def on_manage_sites(self):
        try:
            cfg = self._load_full_config()
            sites = cfg.get("sites", {})
            if not isinstance(sites, dict):
                sites = {}
            dlg = SitesManagerDialog(sites, self)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            new_sites = dlg.sites()
            old_sites = dict(sites)
            synonyms = cfg.get("synonyms", {})
            removed = set(old_sites.keys()) - set(new_sites.keys())
            synonyms = self._remove_synonyms_by_targets(synonyms, removed)
            for k in new_sites.keys():
                synonyms[k] = k
                synonyms[k.replace("_", " ")] = k
            cfg["sites"] = new_sites
            cfg["synonyms"] = synonyms
            self._save_full_config(cfg)
            self.engine.reload_config()
            self.append_log(f"✅ Список сайтов обновлён: {len(new_sites)} шт.")
        except Exception as e:
            self.append_log(f"❌ Не удалось обновить сайты: {e}")

    def _scenario_add_new(self) -> bool:
        cfg = self._load_full_config()
        apps = cfg.get("apps", {})
        if not isinstance(apps, dict):
            apps = {}
        app_names = sorted([str(k).strip() for k in apps.keys() if str(k).strip()])
        result = self._open_scenario_editor(app_names)
        if not result:
            return False
        name, actions = result
        key = self._normalize_config_key(name)
        if not key:
            self.append_log("⚠ Не удалось нормализовать имя сценария.")
            return False
        scenarios = cfg.get("scenarios", {})
        if not isinstance(scenarios, dict):
            scenarios = {}
        scenarios[key] = actions
        cfg["scenarios"] = scenarios
        self._save_full_config(cfg)
        self.engine.reload_config()
        self.append_log(f"✅ Сценарий добавлен: {name} ({len(actions)} шаг.)")
        return True

    def _scenario_edit(self, source_key: str) -> bool:
        cfg = self._load_full_config()
        scenarios = cfg.get("scenarios", {})
        if not isinstance(scenarios, dict) or source_key not in scenarios:
            return False
        apps = cfg.get("apps", {})
        if not isinstance(apps, dict):
            apps = {}
        app_names = sorted([str(k).strip() for k in apps.keys() if str(k).strip()])
        raw_actions = scenarios.get(source_key, [])
        initial_actions = [str(x).strip() for x in raw_actions if str(x).strip()] if isinstance(raw_actions, list) else []
        result = self._open_scenario_editor(app_names, initial_name=source_key, initial_actions=initial_actions)
        if not result:
            return False
        name, actions = result
        key = self._normalize_config_key(name)
        if not key:
            self.append_log("⚠ Не удалось нормализовать имя сценария.")
            return False
        if source_key != key:
            scenarios.pop(source_key, None)
        scenarios[key] = actions
        cfg["scenarios"] = scenarios
        self._save_full_config(cfg)
        self.engine.reload_config()
        self.append_log(f"✅ Сценарий обновлён: {name} ({len(actions)} шаг.)")
        return True

    def _scenario_remove(self, key: str) -> bool:
        cfg = self._load_full_config()
        scenarios = cfg.get("scenarios", {})
        if not isinstance(scenarios, dict) or key not in scenarios:
            return False
        scenarios.pop(key, None)
        cfg["scenarios"] = scenarios
        self._save_full_config(cfg)
        self.engine.reload_config()
        self.append_log(f"✅ Сценарий удалён: {key}")
        return True

    def on_manage_scenarios(self):
        try:
            cfg = self._load_full_config()
            scenarios = cfg.get("scenarios", {})
            if not isinstance(scenarios, dict):
                scenarios = {}
            dlg = ScenariosManagerDialog(
                scenarios,
                on_add=self._scenario_add_new,
                on_edit=self._scenario_edit,
                on_remove=self._scenario_remove,
                parent=self,
            )
            dlg.exec()
        except Exception as e:
            self.append_log(f"❌ Не удалось открыть менеджер сценариев: {e}")

    def on_add_program(self):
        title, ok = QInputDialog.getText(self, "Добавить программу", "Название программы:")
        if not ok:
            return
        title = (title or "").strip()
        if not title:
            self.append_log("⚠ Название программы пустое.")
            return

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Выбери .exe программы",
            str(Path.home()),
            "Программы (*.exe);;Все файлы (*.*)",
        )
        if not file_path:
            return

        try:
            key, ai_syns = self._add_program_to_config(title, file_path)
            self.append_log(f"✅ Программа добавлена: {title} -> {key}")
            if ai_syns:
                self.append_log(f"🤖 Добавлены AI-синонимы: {', '.join(ai_syns[:8])}{'…' if len(ai_syns) > 8 else ''}")
        except Exception as e:
            self.append_log(f"❌ Не удалось добавить программу: {e}")

    def on_add_site(self):
        title, ok = QInputDialog.getText(self, "Добавить сайт", "Название сайта:")
        if not ok:
            return
        title = (title or "").strip()
        if not title:
            self.append_log("⚠ Название сайта пустое.")
            return

        url, ok = QInputDialog.getText(self, "Добавить сайт", "Ссылка (https://...):")
        if not ok:
            return
        url = (url or "").strip()
        if not url:
            self.append_log("⚠ Ссылка сайта пустая.")
            return
        if not re.match(r"^https?://", url, flags=re.IGNORECASE):
            url = "https://" + url

        key = self._normalize_config_key(title)
        if not key:
            self.append_log("⚠ Не удалось нормализовать имя сайта.")
            return

        try:
            cfg = self._load_full_config()
            sites = cfg.get("sites", {})
            synonyms = cfg.get("synonyms", {})
            if not isinstance(sites, dict):
                sites = {}
            if not isinstance(synonyms, dict):
                synonyms = {}

            sites[key] = url
            base_syns = [
                self._normalize_config_key(title).replace("_", " "),
                key,
            ]
            ai_syns = self._generate_synonyms_with_ai("site", title, key, url)
            for syn in list(dict.fromkeys(base_syns + ai_syns)):
                if syn:
                    synonyms[syn] = key

            cfg["sites"] = sites
            cfg["synonyms"] = synonyms
            self._save_full_config(cfg)
            self.engine.reload_config()
            self.append_log(f"✅ Сайт добавлен: {title} -> {url}")
            if ai_syns:
                self.append_log(f"🤖 Добавлены AI-синонимы: {', '.join(ai_syns[:8])}{'…' if len(ai_syns) > 8 else ''}")
        except Exception as e:
            self.append_log(f"❌ Не удалось добавить сайт: {e}")

    def on_add_scenario(self):
        # Backward compatibility: старый хендлер теперь просто открывает менеджер.
        self.on_manage_scenarios()

    def on_phrase_timeout_changed(self, value):
        self.save_audio_setting("phrase_timeout", value)

    def on_silence_timeout_changed(self, value):
        self.save_audio_setting("silence_timeout", value)

    def on_tts_enabled_changed(self, state: int):
        enabled = state != 0
        self.save_audio_setting("tts_enabled", enabled)
        try:
            self.engine.reload_config()
            self.append_log(f"🔊 Озвучка: {'включена' if enabled else 'выключена'}")
        except Exception as error:
            self.append_log(f"❌ Не удалось применить настройки TTS: {error}")

    def on_tts_rate_changed(self, value: int):
        v = int(value)
        self.tts_rate_value.setText(str(v))
        self.save_audio_setting("tts_rate", v)
        try:
            self.engine.reload_config()
        except Exception as error:
            self.append_log(f"❌ Не удалось применить скорость TTS: {error}")

    def on_tts_volume_changed(self, value: int):
        pct = max(0, min(100, int(value)))
        self.tts_volume_value.setText(f"{pct}%")
        self.save_audio_setting("tts_volume", round(pct / 100.0, 3))
        try:
            self.engine.reload_config()
        except Exception as error:
            self.append_log(f"❌ Не удалось применить громкость TTS: {error}")

    def on_post_tts_delay_changed(self, value: float):
        v = float(value)
        self.save_audio_setting("post_tts_mic_delay", v)
        if hasattr(self.engine, "audio_config") and isinstance(self.engine.audio_config, dict):
            self.engine.audio_config["post_tts_mic_delay"] = v

    def on_test_tts(self):
        if not getattr(self.engine, "_tts_enabled", True):
            self.append_log("⚠ Озвучка выключена в настройках.")
            return
        self.append_log("🔊 Тест озвучки…")
        self.engine.test_tts_utterance()

    def save_config_section_value(self, section: str, key: str, value):
        config_path = Path(__file__).resolve().parents[1] / "src" / "jarvis" / "config.json"
        try:
            with open(config_path, "r", encoding="utf-8") as file:
                config = json.load(file)

            if section not in config or not isinstance(config[section], dict):
                config[section] = {}

            config[section][key] = value

            with open(config_path, "w", encoding="utf-8") as file:
                json.dump(config, file, ensure_ascii=False, indent=2)
        except Exception as error:
            logger.error(f"Не удалось сохранить {section}.{key}: {error}")

    def on_ai_enabled_toggled(self, state: int):
        enabled = state != 0
        self.save_config_section_value("ai", "enabled", enabled)
        try:
            self.engine.reload_config()
            self.append_log(f"🤖 AI {'включён' if enabled else 'отключён'}")
            self._show_openai_key_warning()
        except Exception as error:
            self.append_log(f"❌ Ошибка переключения AI: {error}")

    def on_ai_model_changed(self, _index: int):
        model = self.ai_model_combo.currentData()
        if not model:
            return
        self.save_config_section_value("ai", "model", model)
        try:
            self.engine.reload_config()
            self.append_log(f"🤖 Модель AI: {model}")
        except Exception as error:
            self.append_log(f"❌ Ошибка смены модели AI: {error}")

    def on_test_ai(self):
        try:
            ai_client = getattr(self.engine.ex, "_ai_client", None)
            if ai_client is None or not ai_client.is_enabled():
                self.append_log("⚠ AI недоступен: проверь ключ выбранного провайдера")
                return

            self.append_log("🤖 Проверяю AI...")
            response = ai_client.get_response("Кто ты такой? Ответь одним коротким предложением.")
            if response:
                self.append_log(f"🤖 AI: {response}")
            else:
                last_error = getattr(ai_client, "last_error", "неизвестная ошибка")
                self.append_log(f"⚠ AI недоступен: {last_error}")
        except Exception as error:
            self.append_log(f"❌ Ошибка теста AI: {error}")

    def on_clear_chat_history(self):
        """Очистить историю диалога для AI."""
        try:
            reset = self.engine.reset_chat_history("gui") if hasattr(self.engine, "reset_chat_history") else False
            if reset:
                self.append_log("🧹 Контекст диалога очищен")
            else:
                self.append_log("⚠ Не удалось очистить контекст")
        except Exception as error:
            self.append_log(f"❌ Ошибка очистки контекста: {error}")

    def on_save_openai_key(self):
        key = (self.openai_key_input.text() or "").strip()
        if not key:
            self.append_log("⚠ OpenAI ключ пустой — сохранение пропущено")
            return
        try:
            save_keys({"openai_api_key": key})
            self.save_config_section_value("ai", "provider", "openai")
            self.append_log("💾 Ключ OpenAI сохранён в keys.json")
            self.engine.reload_config()
            self._show_openai_key_warning()
        except Exception as error:
            self.append_log(f"❌ Не удалось сохранить OpenAI ключ: {error}")

    def on_save_pv_key(self):
        key = (self.pv_key_input.text() or "").strip()
        if not key:
            self.append_log("⚠ Porcupine ключ пустой — сохранение пропущено")
            return
        try:
            save_keys({"picovoice_access_key": key})
            self.append_log("💾 Porcupine ключ сохранён в keys.json")
            # Обновляем движок, чтобы подхватить ключ
            self.engine.set_wakeword_engine(self.engine.wakeword_engine)
            self._show_pv_key_warning()
        except Exception as error:
            self.append_log(f"❌ Не удалось сохранить Porcupine ключ: {error}")
    
    def on_autostart_toggled(self, state: int):
        enabled = state != 0
        try:
            self._set_autostart_enabled(enabled)
            status_text = "включён" if enabled else "выключен"
            self.append_log(f"⚙ Автозапуск {status_text}")
        except Exception as error:
            self.autostart_checkbox.blockSignals(True)
            self.autostart_checkbox.setChecked(not enabled)
            self.autostart_checkbox.blockSignals(False)
            self.append_log(f"❌ Не удалось изменить автозапуск: {error}")
    
    def on_ptt_toggled(self, state: int):
        enabled = state != 0
        try:
            if enabled:
                success = self.engine.enable_push_to_talk(self._ptt_hotkey)
                if success:
                    key_display = self.ptt_key_input.text()
                    self.append_log(f"🎯 Push-to-talk активирован ({key_display})")
                else:
                    self.ptt_checkbox.blockSignals(True)
                    self.ptt_checkbox.setChecked(False)
                    self.ptt_checkbox.blockSignals(False)
                    self.append_log("❌ Не удалось активировать push-to-talk (нужен pynput)")
            else:
                self.engine.disable_push_to_talk()
                self.append_log("⏸ Push-to-talk отключён")
        except Exception as error:
            self.ptt_checkbox.blockSignals(True)
            self.ptt_checkbox.setChecked(not enabled)
            self.ptt_checkbox.blockSignals(False)
            self.append_log(f"❌ Ошибка push-to-talk: {error}")
    
    def on_record_ptt_key(self):
        """Запись новой горячей клавиши/комбинации для PTT"""
        if self.ptt_checkbox.isChecked():
            self.append_log("⚠ Сначала отключи Push-to-talk")
            return
        
        self._recording_key = True
        self._recorded_combo = []
        self.ptt_key_input.setText("Нажми клавишу/мышь...")
        self.ptt_key_input.setStyleSheet("background-color: #ffeeaa;")
        self.append_log("⌨ Нажми клавишу, кнопку мыши или комбинацию (удерживай модификаторы)...")
    
    def keyPressEvent(self, event):
        """Перехват нажатия клавиши для записи PTT hotkey"""
        if self._recording_key:
            from PySide6.QtCore import Qt
            key = event.key()
            modifiers = event.modifiers()
            
            # Маппинг Qt клавиш на pynput формат
            key_map = {
                Qt.Key_F1: "f1", Qt.Key_F2: "f2", Qt.Key_F3: "f3", Qt.Key_F4: "f4",
                Qt.Key_F5: "f5", Qt.Key_F6: "f6", Qt.Key_F7: "f7", Qt.Key_F8: "f8",
                Qt.Key_F9: "f9", Qt.Key_F10: "f10", Qt.Key_F11: "f11", Qt.Key_F12: "f12",
                Qt.Key_Space: "space", Qt.Key_Control: "ctrl", Qt.Key_Alt: "alt",
                Qt.Key_Shift: "shift", Qt.Key_CapsLock: "caps_lock",
                Qt.Key_Tab: "tab", Qt.Key_Return: "enter", Qt.Key_Escape: "esc",
                Qt.Key_Backspace: "backspace", Qt.Key_Delete: "delete",
                Qt.Key_Insert: "insert", Qt.Key_Home: "home", Qt.Key_End: "end",
                Qt.Key_PageUp: "page_up", Qt.Key_PageDown: "page_down"
            }
            
            # Собираем комбинацию
            combo = []
            
            # Добавляем модификаторы
            if modifiers & Qt.ControlModifier:
                combo.append("ctrl")
            if modifiers & Qt.AltModifier:
                combo.append("alt")
            if modifiers & Qt.ShiftModifier and key not in [Qt.Key_Control, Qt.Key_Alt, Qt.Key_Shift]:
                combo.append("shift")
            
            # Добавляем основную клавишу (если не модификатор)
            if key not in [Qt.Key_Control, Qt.Key_Alt, Qt.Key_Shift]:
                key_name = key_map.get(key)
                if key_name and key_name not in combo:
                    combo.append(key_name)
            
            if combo:
                # Сохраняем как строку с + разделителем
                combo_str = "+".join(combo)
                self._ptt_hotkey = combo_str
                display_name = " + ".join([k.upper() if len(k) <= 3 else k.title() for k in combo])
                self.ptt_key_input.setText(display_name)
                self.ptt_key_input.setStyleSheet("")
                self._recording_key = False
                self.save_audio_setting("ptt_hotkey", combo_str)
                self.append_log(f"✅ PTT установлен: {display_name}")
            else:
                # Только модификатор нажат, ждём основную клавишу
                display_parts = []
                if modifiers & Qt.ControlModifier:
                    display_parts.append("Ctrl")
                if modifiers & Qt.AltModifier:
                    display_parts.append("Alt")
                if modifiers & Qt.ShiftModifier:
                    display_parts.append("Shift")
                if display_parts:
                    self.ptt_key_input.setText(" + ".join(display_parts) + " + ...")
            return
        
        super().keyPressEvent(event)
    
    def mousePressEvent(self, event):
        """Перехват нажатия мыши для записи PTT hotkey"""
        if self._recording_key:
            from PySide6.QtCore import Qt
            button_map = {
                Qt.LeftButton: "mouse_left",
                Qt.RightButton: "mouse_right",
                Qt.MiddleButton: "mouse_middle",
                Qt.BackButton: "mouse_x1",
                Qt.ForwardButton: "mouse_x2"
            }
            
            button_name = button_map.get(event.button())
            if button_name:
                self._ptt_hotkey = button_name
                display_map = {
                    "mouse_left": "ЛКМ",
                    "mouse_right": "ПКМ",
                    "mouse_middle": "СКМ",
                    "mouse_x1": "X1",
                    "mouse_x2": "X2"
                }
                display_name = display_map.get(button_name, button_name)
                self.ptt_key_input.setText(display_name)
                self.ptt_key_input.setStyleSheet("")
                self._recording_key = False
                self.save_audio_setting("ptt_hotkey", button_name)
                self.append_log(f"✅ PTT установлен: {display_name}")
                return
        
        super().mousePressEvent(event)
    
    def on_test_microphone(self):
        """Мониторинг микрофона (как в Discord): слышишь сам себя."""
        # Toggle
        if getattr(self, "_mic_monitor_active", False):
            self._stop_mic_monitoring()
            return

        if self.device_combo.currentIndex() < 0:
            self.append_log("❌ Сначала выбери микрофон")
            return
        # Во время активного прослушивания мониторинг не включаем (иначе будет каша/эхо).
        # Во время загрузки модели (is_loading) мониторинг разрешаем: это полезно как в Discord.
        if getattr(self.engine, "is_running", False):
            self.append_log("⚠ Останови движок перед тестом микрофона.")
            return
        if getattr(self.engine, "is_loading", False):
            self.append_log("ℹ Идёт загрузка модели. Тест микрофона может не запуститься, если устройство занято.")

        device_id = self.device_combo.itemData(self.device_combo.currentIndex())

        try:
            in_info = sd.query_devices(device_id, "input")
            sr = int(in_info.get("default_samplerate", 48000))

            def rms_to_level01(rms: float) -> float:
                # Нормализация, чтобы "обычная речь" была заметной (как в Discord).
                # Лог-кривая: small rms -> заметно, большие -> насыщение.
                r = max(0.0, min(1.0, float(rms)))
                k = 60.0
                return max(0.0, min(1.0, math.log1p(r * k) / math.log1p(k)))

            def callback(indata, outdata, frames, time_info, status):  # noqa: ANN001
                if status:
                    # не спамим в лог, просто молча продолжаем
                    pass
                # Voice gate по порогу чувствительности (как в Discord).
                try:
                    rms = float((indata * indata).mean() ** 0.5)
                except Exception:
                    rms = 0.0
                lvl01 = rms_to_level01(rms)
                thr = self.mic_sensitivity.threshold() if hasattr(self, "mic_sensitivity") else 0.2
                target = 1.0 if lvl01 >= thr else 0.0

                # Сглаживаем включение/выключение, чтобы не было щелчков
                gain = getattr(callback, "_gain", 0.0)
                a = 0.25 if target > gain else 0.12
                gain = (1.0 - a) * gain + a * target
                setattr(callback, "_gain", gain)

                outdata[:] = indata * gain

            stream = sd.Stream(
                device=(device_id, None),
                samplerate=sr,
                channels=1,
                dtype="float32",
                callback=callback,
            )
            stream.start()
            self._mic_monitor_stream = stream
            self._mic_monitor_active = True
            self.btn_test_mic.setText("Стоп")
            msg = "Тест микрофона: мониторинг включен. Ты слышишь сам себя. Нажми «Стоп»."
            self.mic_test_result.setStyleSheet("color: #0F172A; font-size: 11px;")
            self.mic_test_result.setText(msg)
            self.append_log(msg)

        except Exception as e:
            error_msg = f"❌ Ошибка теста микрофона: {e}"
            self.append_log(error_msg)
            self.mic_test_result.setText(error_msg)
            self.mic_test_result.setStyleSheet("color: #ff0000; font-size: 10px;")

    def on_mic_threshold_changed_01(self, value01: float):
        v01 = max(0.0, min(1.0, float(value01)))
        self.save_audio_setting("mic_threshold", int(round(v01 * 100)))

    def on_mic_auto_toggled(self, state: int):
        enabled = state != 0
        self.save_audio_setting("mic_auto_sensitivity", bool(enabled))
        self._mic_auto_enabled = bool(enabled)
        if hasattr(self, "mic_sensitivity"):
            self.mic_sensitivity.set_auto(self._mic_auto_enabled)

    def _ensure_mic_level_stream(self):
        if getattr(self, "_mic_level_active", False):
            return
        if not hasattr(self, "device_combo") or self.device_combo.currentIndex() < 0:
            return
        device_id = self.device_combo.itemData(self.device_combo.currentIndex())
        try:
            info = sd.query_devices(device_id, "input")
            sr = int(info.get("default_samplerate", 48000))

            def cb(indata, frames, time_info, status):  # noqa: ANN001
                try:
                    # RMS level for current chunk (0..1)
                    # indata is float32 [-1..1]
                    rms = float((indata * indata).mean() ** 0.5)
                    self._mic_level_rms = max(0.0, min(1.0, rms))
                    # Нормализованный уровень для UI/порога
                    k = 60.0
                    self._mic_level_norm = max(
                        0.0, min(1.0, math.log1p(self._mic_level_rms * k) / math.log1p(k))
                    )
                except Exception:
                    pass

            stream = sd.InputStream(
                device=device_id,
                samplerate=sr,
                channels=1,
                dtype="float32",
                callback=cb,
            )
            stream.start()
            self._mic_level_stream = stream
            self._mic_level_active = True
        except Exception:
            # если устройство занято — просто не показываем живой уровень
            self._mic_level_stream = None
            self._mic_level_active = False

    def _stop_mic_level_stream(self):
        stream = getattr(self, "_mic_level_stream", None)
        try:
            if stream is not None:
                stream.stop()
                stream.close()
        finally:
            self._mic_level_stream = None
            self._mic_level_active = False
            self._mic_level_rms = 0.0
            self._mic_level_norm = 0.0

    def _update_mic_level_ui(self):
        # Поднимаем поток уровня только когда открыт экран настроек и выбран микрофон.
        try:
            if hasattr(self, "_stack") and self._stack.currentWidget() is self.settings_tab:
                self._ensure_mic_level_stream()
            else:
                if getattr(self, "_mic_level_active", False):
                    self._stop_mic_level_stream()
        except Exception:
            pass

        if not hasattr(self, "mic_sensitivity"):
            return

        lvl01 = max(0.0, min(1.0, float(self._mic_level_norm)))
        self.mic_sensitivity.set_level(lvl01)

        # Авто-порог (по шуму) — простой, понятный алгоритм для диплома.
        if getattr(self, "_mic_auto_enabled", False):
            now = time.time()
            if not hasattr(self, "_mic_noise_est"):
                self._mic_noise_est = max(0.02, min(0.4, lvl01))
            # нижняя огибающая: быстро вниз, медленно вверх
            if lvl01 < self._mic_noise_est:
                self._mic_noise_est = lvl01
            else:
                self._mic_noise_est = self._mic_noise_est * 0.995 + lvl01 * 0.005

            suggested = self._mic_noise_est * 1.8 + 0.05
            suggested = max(0.06, min(0.85, suggested))
            self.mic_sensitivity.set_threshold(suggested, emit_signal=True)

            # чтобы не спамить записью в файл, сохраняем раз в ~1с
            last = getattr(self, "_mic_auto_last_save", 0.0)
            if now - last > 1.0:
                self._mic_auto_last_save = now
                self.save_audio_setting("mic_threshold", int(round(suggested * 100)))

    def _stop_mic_monitoring(self):
        stream = getattr(self, "_mic_monitor_stream", None)
        try:
            if stream is not None:
                stream.stop()
                stream.close()
        finally:
            self._mic_monitor_stream = None
            self._mic_monitor_active = False
            if hasattr(self, "btn_test_mic"):
                self.btn_test_mic.setText("Тест")
            msg = "Тест микрофона: мониторинг выключен."
            if hasattr(self, "mic_test_result"):
                self.mic_test_result.setStyleSheet("color: #475569; font-size: 11px;")
                self.mic_test_result.setText(msg)
            self.append_log(msg)


    def save_audio_setting(self, key: str, value):
        config_path = Path(__file__).resolve().parents[1] / "src" / "jarvis" / "config.json"
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            
            if "audio" not in config:
                config["audio"] = {}
            
            config["audio"][key] = value
            
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Не удалось сохранить настройку {key}: {e}")

    def load_audio_settings(self):
        config_path = Path(__file__).resolve().parents[1] / "src" / "jarvis" / "config.json"
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            
            audio = config.get("audio", {})
            
            # Загрузка микрофона
            mic_name = audio.get("microphone_name")
            if mic_name:
                for i in range(self.device_combo.count()):
                    if mic_name in self.device_combo.itemText(i):
                        self.device_combo.setCurrentIndex(i)
                        break
            
            # Загрузка таймаутов
            phrase_timeout = audio.get("phrase_timeout", 6.0)
            silence_timeout = audio.get("silence_timeout", 1.2)
            self.timeout_spinbox.setValue(phrase_timeout)
            self.silence_spinbox.setValue(silence_timeout)

            tts_en = bool(audio.get("tts_enabled", True))
            self.tts_enabled_checkbox.blockSignals(True)
            self.tts_enabled_checkbox.setChecked(tts_en)
            self.tts_enabled_checkbox.blockSignals(False)

            tts_rate = int(audio.get("tts_rate", 182))
            tts_vol = float(audio.get("tts_volume", 0.95))
            self.tts_rate_slider.blockSignals(True)
            self.tts_rate_slider.setValue(max(120, min(240, tts_rate)))
            self.tts_rate_slider.blockSignals(False)
            self.tts_rate_value.setText(str(max(120, min(240, tts_rate))))

            pct = int(round(max(0.0, min(1.0, tts_vol)) * 100))
            self.tts_volume_slider.blockSignals(True)
            self.tts_volume_slider.setValue(pct)
            self.tts_volume_slider.blockSignals(False)
            self.tts_volume_value.setText(f"{pct}%")

            mic_threshold_pct = int(audio.get("mic_threshold", 20))
            mic_threshold_pct = max(0, min(100, mic_threshold_pct))
            mic_auto = bool(audio.get("mic_auto_sensitivity", False))
            self._mic_auto_enabled = mic_auto
            self.mic_auto_checkbox.blockSignals(True)
            self.mic_auto_checkbox.setChecked(mic_auto)
            self.mic_auto_checkbox.blockSignals(False)
            self.mic_sensitivity.set_auto(mic_auto)
            self.mic_sensitivity.set_threshold(mic_threshold_pct / 100.0, emit_signal=False)

            self.post_tts_spin.blockSignals(True)
            self.post_tts_spin.setValue(float(audio.get("post_tts_mic_delay", 0.45)))
            self.post_tts_spin.blockSignals(False)
            
            # Загрузка движка wake-word
            wake_engine = audio.get("wake_engine", "vosk_text")
            wake_idx = 0 if wake_engine == "vosk_text" else 1
            self.wake_engine_combo.blockSignals(True)
            self.wake_engine_combo.setCurrentIndex(wake_idx)
            self.wake_engine_combo.blockSignals(False)
            self.engine.set_wakeword_engine(wake_engine)
            
            # Загрузка PTT клавиши
            ptt_hotkey = audio.get("ptt_hotkey", "f6")
            self._ptt_hotkey = ptt_hotkey
            display_name = ptt_hotkey.upper() if len(ptt_hotkey) <= 3 else ptt_hotkey.title()
            self.ptt_key_input.setText(display_name)

            ai_config = config.get("ai", {})
            self.ai_enabled_checkbox.blockSignals(True)
            self.ai_enabled_checkbox.setChecked(ai_config.get("enabled", True))
            self.ai_enabled_checkbox.blockSignals(False)

            ai_model = ai_config.get("model", "gpt-4o-mini")
            ai_model_index = self.ai_model_combo.findData(ai_model)
            self.ai_model_combo.blockSignals(True)
            self.ai_model_combo.setCurrentIndex(ai_model_index if ai_model_index >= 0 else 0)
            self.ai_model_combo.blockSignals(False)

            self._load_openai_key()
            self._load_pv_key()
            
        except Exception as e:
            logger.error(f"Не удалось загрузить настройки аудио: {e}")

    def _load_openai_key(self):
        try:
            keys, _created = ensure_keys_file()
            key = keys.get("openai_api_key", "")
            self.openai_key_input.setText(key)
            self._show_openai_key_warning()
        except Exception as error:
            self.append_log(f"❌ Не удалось загрузить OpenAI ключ: {error}")

    def _load_pv_key(self):
        try:
            keys, created = ensure_keys_file()
            key = keys.get("picovoice_access_key", "")
            self.pv_key_input.setText(key)
            if created and not key:
                self.append_log("⚠ keys.json создан. Добавь Porcupine ключ, если используешь Picovoice.")
            self._show_pv_key_warning()
        except Exception as error:
            self.append_log(f"❌ Не удалось загрузить Porcupine ключ: {error}")

    def _show_openai_key_warning(self):
        ai_enabled = self.ai_enabled_checkbox.isChecked()
        key_empty = not (self.openai_key_input.text() or "").strip()
        if ai_enabled and key_empty:
            self.openai_key_warning.setText("⚠ OpenAI выбран, но ключ пуст. Добавь OPENAI ключ.")
            if not self._openai_key_warn_shown:
                self.append_log("⚠ OpenAI выбран, но ключ пуст. Добавь ключ OpenAI.")
                self._openai_key_warn_shown = True
        else:
            self.openai_key_warning.setText("")

    def _show_pv_key_warning(self):
        use_porcupine = getattr(self.engine, "wakeword_engine", "vosk_text") == "porcupine"
        key_empty = not (self.pv_key_input.text() or "").strip()
        if use_porcupine and key_empty:
            self.pv_key_warning.setText("⚠ Porcupine выбран, но ключ пуст. Добавь ключ или переключи движок.")
            if not self._pv_key_warn_shown:
                self.append_log("⚠ Porcupine без ключа не работает. Добавь ключ или выбери Vosk.")
                self._pv_key_warn_shown = True
        else:
            self.pv_key_warning.setText("")

    def on_wake_engine_changed(self, combo_index: int):
        if combo_index < 0:
            return

        selected_engine = self.wake_engine_combo.itemData(combo_index)

        if getattr(self.engine, "is_loading", False) or getattr(self.engine, "is_running", False):
            self.append_log("⚠ Сначала останови движок, затем меняй движок активации.")
            self.wake_engine_combo.blockSignals(True)
            current_idx = self.wake_engine_combo.findData(getattr(self.engine, "wakeword_engine", "vosk_text"))
            self.wake_engine_combo.setCurrentIndex(current_idx if current_idx >= 0 else 0)
            self.wake_engine_combo.blockSignals(False)
            return

        changed = self.engine.set_wakeword_engine(selected_engine)
        if not changed:
            self.wake_engine_combo.blockSignals(True)
            current_idx = self.wake_engine_combo.findData(getattr(self.engine, "wakeword_engine", "vosk_text"))
            self.wake_engine_combo.setCurrentIndex(current_idx if current_idx >= 0 else 0)
            self.wake_engine_combo.blockSignals(False)
        else:
            self.save_audio_setting("wake_engine", selected_engine)
        self._show_pv_key_warning()

def main():
    # Подавить Qt DPI-related ошибки на Windows
    if sys.platform == "win32":
        import os
        os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = ""
    
    parser = argparse.ArgumentParser(description="Jarvis Assistant GUI")
    parser.add_argument(
        "--minimized",
        action="store_true",
        help="Запускать приложение свернутым в трей",
    )
    args, _unknown = parser.parse_known_args()

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    bus = LogBus()

    # движок + лог callback в Qt сигнал
    engine = JarvisEngine(asr=None, log=lambda m: bus.log.emit(m))
    win = MainWindow(engine, bus)

    threading.Thread(target=engine.preload, daemon=True).start()

    # трей
    tray = QSystemTrayIcon()
    fallback_icon = app.style().standardIcon(QStyle.SP_ComputerIcon)
    tray.setIcon(fallback_icon)
    win.setWindowIcon(fallback_icon)
    tray.setToolTip("Jarvis Assistant")

    menu = QMenu()
    act_show = QAction("Открыть")
    act_start = QAction("Старт")
    act_stop = QAction("Стоп")
    act_quit = QAction("Выход")

    act_show.triggered.connect(win.show_and_raise)
    act_start.triggered.connect(engine.start)
    act_stop.triggered.connect(engine.stop)

    def _quit_app():
        win._quit_requested = True
        engine.stop()
        app.quit()

    act_quit.triggered.connect(_quit_app)

    menu.addAction(act_show)
    menu.addSeparator()
    menu.addAction(act_start)
    menu.addAction(act_stop)
    menu.addSeparator()
    menu.addAction(act_quit)

    tray.setContextMenu(menu)
    tray.activated.connect(lambda reason: win.show_and_raise() if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
    tray.show()

    win.set_tray(tray)

    if args.minimized:
        win.hide()
        tray.showMessage(
            "Jarvis Assistant",
            "Запущен в фоне. Модель загружается в трее.",
            QSystemTrayIcon.MessageIcon.Information,
            2500,
        )
    else:
        win.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
