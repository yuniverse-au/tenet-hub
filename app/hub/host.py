"""The background engine: owns the keyboard connection and runs packages.

Each tick it reads which screen the keyboard shows (REVIVER v6+). If an enabled screen package owns
that screen it gets frame() calls at its fps and its images are drawn. Background packages get
events. The host also does the screen-off rules (PC asleep / monitor off, idle, nothing to show),
nudges package screens sideways against burn-in, and reconnects when the keyboard is replugged.
"""
import ctypes, os, sys, threading, time, traceback

from PIL import Image

from . import api, power
from .config import data_dir
from .device import Keyboard, enumerate_keyboards
from .screen import Screen

SHIFT_EVERY = 120
SHIFT_PATTERN = [0, 1, 2, 1, 0, -1, -2, -1]
FULL_REFRESH = 10.0
MAX_ERRORS = 5


def idle_seconds():
    if sys.platform != "win32":
        return 0.0
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
    li = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li)):
        return 0.0
    return ((ctypes.windll.kernel32.GetTickCount() - li.dwTime) & 0xFFFFFFFF) / 1000.0


class Running:
    def __init__(self, manifest, obj, ctx):
        self.m, self.obj, self.ctx = manifest, obj, ctx
        self.errors = 0
        self.next_frame = 0.0
        self.none_since = None
        self.shown = False


