"""PC stats for the keyboard's PC stats screen (REVIVER v24+). The screen has three slots (left,
middle, right); the user picks what each one measures: CPU / GPU / disk usage or temperature, or
memory usage. The keyboard shows two digits (0-99) and a °C or % mark per slot.

Usage comes from Windows (no admin needed):
  CPU     \\Processor Information(_Total)\\% Processor Utility   (what Task Manager shows)
  GPU     \\GPU Engine(*engtype_3D)\\Utilization Percentage      (summed over processes)
  Disk    100 - \\PhysicalDisk(_Total)\\% Idle Time              (active time)
  Memory  GlobalMemoryStatusEx, dwMemoryLoad                      (RAM in use)

Temperatures, in order of preference:
  LibreHardwareMonitor's web server (Options > Remote Web Server, default port 8085): CPU, GPU, disk
  NVIDIA's driver (nvml.dll): GPU
Windows itself has no reliable CPU or disk temperature for normal programs, so those need
LibreHardwareMonitor running (as administrator, with its web server on).

Settings: cfg["stats_sources"] = {"cpu": "cpu_usage", "gpu": "gpu_temp", "disk": "disk_usage"}, keyed
by slot (the slot keys are the stock screen's left / middle / right). The older cfg["stats_kinds"]
({"cpu": "usage" | "temp", ...}) is still read when there is no stats_sources.
"""
import ctypes, json, re, sys, threading, time, urllib.request

SLOTS = ("cpu", "gpu", "disk")
SLOT_NAMES = {"cpu": "Left", "gpu": "Middle", "disk": "Right"}
NAMES = {"cpu": "CPU", "gpu": "GPU", "disk": "Disk"}
KINDS = {"temp": "Temperature (°C)", "usage": "Usage (%)"}
# source id -> (label, device, kind)
SOURCES = {
    "cpu_usage": ("CPU usage (%)", "cpu", "usage"),
    "cpu_temp": ("CPU temperature (°C)", "cpu", "temp"),
    "gpu_usage": ("GPU usage (%)", "gpu", "usage"),
    "gpu_temp": ("GPU temperature (°C)", "gpu", "temp"),
    "mem_usage": ("Memory usage (%)", "mem", "usage"),
    "disk_usage": ("Disk usage (%)", "disk", "usage"),
    "disk_temp": ("Disk temperature (°C)", "disk", "temp"),
}
DEFAULT_SOURCES = {"cpu": "cpu_usage", "gpu": "gpu_temp", "disk": "disk_usage"}
# The keyboard's label under each number (REVIVER v25+, 70 24). The left and middle boxes fit 3
# characters, the right one 4; the stock pictures say CPU / GPU / DISK.
LABELS_PATCH = 25
LABELS = {"cpu": ("CPU", "CPU"), "gpu": ("GPU", "GPU"), "mem": ("RAM", "RAM"), "disk": ("DSK", "DISK")}
DEFAULT_KINDS = {s: SOURCES[v][2] for s, v in DEFAULT_SOURCES.items()}
LHM_URL = "http://localhost:8085/data.json"


def sources(cfg):
    """slot -> source id."""
    out = dict(DEFAULT_SOURCES)
    saved = cfg.get("stats_sources")
    if isinstance(saved, dict):
        out.update({s: v for s, v in saved.items() if s in SLOTS and v in SOURCES})
    else:                                            # settings from before memory etc. were choices
        for s, v in (cfg.get("stats_kinds") or {}).items():
            if s in SLOTS and v in KINDS:
                out[s] = f"{s}_{v}"
    return out


def kinds(cfg):
    """slot -> "usage" | "temp"."""
    return {s: SOURCES[v][2] for s, v in sources(cfg).items()}


def labels(cfg):
    """slot -> the text the keyboard shows under that number."""
    return {s: LABELS[SOURCES[v][1]][1 if s == "disk" else 0] for s, v in sources(cfg).items()}


def label_payload(slot, text):
    """70 24 argument: slot index, then 4 characters (0-padded)."""
    return bytes([SLOTS.index(slot)]) + text.upper().encode("ascii", "replace")[:4].ljust(4, b"\0")


def units_bits(k):
    """70 23 argument: bit i set = slot i shows %."""
    return sum(1 << i for i, s in enumerate(SLOTS) if k[s] == "usage")


def to_byte(v):
    return 0 if v is None else max(0, min(99, int(round(v))))


