"""Settings and data folders. Everything lives in %LOCALAPPDATA%\\TenetHub."""
import json, os, sys, threading

DEFAULT_INDEX = "https://raw.githubusercontent.com/yuuusername/tenet-hub/main/index.json"
OLD_INDEXES = ("https://raw.githubusercontent.com/yuniverse-au/tenet-hub/main/index.json",)   # before the repo moved


def data_dir(*parts):
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
    d = os.path.join(base, "TenetHub", *parts)
    os.makedirs(d, exist_ok=True)
    return d


DEFAULTS = {
    "index_url": DEFAULT_INDEX,
    "check_updates": True,          # check GitHub for new packages / updates
    "check_every_hours": 6,
    "auto_update_packages": True,   # official packages; firmware is never updated automatically
    "auto_update_community": False, # community packages too (opt in: new code from someone unvetted)
    "start_at_login": True,
    "off_when_pc_sleeps": True,     # screen off while the PC sleeps / shuts down
    "off_with_monitor": True,       # ...and while Windows has the monitor off
    "idle_off_min": 5,              # package screens: off after this long without input (0 = never)
    "contrast": 0x3D,
    "burn_in_shift": True,
    "sync_clock": True,
    "lighting": "keep",             # keyboard lights: "keep" (its own) | "solid" | "off"
    "lighting_color": "#FFFFFF",             # set the keyboard's clock (Dashboard + Clock screens) from the PC
    "clock_format": "auto",         # "auto" (follow Windows) | "12" | "24"          # nudge package screens sideways every 2 minutes
    "packages": {},                 # id -> {"enabled": bool, "mode": int, "settings": {...}}
    "seen": [],                     # package ids already shown to the user (for "New")
    "last_check": 0,
}


class Config:
    def __init__(self, path=None):
        self.path = path or os.path.join(data_dir(), "settings.json")
        self.lock = threading.RLock()
        self.data = json.loads(json.dumps(DEFAULTS))
        try:
            with open(self.path, encoding="utf-8") as f:
                self.data.update(json.load(f))
        except (OSError, ValueError):
            pass
        if self.data.get("index_url") in OLD_INDEXES:      # the repo moved: follow it
            self.data["index_url"] = DEFAULT_INDEX

    def __getitem__(self, k):
        return self.data[k]

    def get(self, k, default=None):
        return self.data.get(k, default)

    def set(self, k, v):
        with self.lock:
            self.data[k] = v
            self.save()

    def pkg(self, pid):
        """Per-package settings dict (created on first use)."""
        with self.lock:
            return self.data["packages"].setdefault(pid, {"enabled": False, "mode": None, "settings": {}})

    def save(self):
        with self.lock:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
            os.replace(tmp, self.path)


def set_start_at_login(enabled):
    """Adds/removes TENET Hub in HKCU\\...\\Run (Windows, frozen .exe only)."""
    if sys.platform != "win32":
        return False
    import winreg
    key = r"Software\Microsoft\Windows\CurrentVersion\Run"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
        if enabled:
            exe = sys.executable if getattr(sys, "frozen", False) else \
                f'"{sys.executable}" "{os.path.abspath(sys.argv[0])}"'
            if getattr(sys, "frozen", False):
                exe = f'"{exe}"'
            winreg.SetValueEx(k, "TENET Hub", 0, winreg.REG_SZ, exe + " --tray")
        else:
            try:
                winreg.DeleteValue(k, "TENET Hub")
            except FileNotFoundError:
                pass
    return True


def windows_uses_12h():
    """True if Windows' time format is 12-hour (h:mm tt), False if 24-hour. None if unknown."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(80)
        if ctypes.windll.kernel32.GetLocaleInfoEx(None, 0x1003, buf, 80):      # LOCALE_STIMEFORMAT
            return "H" not in buf.value
    except Exception:
        pass
    return None
