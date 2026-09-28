from contextlib import contextmanager
from types import SimpleNamespace

from ace_scheduler.windows.eco_qos import EcoState


class FakeApi:
    def __init__(self):
        self.values = {}
        self.writes = []
        self.fail = set()
        self.opened = self.closed = 0

    @contextmanager
    def open(self, identity, write=False):
        if "open" in self.fail:
            raise OSError("access denied")
        self.values.setdefault(identity, {"priority": 0x20, "affinity": tuple(range(8)), "eco": EcoState(0, 0)})
        self.opened += 1
        try:
            yield identity
        finally:
            self.closed += 1

    def __getattr__(self, method):
        action, field = method.split("_", 1)
        if action == "get":
            def read(handle):
                if method in self.fail:
                    raise OSError("unsupported")
                return self.values[handle][field]
            return read
        def write(handle, value):
            if method in self.fail:
                raise OSError("access denied")
            self.writes.append((handle, field, value))
            if field == "eco" and isinstance(value, bool):
                value = EcoState(1, int(value))
            self.values[handle][field] = value
        return write


class FakeProcess:
    def __init__(self, pid, created=1.0, name="test.exe"):
        self.pid = pid
        self.created = created
        self._name = name
        self.info = {"pid": pid, "name": name}
        self.running = True

    def name(self): return self._name
    def create_time(self): return self.created
    def is_running(self): return self.running
    def cpu_times(self): return SimpleNamespace(user=0, system=0)
    def io_counters(self): return SimpleNamespace(read_bytes=0, write_bytes=0, read_count=0, write_count=0)
    def memory_info(self): return SimpleNamespace(rss=10)