# --- Windows performance counters ------------------------------------------------------------
class _Pdh:
    PDH_FMT_DOUBLE = 0x200
    PDH_MORE_DATA = 0x800007D2

    class Value(ctypes.Structure):
        _fields_ = [("CStatus", ctypes.c_uint32), ("_pad", ctypes.c_uint32), ("doubleValue", ctypes.c_double)]

    class Item(ctypes.Structure):
        pass

    def __init__(self):
        self.pdh = ctypes.windll.pdh
        self.Item._fields_ = [("szName", ctypes.c_wchar_p), ("FmtValue", self.Value)]
        self.q = ctypes.c_void_p()
        if self.pdh.PdhOpenQueryW(None, 0, ctypes.byref(self.q)):
            raise OSError("PdhOpenQuery failed")
        self.h = {}
        for key, paths in (("cpu", [r"\Processor Information(_Total)\% Processor Utility",
                                    r"\Processor(_Total)\% Processor Time"]),
                           ("gpu", [r"\GPU Engine(*engtype_3D)\Utilization Percentage"]),
                           ("disk", [r"\PhysicalDisk(_Total)\% Idle Time"])):
            for p in paths:
                h = ctypes.c_void_p()
                if self.pdh.PdhAddEnglishCounterW(self.q, p, 0, ctypes.byref(h)) == 0:
                    self.h[key] = h
                    break
        self.pdh.PdhCollectQueryData(self.q)

    def _one(self, h):
        v = self.Value()
        if self.pdh.PdhGetFormattedCounterValue(h, self.PDH_FMT_DOUBLE, None, ctypes.byref(v)):
            return None
        return v.doubleValue

    def _sum(self, h):
        size, count = ctypes.c_uint32(0), ctypes.c_uint32(0)
        r = self.pdh.PdhGetFormattedCounterArrayW(h, self.PDH_FMT_DOUBLE, ctypes.byref(size), ctypes.byref(count), None)
        if (r & 0xFFFFFFFF) != self.PDH_MORE_DATA or not size.value:
            return 0.0 if count.value == 0 else None
        buf = (ctypes.c_byte * size.value)()
        if self.pdh.PdhGetFormattedCounterArrayW(h, self.PDH_FMT_DOUBLE, ctypes.byref(size), ctypes.byref(count), buf):
            return None
        items = ctypes.cast(buf, ctypes.POINTER(self.Item))
        return sum(items[i].FmtValue.doubleValue for i in range(count.value) if items[i].FmtValue.CStatus in (0, 1))

    def sample(self):
        if self.pdh.PdhCollectQueryData(self.q):
            return {}
        out = {}
        if "cpu" in self.h:
            out["cpu"] = self._one(self.h["cpu"])
        if "gpu" in self.h:
            out["gpu"] = self._sum(self.h["gpu"])
        if "disk" in self.h:
            idle = self._one(self.h["disk"])
            out["disk"] = None if idle is None else 100 - idle
        return {k: (None if v is None else max(0.0, min(100.0, v))) for k, v in out.items()}


class _MemStatus(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_uint32), ("dwMemoryLoad", ctypes.c_uint32),
                ("ullTotalPhys", ctypes.c_uint64), ("ullAvailPhys", ctypes.c_uint64),
                ("ullTotalPageFile", ctypes.c_uint64), ("ullAvailPageFile", ctypes.c_uint64),
                ("ullTotalVirtual", ctypes.c_uint64), ("ullAvailVirtual", ctypes.c_uint64),
                ("ullAvailExtendedVirtual", ctypes.c_uint64)]


def mem_usage():
    """RAM in use (%), or None where Windows' GlobalMemoryStatusEx isn't available."""
    if sys.platform != "win32":
        return None
    m = _MemStatus()
    m.dwLength = ctypes.sizeof(m)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
        return None
    if m.ullTotalPhys:
        return 100.0 * (m.ullTotalPhys - m.ullAvailPhys) / m.ullTotalPhys
    return float(m.dwMemoryLoad)


# --- temperatures ----------------------------------------------------------------------------
def _num(text):
    m = re.search(r"-?\d+(?:[.,]\d+)?", text or "")
    return float(m.group(0).replace(",", ".")) if m else None


