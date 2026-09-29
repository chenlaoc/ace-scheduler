"""Read-only PDH and storage discovery. Called only in the disposable helper."""
import ctypes as C
from ctypes import wintypes as W
import hashlib
import math
import re
import struct
import time
import uuid
import winreg

COUNTERS = {
    "read_B_s": "Disk Read Bytes/sec", "write_B_s": "Disk Write Bytes/sec",
    "read_iops": "Disk Reads/sec", "write_iops": "Disk Writes/sec",
    "read_latency_s": "Avg. Disk sec/Read", "write_latency_s": "Avg. Disk sec/Write",
    "queue_mean": "Avg. Disk Queue Length", "queue_current": "Current Disk Queue Length",
}


class Value(C.Structure):
    _fields_ = [("status", W.DWORD), ("value", C.c_double)]


class RawValue(C.Structure):
    _fields_ = [("status", W.DWORD), ("stamp", W.FILETIME), ("first", C.c_longlong),
                ("second", C.c_longlong), ("multi", W.DWORD)]


def bind(library, name, args, result=W.DWORD):
    fn = getattr(library, name)
    fn.argtypes, fn.restype = args, result
    return fn


def check(status):
    if status:
        raise OSError(f"PDH 0x{status:08x}")


class Pdh:
    def __init__(self):
        self.api = C.WinDLL("pdh")
        pointer = C.POINTER(W.DWORD)
        bind(self.api, "PdhOpenQueryW", [W.LPCWSTR, C.c_size_t, C.POINTER(W.HANDLE)])
        bind(self.api, "PdhAddEnglishCounterW", [W.HANDLE, W.LPCWSTR, C.c_size_t, C.POINTER(W.HANDLE)])
        bind(self.api, "PdhCollectQueryData", [W.HANDLE])
        bind(self.api, "PdhGetFormattedCounterValue", [W.HANDLE, W.DWORD, pointer, C.POINTER(Value)])
        bind(self.api, "PdhGetRawCounterValue", [W.HANDLE, pointer, C.POINTER(RawValue)])
        bind(self.api, "PdhCloseQuery", [W.HANDLE])
        bind(self.api, "PdhLookupPerfNameByIndexW", [W.LPCWSTR, W.DWORD, W.LPWSTR, pointer])
        bind(self.api, "PdhEnumObjectsW", [W.LPCWSTR, W.LPCWSTR, W.LPWSTR, pointer, W.DWORD, W.BOOL])
        bind(self.api, "PdhEnumObjectItemsW", [W.LPCWSTR, W.LPCWSTR, W.LPCWSTR, W.LPWSTR, pointer,
                                              W.LPWSTR, pointer, W.DWORD, W.DWORD])

    def instances(self):
        # HKEY_PERFORMANCE_TEXT (English) is not exposed by Python's winreg.
        english_key = C.c_void_p(C.c_int32(0x80000050).value).value
        values = winreg.QueryValueEx(english_key, "Counter")[0]
        index = next(int(values[i]) for i in range(0, len(values) - 1, 2) if values[i + 1] == "PhysicalDisk")
        size = W.DWORD(1024)
        name = C.create_unicode_buffer(size.value)
        check(self.api.PdhLookupPerfNameByIndexW(None, index, name, C.byref(size)))
        size = W.DWORD()
        status = self.api.PdhEnumObjectsW(None, None, None, C.byref(size), 400, True)
        if status not in (0, 0x800007D2):
            check(status)
        for _ in range(3):
            nc, ni = W.DWORD(), W.DWORD()
            status = self.api.PdhEnumObjectItemsW(None, None, name.value, None, C.byref(nc), None, C.byref(ni), 400, 0)
            if status not in (0, 0x800007D2):
                check(status)
            if max(nc.value, ni.value) > 1_000_000:
                raise ValueError("PDH 枚举超过容量")
            counters, instances = C.create_unicode_buffer(max(2, nc.value)), C.create_unicode_buffer(max(2, ni.value))
            status = self.api.PdhEnumObjectItemsW(None, None, name.value, counters, C.byref(nc), instances, C.byref(ni), 400, 0)
            if status == 0:
                return sorted(s for s in instances[:ni.value].split("\0") if re.match(r"^\d+(?:\s|$)", s))
        check(status)


