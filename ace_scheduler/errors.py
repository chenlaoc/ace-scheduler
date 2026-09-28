import logging
import sys
import threading

from PySide6.QtCore import QObject, Signal, Slot


class ExceptionReporter(QObject):
    raised = Signal(str)

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        window.exception_reporter = self
        self.reported = False
        self.previous = sys.excepthook
        self.previous_thread = threading.excepthook
        self.raised.connect(self.notify)
        sys.excepthook = self.handle
        threading.excepthook = self.handle_thread

    def handle(self, kind, value, traceback):
        if issubclass(kind, (KeyboardInterrupt, SystemExit)):
            self.previous(kind, value, traceback)
            return
        # One persistent banner per incident, no modal or repeating traceback storm.
        if self.reported:
            return
        self.reported = True
        logging.getLogger("ace_scheduler").error("Unhandled application exception", exc_info=(kind, value, traceback))
        self.raised.emit("应用发生未处理异常（" + kind.__name__ + "），维护已停止")

    def handle_thread(self, args):
        self.handle(args.exc_type, args.exc_value, args.exc_traceback)

    @Slot(str)
    def notify(self, message):
        self.window.halt_requested.emit(message)
        self.window.on_worker_failure(message)

    def close(self):
        sys.excepthook = self.previous
        threading.excepthook = self.previous_thread
