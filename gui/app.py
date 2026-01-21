import sys
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QTextEdit, QPushButton,
    QVBoxLayout, QWidget, QSystemTrayIcon, QMenu, QLabel
)

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
        self.resize(700, 450)

        self.log_view = QTextEdit()
        self.status_label = QLabel("Статус: INIT")
        self.log_view.setReadOnly(True)

        self.btn_start = QPushButton("Старт")
        self.btn_stop = QPushButton("Стоп")

        self.btn_start.clicked.connect(self.engine.start)
        self.btn_stop.clicked.connect(self.engine.stop)

        layout = QVBoxLayout()
        layout.addWidget(self.status_label)
        layout.addWidget(self.log_view)
        layout.addWidget(self.btn_start)
        layout.addWidget(self.btn_stop)
        

        w = QWidget()
        w.setLayout(layout)
        self.setCentralWidget(w)

        self.bus.log.connect(self.append_log)

        self.btn_stop.setEnabled(False)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_buttons)
        self.timer.start(300)

    def refresh_buttons(self):
        # LOADING
        if getattr(self.engine, "is_loading", False):
            self.status_label.setText("Статус: LOADING (загрузка модели)")
            self.btn_start.setEnabled(False)
            self.btn_stop.setEnabled(False)
            return

        # RUNNING
        if getattr(self.engine, "is_running", False):
            self.status_label.setText("Статус: RUNNING (слушаю)")
            self.btn_start.setEnabled(False)
            self.btn_stop.setEnabled(True)
            return

        # READY
        if getattr(self.engine, "is_ready", False):
            self.status_label.setText("Статус: READY (готов)")
            self.btn_start.setEnabled(True)
            self.btn_stop.setEnabled(False)
            return

        # INIT / UNKNOWN
        self.status_label.setText("Статус: INIT")
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(False)



    def append_log(self, msg: str):
        self.log_view.append(msg)


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