class Storage:
    def __init__(self):
        self.api = C.WinDLL("kernel32", use_last_error=True)
        bind(self.api, "CreateFileW", [W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p, W.DWORD, W.DWORD, W.HANDLE], W.HANDLE)
        bind(self.api, "CloseHandle", [W.HANDLE], W.BOOL)
        bind(self.api, "DeviceIoControl", [W.HANDLE, W.DWORD, C.c_void_p, W.DWORD, C.c_void_p,
                                           W.DWORD, C.POINTER(W.DWORD), C.c_void_p], W.BOOL)
        bind(self.api, "GetLogicalDrives", [], W.DWORD)

    def query(self, path, code, request=b""):
        handle = self.api.CreateFileW(path, 0, 3, None, 3, 0, None)
        if handle == W.HANDLE(-1).value:
            raise C.WinError(C.get_last_error())
        try:
            result, count = C.create_string_buffer(65536), W.DWORD()
            if not self.api.DeviceIoControl(handle, code, request or None, len(request), result,
                                           len(result), C.byref(count), None):
                raise C.WinError(C.get_last_error())
            return result.raw[:count.value]
        finally:
            self.api.CloseHandle(handle)

    def identity(self, instance):
        number = int(instance.split()[0])
        data = {"instance": instance, "number": number, "name": "未知设备", "fingerprint": None,
                "volumes": [], "mapping_source": "IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS (drive letters only)",
                "uncertainty": []}
        try:
            raw = self.query(rf"\\.\PhysicalDrive{number}", 0x2D1400, struct.pack("III", 0, 0, 0))
            if len(raw) < 36:
                raise ValueError("storage descriptor too short")
            def text(offset):
                return raw[offset:].split(b"\0")[0].decode("ascii", "replace").strip() if 0 < offset < len(raw) else ""
            vendor, product, revision, serial = (text(v) for v in struct.unpack_from("IIII", raw, 12))
            data["name"] = " ".join(filter(None, (vendor, product))) or "未知设备"
            if serial:
                data["fingerprint"] = hashlib.sha256((vendor + "|" + product + "|" + serial).encode()).hexdigest()
            else:
                data["uncertainty"].append("设备未返回序列号；仅保证本次采集内的身份，重新枚举不续接")
        except (OSError, ValueError) as exc:
            data["uncertainty"].append("设备身份无法核验：" + str(exc))
        return data

    def volumes(self, devices):
        mask = self.api.GetLogicalDrives()
        for n in range(26):
            if not mask & (1 << n):
                continue
            letter = chr(65 + n) + ":"
            try:
                raw = self.query("\\\\.\\" + letter, 0x560000)
                count = struct.unpack_from("I", raw)[0]
                if not 0 < count <= 128 or len(raw) < 8 + count * 24:
                    raise ValueError("invalid volume extents")
                numbers = [struct.unpack_from("I", raw, 8 + i * 24)[0] for i in range(count)]
                for device in devices:
                    if device["number"] in numbers:
                        device["volumes"].append(letter)
                        if len(set(numbers)) > 1:
                            device["uncertainty"].append(letter + " 跨多个磁盘")
            except (OSError, ValueError, struct.error):
                for device in devices:
                    device["uncertainty"].append(letter + " 盘符映射无法核验")


