import sys
import logging
import threading
import re
import argparse
import json
import os
import subprocess
import html
from pathlib import Path
from datetime import datetime
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QTextEdit, QPushButton,
    QVBoxLayout, QWidget, QSystemTrayIcon, QMenu, QLabel,
    QComboBox, QHBoxLayout, QProgressBar, QTabWidget, QDoubleSpinBox, QStyle, QCheckBox, QLineEdit,
    QFrame, QGraphicsDropShadowEffect, QGroupBox, QFormLayout, QToolButton, QSizePolicy, QToolTip,
    QScrollArea,
)

import sounddevice as sd

from PySide6.QtGui import QAction, QIcon, QPixmap, QPainter, QColor, QPen, QBrush, QCursor
from PySide6.QtCore import Signal, QObject, QTimer, QEvent, Qt, QPropertyAnimation, QEasingCurve

from src.jarvis.engine import JarvisEngine, TTS_PRESETS
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


class MainWindow(QMainWindow):
    def __init__(self, engine: JarvisEngine, bus: LogBus):
        super().__init__()
        self.engine = engine
        self.bus = bus
        self.tray: QSystemTrayIcon | None = None
        self._quit_requested = False
        self._loading_progress_value = 0
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

        self.setWindowTitle("Jarvis Assistant")
        self.resize(900, 700)
        # Фиксированный размер окна: основная вкладка "выжимается" в этот размер,
        # а в "Настройках" используется скролл.
        self.setFixedSize(self.size())
        self._apply_light_theme()

        # Вкладки для навигации
        self.tabs = QTabWidget()
        
        # Вкладка 1: Основное окно
        self.main_tab = QWidget()
        self.setup_main_tab()
        
        # Вкладка 2: Настройки
        self.settings_tab = QWidget()
        self.setup_settings_tab()

        self.tabs.addTab(self.main_tab, "Основное")
        self.tabs.addTab(self.settings_tab, "Настройки")

        self.tabs.currentChanged.connect(self.on_tab_changed)
        
        self.setCentralWidget(self.tabs)

        self.bus.log.connect(self.append_log)
        self.engine.set_asr_ready_callback(self._schedule_asr_ready_tray_notification)
        self.load_devices()
        self.load_audio_settings()
        
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_buttons)
        self.timer.start(100)

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
        
        # Статус
        self.status_label = QLabel("Статус: INIT")
        status_font = self.status_label.font()
        status_font.setPointSize(12)
        status_font.setBold(True)
        self.status_label.setFont(status_font)
        layout.addWidget(self.status_label)
        
        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)
        
        # Информация о микрофоне и режиме
        info_layout = QHBoxLayout()
        self.microphone_label = QLabel("🎤 Микрофон: не выбран")
        self.microphone_label.setStyleSheet("color: #666;")
        info_layout.addWidget(self.microphone_label)
        
        self.mode_label = QLabel("⏸ Режим: не активен")
        self.mode_label.setStyleSheet("color: #666;")
        info_layout.addWidget(self.mode_label)
        info_layout.addStretch()
        layout.addLayout(info_layout)

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
        
        button_layout.addWidget(self.btn_start)
        button_layout.addWidget(self.btn_stop)
        layout.addLayout(button_layout)
        
        self.main_tab.setLayout(layout)
        self._update_avatar_for_state()

    def _apply_avatar_size(self, size: int, inner_size: int):
        self.avatar_label.setFixedSize(size, size)
        self.avatar_label.setPixmap(self._make_avatar_pixmap(inner_size))
        radius = size // 2
        self.avatar_label.setStyleSheet(
            f"border: 3px solid #DDE6F2; border-radius: {radius}px; background: #FFFFFF;"
        )

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

    @staticmethod
    def _make_avatar_pixmap(size: int) -> QPixmap:
        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Внешнее кольцо
        painter.setPen(QPen(QColor("#D8E6FF"), 2))
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
        painter.setBrush(QBrush(QColor("#7EA8FF")))
        painter.drawEllipse(core_x, core_y, core, core)
        painter.end()
        return pix

    def _start_avatar_animation(self, duration_ms: int = 1800):
        if self.avatar_pulse_anim.state() != QPropertyAnimation.State.Running:
            self.avatar_pulse_anim.start()
        self.avatar_shadow.setColor(QColor(76, 132, 255, 150))
        self._avatar_pulse_timer.start(max(600, duration_ms))

    def _stop_avatar_animation(self):
        if self.avatar_pulse_anim.state() == QPropertyAnimation.State.Running:
            self.avatar_pulse_anim.stop()
        self.avatar_shadow.setBlurRadius(20)
        self.avatar_shadow.setColor(QColor(104, 150, 255, 80))

    def _apply_light_theme(self):
        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background: #F6F8FC;
                color: #1F2A37;
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
                padding: 7px 12px;
                color: #3730A3;
                font-weight: 600;
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
            QToolButton#helpBtn {
                border: 1px solid #DCE3ED;
                border-radius: 9px;
                width: 18px;
                height: 18px;
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
    
    def setup_settings_tab(self):
        """Настройка вкладки настроек"""
        # Делаем "Настройки" прокручиваемыми: много параметров не влезает в фиксированное окно.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(12)
        
        mic_group = QGroupBox("Микрофон")
        mic_form = QFormLayout(mic_group)
        mic_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        mic_row = QHBoxLayout()
        self.device_combo = NoWheelComboBox()
        self.device_combo.currentIndexChanged.connect(self.on_device_changed)
        mic_row.addWidget(self.device_combo, 1)

        self.btn_refresh_devices = QPushButton("Обновить")
        self.btn_refresh_devices.setToolTip("Если переподключил микрофон — нажми, чтобы обновить список устройств.")
        self.btn_refresh_devices.clicked.connect(self.load_devices)
        mic_row.addWidget(self.btn_refresh_devices)

        self.btn_test_mic = QPushButton("Тест")
        self.btn_test_mic.setToolTip("Короткий тест микрофона (3 секунды).")
        self.btn_test_mic.clicked.connect(self.on_test_microphone)
        mic_row.addWidget(self.btn_test_mic)

        mic_form.addRow("Устройство:", mic_row)

        self.mic_test_result = QLabel("")
        self.mic_test_result.setStyleSheet("color: #475569; font-size: 11px;")
        self.mic_test_result.setWordWrap(True)
        mic_form.addRow("", self.mic_test_result)

        layout.addWidget(mic_group)
        
        asr_group = QGroupBox("Распознавание и активация")
        asr_form = QFormLayout(asr_group)

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

        tts_group = QGroupBox("Озвучка (TTS)")
        tts_form = QFormLayout(tts_group)

        self.tts_enabled_checkbox = QCheckBox("Включить озвучку (pyttsx3)")
        self.tts_enabled_checkbox.stateChanged.connect(self.on_tts_enabled_changed)
        tts_form.addRow(self.tts_enabled_checkbox)

        self.tts_preset_combo = NoWheelComboBox()
        self.tts_preset_combo.addItem("Тихий", "quiet")
        self.tts_preset_combo.addItem("Нормальный", "normal")
        self.tts_preset_combo.addItem("Чёткий", "clear")
        self.tts_preset_combo.currentIndexChanged.connect(self.on_tts_preset_changed)
        tts_form.addRow("Пресет:", self.tts_preset_combo)

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
        
        misc_group = QGroupBox("Дополнительно")
        misc_layout = QVBoxLayout(misc_group)

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

        ai_group = QGroupBox("AI (ответы на неизвестные команды)")
        ai_layout = QVBoxLayout(ai_group)

        ai_enable_layout = QHBoxLayout()
        self.ai_enabled_checkbox = QCheckBox("Включить AI для неизвестных команд")
        self.ai_enabled_checkbox.setToolTip("Если команда не распознана локально, вопрос отправляется в выбранный AI-провайдер")
        self.ai_enabled_checkbox.stateChanged.connect(self.on_ai_enabled_toggled)
        ai_enable_layout.addWidget(self.ai_enabled_checkbox)
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

        ai_info_label = QLabel(
            "• Локальные системные команды выполняются без AI\n"
            "• AI используется только для неизвестных команд и общих вопросов\n"
            "• Ключи хранятся локально в keys.json (или в переменных окружения)"
        )
        ai_info_label.setStyleSheet("color: #666; font-size: 10px;")
        ai_layout.addWidget(ai_info_label)

        chat_ctx_layout = QHBoxLayout()
        self.clear_ctx_btn = QPushButton("🧹 Очистить контекст")
        self.clear_ctx_btn.setToolTip("Очистить историю диалога для AI")
        self.clear_ctx_btn.clicked.connect(self.on_clear_chat_history)
        chat_ctx_layout.addWidget(self.clear_ctx_btn)
        chat_ctx_layout.addStretch()
        ai_layout.addLayout(chat_ctx_layout)

        layout.addWidget(ai_group)

        keys_group = QGroupBox("Ключи и конфигурация")
        keys_layout = QVBoxLayout(keys_group)

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
        
        # Кнопки управления конфигом
        config_buttons_layout = QHBoxLayout()
        
        open_config_btn = QPushButton("📄 Открыть config.json")
        open_config_btn.clicked.connect(self.on_open_config)
        config_buttons_layout.addWidget(open_config_btn)
        
        reload_config_btn = QPushButton("🔄 Перезагрузить конфиг")
        reload_config_btn.clicked.connect(self.on_reload_config)
        config_buttons_layout.addWidget(reload_config_btn)

        scan_apps_btn = QPushButton("🔎 Найти приложения")
        scan_apps_btn.setToolTip(
            "Ищет ~40 типовых программ (браузеры, IDE, мессенджеры, игры, Office…) и дописывает apps в config.json"
        )
        scan_apps_btn.clicked.connect(self.on_scan_apps)
        config_buttons_layout.addWidget(scan_apps_btn)

        layout.addLayout(config_buttons_layout)
        keys_layout.addLayout(config_buttons_layout)

        layout.addWidget(keys_group)
        
        layout.addStretch()
        scroll.setWidget(content)

        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        self.settings_tab.setLayout(outer)

    def refresh_buttons(self):
        # Обновление микрофона
        mic_name = self.device_combo.currentText() if self.device_combo.currentIndex() >= 0 else "не выбран"
        self.microphone_label.setText(f"🎤 Микрофон: {mic_name}")
        
        # Обновление режима
        if getattr(self.engine, "is_running", False):
            if getattr(self.engine, "continuous_mode", False):
                self.mode_label.setText("🔄 Режим: continuous")
            elif getattr(self.engine, "armed", False):
                self.mode_label.setText("🎙 Режим: ожидание команды")
            else:
                wake_engine = getattr(self.engine, "wakeword_engine", "vosk_text")
                self.mode_label.setText(f"👂 Режим: ожидание wake-word ({wake_engine})")
        else:
            self.mode_label.setText("⏸ Режим: не активен")
        
        # LOADING
        if getattr(self.engine, "is_loading", False):
            self.status_label.setText("Статус: LOADING (загрузка модели)")
            self.progress_bar.setVisible(True)
            self.progress_bar.setValue(self._loading_progress_value)
            self.btn_start.setEnabled(False)
            self.btn_stop.setEnabled(False)
            return

        # READY (после LOADING)
        if getattr(self.engine, "is_ready", False) and not getattr(self.engine, "is_running", False):
            self.progress_bar.setVisible(False)
            self.progress_bar.setValue(0)
            self._loading_progress_value = 0
            self.status_label.setText("Статус: READY (готов)")
            self.btn_start.setEnabled(True)
            self.btn_stop.setEnabled(False)
            return

        # RUNNING
        if getattr(self.engine, "is_running", False):
            self.progress_bar.setVisible(False)
            self.status_label.setText("Статус: RUNNING (слушаю)")
            self.btn_start.setEnabled(False)
            self.btn_stop.setEnabled(True)
            return

        # INIT / UNKNOWN
        self.progress_bar.setVisible(False)
        self.status_label.setText("Статус: INIT")
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

        if self.tray and str(msg).startswith("⏰ Напоминание:"):
            body = str(msg).replace("⏰ Напоминание:", "", 1).strip()
            self.tray.showMessage(
                "Jarvis — Напоминание",
                body or "Время напоминания наступило.",
                QSystemTrayIcon.MessageIcon.Information,
                6000,
            )
        if self.tray and str(msg).startswith("⏱ Таймер: время вышло"):
            body = str(msg).replace("⏱ Таймер:", "", 1).strip()
            self.tray.showMessage(
                "Jarvis — Таймер",
                body or "Время таймера вышло.",
                QSystemTrayIcon.MessageIcon.Information,
                6000,
            )

        # Привязываем прогресс-бар к реальному прогрессу загрузки из логов:
        # "📊 Загрузка: 50% (1/2)"
        match = re.search(r"Загрузка:\s*(\d+)%", msg)
        if match:
            progress = int(match.group(1))
            self._loading_progress_value = max(0, min(100, progress))
            if self.progress_bar.isVisible():
                self.progress_bar.setValue(self._loading_progress_value)

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

    def on_tts_preset_changed(self, _index: int):
        preset = self.tts_preset_combo.currentData()
        if not preset:
            return
        pr = TTS_PRESETS.get(preset, TTS_PRESETS["normal"])
        self.save_audio_setting("tts_preset", preset)
        self.save_audio_setting("tts_rate", pr["tts_rate"])
        self.save_audio_setting("tts_volume", pr["tts_volume"])
        try:
            self.engine.reload_config()
            self.append_log(f"🔊 Пресет озвучки: {self.tts_preset_combo.currentText()}")
        except Exception as error:
            self.append_log(f"❌ Не удалось применить пресет: {error}")

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
        """Тест микрофона с визуализацией уровня звука"""
        if self.device_combo.currentIndex() < 0:
            self.append_log("❌ Сначала выбери микрофон")
            return
        
        device_id = self.device_combo.itemData(self.device_combo.currentIndex())
        self.append_log(f"🎙 Тест микрофона (говори 3 секунды)...")
        
        try:
            import numpy as np
            duration = 3.0
            sample_rate = 16000
            
            recording = sd.rec(
                int(duration * sample_rate),
                samplerate=sample_rate,
                channels=1,
                device=device_id,
                dtype='int16'
            )
            sd.wait()
            
            # Анализ уровня звука
            audio_data = recording.flatten()
            max_amplitude = np.abs(audio_data).max()
            rms = np.sqrt(np.mean(audio_data.astype(np.float32)**2))
            
            # Нормализация к 100
            max_percent = (max_amplitude / 32768.0) * 100
            rms_percent = (rms / 32768.0) * 100
            
            result_msg = ""
            if max_percent < 1:
                result_msg = "⚠ Очень тихо! Проверь микрофон или увеличь громкость"
                self.mic_test_result.setStyleSheet("color: #ff0000; font-size: 10px;")
            elif max_percent < 10:
                result_msg = "🔉 Уровень низкий. Говори громче или ближе к микрофону"
                self.mic_test_result.setStyleSheet("color: #ff8800; font-size: 10px;")
            elif max_percent > 90:
                result_msg = "🔊 Уровень слишком высокий! Уменьши громкость микрофона"
                self.mic_test_result.setStyleSheet("color: #ff8800; font-size: 10px;")
            else:
                result_msg = f"✅ Микрофон работает (пик: {max_percent:.1f}%, средний: {rms_percent:.1f}%)"
                self.mic_test_result.setStyleSheet("color: #00aa00; font-size: 10px;")
            
            self.append_log(result_msg)
            self.mic_test_result.setText(result_msg)
            self.engine.speak_if_logged_phrase(result_msg)

        except Exception as e:
            error_msg = f"❌ Ошибка теста микрофона: {e}"
            self.append_log(error_msg)
            self.mic_test_result.setText(error_msg)
            self.mic_test_result.setStyleSheet("color: #ff0000; font-size: 10px;")
            self.engine.speak_if_logged_phrase(error_msg)


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

            preset = str(audio.get("tts_preset", "normal")).strip().lower()
            pidx = self.tts_preset_combo.findData(preset)
            self.tts_preset_combo.blockSignals(True)
            self.tts_preset_combo.setCurrentIndex(pidx if pidx >= 0 else 1)
            self.tts_preset_combo.blockSignals(False)

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
