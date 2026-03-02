import sys
import logging
import threading
import re
import argparse
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QTextEdit, QPushButton,
    QVBoxLayout, QWidget, QSystemTrayIcon, QMenu, QLabel,
    QComboBox, QHBoxLayout, QProgressBar, QTabWidget, QDoubleSpinBox, QStyle
)

import sounddevice as sd

from PySide6.QtGui import QAction, QIcon
from PySide6.QtCore import Signal, QObject, QTimer, QEvent

from src.jarvis.engine import JarvisEngine

logger = logging.getLogger(__name__)


class LogBus(QObject):
    log = Signal(str)


class MainWindow(QMainWindow):
    def __init__(self, engine: JarvisEngine, bus: LogBus):
        super().__init__()
        self.engine = engine
        self.bus = bus
        self.tray: QSystemTrayIcon | None = None
        self._quit_requested = False
        self._loading_progress_value = 0

        self.setWindowTitle("Jarvis Assistant")
        self.resize(800, 600)

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
        
        self.setCentralWidget(self.tabs)

        self.bus.log.connect(self.append_log)
        self.load_devices()
        
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_buttons)
        self.timer.start(100)

    def set_tray(self, tray: QSystemTrayIcon):
        self.tray = tray

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
        
        # Логирование
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        layout.addWidget(QLabel("Лог:"))
        layout.addWidget(self.log_view)
        
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
    
    def setup_settings_tab(self):
        """Настройка вкладки настроек"""
        layout = QVBoxLayout()
        
        # Выбор микрофона
        device_layout = QHBoxLayout()
        device_layout.addWidget(QLabel("Микрофон:"))
        self.device_combo = QComboBox()
        self.device_combo.currentIndexChanged.connect(self.on_device_changed)
        device_layout.addWidget(self.device_combo)
        
        self.btn_refresh_devices = QPushButton("🔄 Обновить")
        self.btn_refresh_devices.clicked.connect(self.load_devices)
        device_layout.addWidget(self.btn_refresh_devices)
        
        layout.addLayout(device_layout)
        
        # Параметры ASR
        params_group_label = QLabel("Параметры распознавания:")
        params_font = params_group_label.font()
        params_font.setBold(True)
        params_group_label.setFont(params_font)
        layout.addWidget(params_group_label)
        
        # Таймаут фразы
        timeout_layout = QHBoxLayout()
        timeout_layout.addWidget(QLabel("Таймаут фразы (сек):"))
        self.timeout_spinbox = QDoubleSpinBox()
        self.timeout_spinbox.setMinimum(1.0)
        self.timeout_spinbox.setMaximum(30.0)
        self.timeout_spinbox.setValue(6.0)
        self.timeout_spinbox.setSingleStep(0.5)
        self.timeout_spinbox.setDecimals(1)
        self.timeout_spinbox.setToolTip("Максимальное время ожидания фразы")
        timeout_layout.addWidget(self.timeout_spinbox)
        timeout_layout.addStretch()
        layout.addLayout(timeout_layout)
        
        # Таймаут молчания
        silence_layout = QHBoxLayout()
        silence_layout.addWidget(QLabel("Таймаут молчания (сек):"))
        self.silence_spinbox = QDoubleSpinBox()
        self.silence_spinbox.setMinimum(0.1)
        self.silence_spinbox.setMaximum(10.0)
        self.silence_spinbox.setValue(1.2)
        self.silence_spinbox.setSingleStep(0.1)
        self.silence_spinbox.setDecimals(1)
        self.silence_spinbox.setToolTip("Время тишины для завершения фразы")
        silence_layout.addWidget(self.silence_spinbox)
        silence_layout.addStretch()
        layout.addLayout(silence_layout)
        
        # Информация
        info_label = QLabel(
            "💡 Информация:\n"
            "• Таймаут фразы: максимальное время ожидания одной фразы\n"
            "• Таймаут молчания: время тишины, после которого фраза считается завершённой\n"
            "• Нажми 'Обновить' для переподключения микрофона"
        )
        info_label.setStyleSheet("color: #666; font-size: 10px;")
        layout.addWidget(info_label)
        
        layout.addStretch()
        self.settings_tab.setLayout(layout)

    def refresh_buttons(self):
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
        self.log_view.append(msg)

        # Привязываем прогресс-бар к реальному прогрессу загрузки из логов:
        # "📊 Загрузка: 50% (1/2)"
        match = re.search(r"Загрузка:\s*(\d+)%", msg)
        if match:
            progress = int(match.group(1))
            self._loading_progress_value = max(0, min(100, progress))
            if self.progress_bar.isVisible():
                self.progress_bar.setValue(self._loading_progress_value)

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