class DiskQuery:
    def __init__(self, pdh, device):
        self.pdh, self.device = pdh, device
        self.handle, self.counters, self.errors = W.HANDLE(), {}, {}
        self.previous = None
        self.raw_previous = {}
        self.invalidated = False
        check(pdh.api.PdhOpenQueryW(None, 0, C.byref(self.handle)))
        try:
            for field, counter in COUNTERS.items():
                handle = W.HANDLE()
                status = pdh.api.PdhAddEnglishCounterW(self.handle, f"\\PhysicalDisk({device['instance']})\\{counter}", 0, C.byref(handle))
                if status:
                    self.errors[field] = f"add:0x{status:08x}"
                else:
                    self.counters[field] = handle
        except Exception:
            self.close()
            raise

    def close(self):
        if self.handle:
            self.pdh.api.PdhCloseQuery(self.handle)
            self.handle = None

    def sample(self):
        start = time.perf_counter()
        status = self.pdh.api.PdhCollectQueryData(self.handle)
        stamp = time.monotonic()
        cost = time.perf_counter() - start
        seconds = stamp - self.previous if self.previous is not None else None
        gap = "warmup" if seconds is None else "sampling_pause" if seconds > 3 else "timeout" if cost > 2 else None
        if status:
            gap = f"collect:0x{status:08x}"
        for field in ("read_B_s", "write_B_s", "read_iops", "write_iops"):
            if field not in self.counters:
                continue
            raw = RawValue()
            result = self.pdh.api.PdhGetRawCounterValue(self.counters[field], None, C.byref(raw))
            if not result and raw.status in (0, 1):
                if field in self.raw_previous and raw.first < self.raw_previous[field]:
                    gap = "counter_reset"
                self.raw_previous[field] = raw.first
            else:
                self.raw_previous.pop(field, None)
        values, errors = {}, dict(self.errors)
        for field in COUNTERS:
            value = Value()
            code = self.pdh.api.PdhGetFormattedCounterValue(self.counters[field], 0x200 | 0x8000, None, C.byref(value)) if field in self.counters else 1
            valid = not code and value.status in (0, 1) and math.isfinite(value.value) and value.value >= 0
            values[field] = value.value if valid and not gap else None
            if not valid and field not in errors:
                errors[field] = f"format:0x{code:08x}/status:0x{value.status:08x}"
        for direction in ("read", "write"):
            if values[direction + "_iops"] in (None, 0):
                values[direction + "_latency_s"] = None
                errors[direction + "_latency_s"] = "no_requests" if values[direction + "_iops"] == 0 else "request_count_unavailable"
        self.previous = stamp if not status else None
        return {"timestamp": stamp, "seconds": seconds if not gap else None,
                "values": values, "errors": errors, "gap": gap, "sample_cost_seconds": cost}


class DiskCollector:
    def __init__(self):
        self.pdh, self.storage = Pdh(), Storage()
        self.queries = {}
        self.last_discovery = 0

    def discover(self):
        instances = self.pdh.instances()
        if len(instances) > 16:
            raise ValueError("超过 16 个物理磁盘实例，请减少设备后重新采集")
        devices = [self.storage.identity(instance) for instance in instances]
        self.storage.volumes(devices)
        keep = {}
        try:
            for device in devices:
                old = self.queries.get(device["instance"])
                if old and not old.invalidated and device["fingerprint"] and old.device["fingerprint"] == device["fingerprint"] and old.device["volumes"] == device["volumes"]:
                    device["id"] = old.device["id"]
                    old.device = device
                    keep[device["instance"]] = old
                else:
                    device["id"] = uuid.uuid4().hex
                    keep[device["instance"]] = DiskQuery(self.pdh, device)
        except Exception:
            for key, query in keep.items():
                if self.queries.get(key) is not query:
                    query.close()
            raise
        for key, query in self.queries.items():
            if keep.get(key) is not query:
                query.close()
        self.queries = keep
        self.last_discovery = time.monotonic()
        return devices

    def sample(self):
        # Verify descriptor identity each interval, not just the PDH instance name.
        rows = []
        for query in self.queries.values():
            current = self.storage.identity(query.device["instance"])
            if not current["fingerprint"] or current["fingerprint"] != query.device["fingerprint"] or current["name"] != query.device["name"]:
                query.previous = None
                query.invalidated = True
                rows.append({"device_id": query.device["id"], "timestamp": time.monotonic(), "seconds": None,
                             "values": dict.fromkeys(COUNTERS), "errors": {},
                             "gap": "identity_changed" if current["fingerprint"] else "identity_unverified", "sample_cost_seconds": 0})
                self.last_discovery = 0
                continue
            rows.append({"device_id": query.device["id"], **query.sample()})
        return rows

    def close(self):
        for query in self.queries.values():
            query.close()
        self.queries.clear()