def lhm_temps(data):
    """LibreHardwareMonitor data.json -> {"cpu"/"gpu"/"disk": °C} (the hottest sensible sensor)."""
    found = {}

    def kind_of(path, hw_texts):
        p = path.lower()
        if "cpu" in p:
            return "cpu"
        if "gpu" in p:
            return "gpu"
        if any(x in p for x in ("/nvme", "/hdd", "/ssd", "/storage", "/ata")):
            return "disk"
        t = " ".join(hw_texts).lower()
        if any(x in t for x in ("ryzen", "intel core", "cpu", "processor", "xeon")):
            return "cpu"
        if any(x in t for x in ("geforce", "radeon", "nvidia", "gpu", "arc ")):
            return "gpu"
        if any(x in t for x in ("ssd", "nvme", "samsung", "wd", "seagate", "crucial", "kingston", "disk", "hdd")):
            return "disk"
        return None

    def walk(node, texts):
        texts = texts + [node.get("Text", "")]
        sid = node.get("SensorId") or ""
        is_temp = node.get("Type") == "Temperature" or "/temperature/" in sid or "°C" in (node.get("Value") or "")
        if is_temp and not node.get("Children"):
            k = kind_of(sid, texts[-4:-1])
            v = _num(node.get("Value"))
            name = (node.get("Text") or "").lower()
            if k and v is not None and 0 < v < 130:
                # prefer the package / core / composite reading over hot spots and limits
                rank = 3 if any(x in name for x in ("package", "tctl")) else \
                    2 if any(x in name for x in ("gpu core", "composite", "core (", "tdie")) else \
                    0 if any(x in name for x in ("limit", "max", "critical", "warning", "hot spot", "junction")) else 1
                if k not in found or rank > found[k][0] or (rank == found[k][0] and v > found[k][1]):
                    found[k] = (rank, v)
        for ch in node.get("Children") or []:
            walk(ch, texts)

    walk(data, [])
    return {k: v for k, (_, v) in found.items()}


class _Nvml:
    def __init__(self):
        lib = ctypes.WinDLL("nvml.dll") if sys.platform == "win32" else ctypes.CDLL("libnvidia-ml.so.1")
        if lib.nvmlInit_v2():
            raise OSError("nvmlInit failed")
        self.lib, self.h = lib, ctypes.c_void_p()
        if lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(self.h)):
            raise OSError("no NVIDIA GPU")

    def gpu_temp(self):
        t = ctypes.c_uint(0)
        return float(t.value) if self.lib.nvmlDeviceGetTemperature(self.h, 0, ctypes.byref(t)) == 0 else None


class Sampler:
    """Samples once a second on its own thread while running. latest: {"cpu": (value, unit, source)}."""

    def __init__(self, cfg, log=print):
        self.cfg, self.log = cfg, log
        self.latest = {}
        self._stop = threading.Event()
        self._thread = None
        self._pdh = self._nvml = None
        self._pdh_tried = self._nvml_tried = False
        self._fail_until = {}                   # source -> time to try again
        self.temp_sources = {}                  # source -> last time it answered (for the UI)

    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        self._stop.clear()                     # (a thread that was told to stop keeps going)
        if not self.running():
            self._thread = threading.Thread(target=self._loop, name="stats", daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.latest = self.sample()
            except Exception as e:
                self.log("PC stats sample failed:", e)
            self._stop.wait(1.0)

    def _usage(self):
        if not self._pdh_tried:
            self._pdh_tried = True
            if sys.platform == "win32":
                try:
                    self._pdh = _Pdh()
                except Exception as e:
                    self.log("performance counters unavailable:", e)
        return self._pdh.sample() if self._pdh else {}

    def _temps(self):
        out, src = {}, {}
        now = time.time()
        from . import sensors
        for name, url in (("TENET Hub sensors", sensors.URL),          # our helper (hub/sensors.py)
                          ("LibreHardwareMonitor", self.cfg.get("lhm_url") or LHM_URL)):
            if now < self._fail_until.get(name, 0):
                continue
            try:
                with urllib.request.urlopen(url, timeout=0.4) as r:
                    found = lhm_temps(json.load(r))
                self.temp_sources[name] = now
                for k, v in found.items():
                    if k not in out:
                        out[k], src[k] = v, name
            except Exception:
                self._fail_until[name] = now + 15         # not running: try again in a bit
        if "gpu" not in out:
            if not self._nvml_tried:
                self._nvml_tried = True
                try:
                    self._nvml = _Nvml()
                except Exception:
                    self._nvml = None
            if self._nvml:
                t = self._nvml.gpu_temp()
                if t is not None:
                    out["gpu"], src["gpu"] = t, "NVIDIA driver"
        return out, src

    def sample(self):
        src_of = sources(self.cfg)
        need = {SOURCES[v][1:] for v in src_of.values()}          # {(device, kind)}
        usage = self._usage() if any(k == "usage" and d != "mem" for d, k in need) else {}
        if ("mem", "usage") in need:
            usage["mem"] = mem_usage()
        temps, src = self._temps() if any(k == "temp" for _, k in need) else ({}, {})
        out = {}
        for s in SLOTS:
            _, dev, kind = SOURCES[src_of[s]]
            if kind == "usage":
                v = usage.get(dev)
                out[s] = (v, "%", "Windows" if v is not None else None)
            else:
                v = temps.get(dev)
                out[s] = (v, "°C", src.get(dev))
        return out

    def values(self):
        """The three bytes for 70 09."""
        return bytes(to_byte((self.latest.get(s) or (None,))[0]) for s in SLOTS)
