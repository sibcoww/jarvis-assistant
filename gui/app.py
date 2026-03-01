import sys
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QTextEdit, QPushButton,
    QVBoxLayout, QWidget, QSystemTrayIcon, QMenu, QLabel,
    QComboBox, QHBoxLayout, QProgressBar
)

import sounddevice as sd

from PySide6.QtGui import QAction, QIcon
from PySide6.QtCore import Signal, QObject, QTimer

from src.jarvis.engine import JarvisEngine



class LogBus(QObject):
    log = Signal(str)


class MainWindow(QMainWindow):
    def __init__(self, engine: JarvisEngine, bus: LogBus):
        super().__init__()
        self.engine = engine
        self.bus = bus

        self.setWindowTitle("Jarvis Assistant")
        self.resize(700, 500)

        self.log_view = QTextEdit()
        self.status_label = QLabel("Статус: INIT")
        self.log_view.setReadOnly(True)
        self.device_combo = QComboBox()
        self.btn_refresh_devices = QPushButton("Обновить микрофоны")
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)

        self.btn_refresh_devices.clicked.connect(self.load_devices)
        self.device_combo.currentIndexChanged.connect(self.on_device_changed)

        self.btn_start = QPushButton("Старт")
        self.btn_stop = QPushButton("Стоп")

        self.btn_start.clicked.connect(self.engine.start)
        self.btn_stop.clicked.connect(self.engine.stop)

        layout = QVBoxLayout()
        layout.addWidget(self.status_label)
        layout.addWidget(self.progress_bar)
        
        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("Микрофон:"))
        top_row.addWidget(self.device_combo)
        top_row.addWidget(self.btn_refresh_devices)
        
        layout.addLayout(top_row)
        layout.addWidget(self.log_view)
        layout.addWidget(self.btn_start)
        layout.addWidget(self.btn_stop)

        w = QWidget()
        w.setLayout(layout)
        self.setCentralWidget(w)

        self.bus.log.connect(self.append_log)
        self.load_devices() 

        self.btn_stop.setEnabled(False)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_buttons)
        self.timer.start(100)  # обновляем чаще для более плавного UI

    def refresh_buttons(self):
        # LOADING
        if getattr(self.engine, "is_loading", False):
            self.status_label.setText("Статус: LOADING (загрузка модели)")
            self.progress_bar.setVisible(True)
            if self.progress_bar.value() < 100:
                self.progress_bar.setValue(self.progress_bar.value() + 2)  # плавное увеличение
            self.btn_start.setEnabled(False)
            self.btn_stop.setEnabled(False)
            return

        # READY (после LOADING)
        if getattr(self.engine, "is_ready", False) and not getattr(self.engine, "is_running", False):
            self.progress_bar.setVisible(False)
            self.progress_bar.setValue(0)
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
        self.engine.set_device(device_index)

def main():
    app = QApplication(sys.argv)

    bus = LogBus()

    # движок + лог callback в Qt сигнал
    engine = JarvisEngine(asr=None, log=lambda m: bus.log.emit(m))
    import threading
    threading.Thread(target=engine.preload, daemon=True).start()

    win = MainWindow(engine, bus)

    # трей
    tray = QSystemTrayIcon()
    tray.setIcon(QIcon())  # можно позже поставить иконку
    tray.setToolTip("Jarvis Assistant")

    menu = QMenu()
    act_show = QAction("Открыть")
    act_start = QAction("Старт")
    act_stop = QAction("Стоп")
    act_quit = QAction("Выход")

    act_show.triggered.connect(win.show)
    act_start.triggered.connect(engine.start)
    act_stop.triggered.connect(engine.stop)
    act_quit.triggered.connect(lambda: (engine.stop(), app.quit()))

    menu.addAction(act_show)
    menu.addSeparator()
    menu.addAction(act_start)
    menu.addAction(act_stop)
    menu.addSeparator()
    menu.addAction(act_quit)

    tray.setContextMenu(menu)
    tray.show()

    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