class Host:
    def __init__(self, cfg, registry, log=print, notify=lambda title, msg: None):
        self.cfg, self.reg, self.log, self.notify = cfg, registry, log, notify
        self.kb = self.fw = self.screen = None
        self.status = "Starting..."
        self.mode = None
        self.running = {}                 # id -> Running
        self.problems = {}                # id -> why it isn't running
        self.paused = threading.Event()   # set while flashing: the host lets go of the keyboard
        self.stopped = threading.Event()
        self.lock = threading.RLock()
        self.asleep = False
        self.watcher = power.PowerWatcher(on_off=self._sleep, on_on=self._wake,
                                          with_display=cfg["off_with_monitor"], log=log)
        self._last_full = 0.0
        self._requests = []

    # --- lifecycle ---------------------------------------------------------------------
    def start(self):
        if self.cfg["off_when_pc_sleeps"]:
            self.watcher.start()
        threading.Thread(target=self._loop, name="host", daemon=True).start()

    def stop(self):
        self.stopped.set()
        with self.lock:
            for pid in list(self.running):
                self._stop_pkg(pid)

    def release_keyboard(self):
        """Pause and disconnect (before flashing). resume() reconnects."""
        self.paused.set()
        with self.lock:
            self._disconnect("Paused for a firmware update")

    def resume(self):
        self.paused.clear()

    def reload(self):
        """Settings or installed packages changed: restart what's affected on the next tick."""
        with self.lock:
            for pid in list(self.running):
                self._stop_pkg(pid)
            self.problems.clear()

    # --- power ----------------------------------------------------------------------------
    def _sleep(self):
        self.asleep = True
        with self.lock:
            if self.screen:
                self.screen.hold_off = True
                try:
                    self.screen.power(False)
                except Exception:
                    pass
        self._event("sleep")

    def _wake(self):
        self.asleep = False
        with self.lock:
            if self.screen:
                self.screen.hold_off = False
        self._event("wake")

    # --- packages ---------------------------------------------------------------------------
    def _mode_for(self, m):
        mode = self.cfg.pkg(m["id"]).get("mode") or m.get("default_mode")
        return mode if mode in api.PACKAGE_MODES else None

    def _request(self, kind, value):
        self._requests.append((kind, value))
        return True

    def _start_pkg(self, m):
        pid = m["id"]
        need = int(m.get("requires_firmware") or 0)
        if need and self.fw is None:
            return                                   # waiting for the keyboard
        if need and not self.fw.at_least(need):
            self.problems[pid] = f"Needs REVIVER v{need} or newer"
            return
        if m["type"] == "screen" and self._mode_for(m) is None:
            self.problems[pid] = "No screen chosen"
            return
        try:
            cls = self.reg.load_class(m)
            obj = cls()
            settings = dict(m.defaults())
            settings.update(self.cfg.pkg(pid).get("settings") or {})
            ctx = api.Context(pid, settings, data_dir("package-data", pid),
                              lambda *a, pid=pid: self.log(f"[{pid}]", *a), self.fw, self._request)
            ctx.screen_mode = self._mode_for(m) if m["type"] == "screen" else None
            obj.start(ctx)
            self.running[pid] = Running(m, obj, ctx)
            self.problems.pop(pid, None)
            self.log(f"started {m['name']} {m['version']}")
        except Exception as e:
            self.problems[pid] = f"Failed to start: {e}"
            self.log(f"{pid} failed to start:\n{traceback.format_exc()}")

    def _stop_pkg(self, pid):
        r = self.running.pop(pid, None)
        if r:
            try:
                r.obj.stop()
            except Exception:
                pass

    def _sync_packages(self):
        wanted = {pid: m for pid, m in self.reg.installed.items()
                  if self.cfg.pkg(pid).get("enabled") and m["type"] != "animation"}
        for pid in list(self.running):
            if pid not in wanted or self.running[pid].m is not wanted[pid]:
                self._stop_pkg(pid)
        claimed = {}
        for pid, m in wanted.items():
            if m["type"] == "screen":
                mode = self._mode_for(m)
                if mode in claimed and claimed[mode] != pid:
                    self.problems[pid] = f"{api.PACKAGE_MODES[mode]} is used by {claimed[mode]}"
                    self._stop_pkg(pid)
                    continue
                claimed[mode] = pid
            if pid not in self.running and pid not in self.problems:
                self._start_pkg(m)
        for pid in list(self.problems):
            if pid not in wanted:
                self.problems.pop(pid)

    def _fail(self, r, e):
        r.errors += 1
        self.log(f"{r.m['id']} error:\n{traceback.format_exc()}")
        if r.errors >= MAX_ERRORS:
            self._stop_pkg(r.m["id"])
            self.problems[r.m["id"]] = f"Stopped after repeated errors: {e}"
            self.notify(r.m["name"], "Stopped after repeated errors (see the log).")

    def _event(self, name, data=None):
        for r in list(self.running.values()):
            if r.m["type"] == "background":
                try:
                    r.obj.on_event(name, data)
                except Exception as e:
                    self._fail(r, e)

    # --- keyboard -----------------------------------------------------------------------
    def _disconnect(self, status):
        if self.kb:
            self.kb.close()
            self._event("disconnected")
        self.kb = self.screen = None
        self.fw = None
        self.status = status

    def _connect(self):
        kind, path = enumerate_keyboards()
        if kind == "iap":
            self.status = "Keyboard is in update mode (install firmware from the Firmware tab)"
            return False
        if kind is None:
            self.status = "Keyboard not found (plugged in? Hexcore Link closed?)"
            return False
        kb = Keyboard(path)
        fw = kb.probe()
        self.kb, self.fw = kb, fw
        self.screen = Screen(kb, fw, self.cfg["contrast"]) if fw.at_least(1) else None
        if self.screen:
            self.screen.hold_off = self.asleep
            if self.asleep:
                self.screen.power(False)
        self.status = f"Connected - {fw}"
        self.log("keyboard connected:", fw)
        if str(fw) != getattr(self, "_last_fw", None):    # different firmware: re-check packages
            for pid in list(self.running):
                self._stop_pkg(pid)
            self._last_fw = str(fw)
        for r in self.running.values():
            r.ctx.firmware = fw
        self.problems.clear()                      # firmware may have changed: re-check
        self._event("connected", fw)
        return True

    # --- main loop ------------------------------------------------------------------------
    def _loop(self):
        while not self.stopped.is_set():
            if self.paused.is_set():
                time.sleep(0.3)
                continue
            try:
                delay = self._tick()
            except Exception as e:
                self.log("keyboard connection lost:", e)
                with self.lock:
                    self._disconnect("Reconnecting...")
                delay = 2.0
            time.sleep(delay)

    def _tick(self):
        with self.lock:
            if self.kb is None and not self._connect():
                self._sync_packages()
                return 2.0
            self._sync_packages()
            while self._requests:
                kind, value = self._requests.pop(0)
                if kind == "mode" and self.fw.at_least(6) and value in range(1, 8):
                    self.kb.set_mode(value)
            if self.asleep or self.screen is None:
                return 0.5
            mode = self.kb.mode() if self.fw.at_least(6) else None
            if mode is None and self.fw.at_least(6):
                raise IOError("no reply")
            if mode != self.mode:
                self.mode = mode
                self.screen.forget()
                self._event("mode", mode)
            active = None
            for r in self.running.values():
                if r.m["type"] == "screen":
                    show = self._mode_for(r.m) == mode
                    if show != r.shown:
                        r.shown = show
                        try:
                            r.obj.visible(show)
                        except Exception as e:
                            self._fail(r, e)
                    if show:
                        active = r
            if active is None:
                self.screen.power(True)            # the keyboard's own screens need it on
                return 0.25
            return self._draw(active)

    def _draw(self, r):
        now = time.time()
        if now < r.next_frame:
            return min(0.25, r.next_frame - now)
        fps = max(0.2, min(30.0, float(getattr(r.obj, "fps", 2) or 2)))
        r.next_frame = now + 1.0 / fps
        try:
            img = r.obj.frame(now)
        except Exception as e:
            self._fail(r, e)
            return 0.25
        idle_min = self.cfg["idle_off_min"]
        off = bool(idle_min and idle_seconds() > idle_min * 60)
        if img is None:
            r.none_since = r.none_since or now
            limit = getattr(r.obj, "nothing_off_sec", 0)
            off = off or bool(limit and now - r.none_since > limit)
            img = Image.new("L", (api.WIDTH, api.HEIGHT), 0)
        else:
            r.none_since = None
        if off:
            self.screen.power(False)
            return 0.5
        if self.screen.power(True):
            self._last_full = 0
        if self.cfg["burn_in_shift"]:
            dx = SHIFT_PATTERN[int(now // SHIFT_EVERY) % len(SHIFT_PATTERN)]
            if dx:
                shifted = Image.new("L", img.size, 0)
                shifted.paste(img, (dx, 0))
                img = shifted
        full = now - self._last_full >= FULL_REFRESH
        if not self.screen.show(img, getattr(r.obj, "crisp_rows", 0), full):
            raise IOError("keyboard stopped answering")
        if full:
            self._last_full = now
        return max(0.01, r.next_frame - time.time())
