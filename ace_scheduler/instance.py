"""Same-user local activation only; the QLockFile remains the ownership authority."""
import json
import secrets
import time

from PySide6.QtCore import QObject, QTimer, Signal, Slot
from PySide6.QtNetwork import QLocalServer, QLocalSocket


class InstanceServer(QObject):
    activated = Signal()

    def __init__(self, directory, parent=None):
        super().__init__(parent)
        self.path = directory / "instance.json"
        self.token = secrets.token_hex(32)
        self.name = "ace-scheduler-" + secrets.token_hex(24)
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.server.setMaxPendingConnections(8)
        self.server.newConnection.connect(self._accept)
        self.connections = set()
        if not self.server.listen(self.name):
            raise OSError(self.server.errorString())
        try:
            self.path.write_text(json.dumps({"name": self.name, "token": self.token}), encoding="utf-8")
        except OSError:
            self.server.close()
            raise

    @Slot()
    def _accept(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            if len(self.connections) >= 8:
                socket.abort()
                socket.deleteLater()
                continue
            connection = ActivationConnection(socket, self)
            self.connections.add(connection)
            connection.read()

    def close(self):
        for connection in tuple(self.connections):
            connection.socket.abort()
        self.server.close()
        try:
            if json.loads(self.path.read_text(encoding="utf-8")).get("token") == self.token:
                self.path.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass


class ActivationConnection(QObject):
    def __init__(self, socket, owner):
        super().__init__(owner)
        self.owner = owner
        self.socket = socket
        self.acknowledged = False
        socket.setParent(self)
        socket.setReadBufferSize(256)
        socket.readyRead.connect(self.read)
        socket.disconnected.connect(self.drop)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.expire)
        self.timer.start(1500)

    @Slot()
    def expire(self):
        self.socket.abort()
        self.drop()

    @Slot()
    def drop(self):
        self.timer.stop()
        self.owner.connections.discard(self)
        self.deleteLater()

    @Slot()
    def read(self):
        socket = self.socket
        if self.acknowledged:
            socket.readAll()
            return
        if socket.bytesAvailable() >= 256:
            socket.abort()
        elif socket.canReadLine():
            if bytes(socket.readLine()) == f"activate {self.owner.token}\n".encode("ascii"):
                self.acknowledged = True
                self.owner.activated.emit()
                socket.write(b"ok\n")
                socket.flush()
                # Keep the pipe alive until its client consumes the acknowledgement.
            else:
                socket.abort()


def activate_existing(directory, timeout_ms=2000):
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        socket = QLocalSocket()
        try:
            path = directory / "instance.json"
            if path.stat().st_size > 1024:
                return False
            data = json.loads(path.read_text(encoding="utf-8"))
            name, token = data["name"], data["token"]
            if not isinstance(name, str) or not name.startswith("ace-scheduler-") or len(name) > 100:
                return False
            if not isinstance(token, str) or len(token) != 64 or any(c not in "0123456789abcdef" for c in token):
                return False
            socket.connectToServer(name)
            if socket.waitForConnected(150):
                socket.write(f"activate {token}\n".encode("ascii"))
                socket.waitForBytesWritten(150)
                if (socket.bytesAvailable() or socket.waitForReadyRead(300)) and bytes(socket.readAll()) == b"ok\n":
                    return True
        except (OSError, ValueError, KeyError, TypeError):
            pass
        finally:
            socket.abort()
        time.sleep(.05)
    return False
