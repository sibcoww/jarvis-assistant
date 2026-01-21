import sys
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QTextEdit, QPushButton,
    QVBoxLayout, QWidget, QSystemTrayIcon, QMenu
)
from PySide6.QtGui import QAction, QIcon
from PySide6.QtCore import Signal, QObject

from src.jarvis.vosk_asr import VoskASR
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
        self.log_view.setReadOnly(True)

        self.btn_start = QPushButton("Старт")
        self.btn_stop = QPushButton("Стоп")

        self.btn_start.clicked.connect(self.engine.start)
        self.btn_stop.clicked.connect(self.engine.stop)

        layout = QVBoxLayout()
        layout.addWidget(self.log_view)
        layout.addWidget(self.btn_start)
        layout.addWidget(self.btn_stop)

        w = QWidget()
        w.setLayout(layout)
        self.setCentralWidget(w)

        self.bus.log.connect(self.append_log)

    def append_log(self, msg: str):
        self.log_view.append(msg)


def main():
    app = QApplication(sys.argv)

    bus = LogBus()

    # создаём ASR
    model_path = "models/vosk-model-ru-0.42"
    asr = VoskASR(model_path=model_path)

    # движок + лог callback в Qt сигнал
    engine = JarvisEngine(asr=asr, log=lambda m: bus.log.emit(m))

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
