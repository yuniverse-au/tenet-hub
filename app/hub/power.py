"""
tenet_power.py - tells the Now Playing program when Windows goes to sleep, wakes up,
shuts down, or turns the monitor off/on, so it can switch the keyboard screen off and on.

Windows only, built-in ctypes (no extra packages). Runs a hidden window on its own thread,
because Windows sends these notifications as window messages.

    w = PowerWatcher(on_off=lambda: ..., on_on=lambda: ...)
    w.start()
    w.asleep   # True while the PC is suspending / shutting down / monitor off
"""
import ctypes, sys, threading, time
from ctypes import wintypes

WM_POWERBROADCAST = 0x0218
WM_QUERYENDSESSION = 0x0011
WM_ENDSESSION = 0x0016
WM_DESTROY = 0x0002
PBT_APMSUSPEND = 0x0004
PBT_APMRESUMESUSPEND = 0x0007
PBT_APMRESUMEAUTOMATIC = 0x0012
PBT_POWERSETTINGCHANGE = 0x8013

LRESULT = ctypes.c_ssize_t
WNDPROC = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


def _guid(s):
    import uuid
    u = uuid.UUID(s)
    g = GUID()
    g.Data1, g.Data2, g.Data3 = u.fields[0], u.fields[1], u.fields[2]
    for i, b in enumerate(u.bytes[8:]):
        g.Data4[i] = b
    return g


# monitor on/off/dimmed (this is also how "sleep" shows up on Modern Standby laptops)
GUID_CONSOLE_DISPLAY_STATE = _guid("6FE69556-704A-47A0-8F24-C28D936FDA47")


class POWERBROADCAST_SETTING(ctypes.Structure):
    _fields_ = [("PowerSetting", GUID), ("DataLength", wintypes.DWORD), ("Data", ctypes.c_ubyte * 1)]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HANDLE), ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HANDLE), ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR)]


class PowerWatcher:
    def __init__(self, on_off, on_on, with_display=True, log=print):
        self.on_off, self.on_on, self.with_display, self.log = on_off, on_on, with_display, log
        self.asleep = False
        self._display_off = False
        self._suspended = False

    def start(self):
        if sys.platform != "win32":
            return self
        threading.Thread(target=self._run, name="power-watcher", daemon=True).start()
        return self

    def _set(self, why):
        asleep = self._suspended or (self.with_display and self._display_off)
        if asleep == self.asleep:
            return
        self.asleep = asleep
        self.log("screen", "off" if asleep else "on", "-", why)
        try:
            (self.on_off if asleep else self.on_on)()
        except Exception as e:
            self.log("power callback failed:", e)

    def _proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_POWERBROADCAST:
            if wparam == PBT_APMSUSPEND:
                self._suspended = True
                self._set("PC going to sleep")
            elif wparam in (PBT_APMRESUMEAUTOMATIC, PBT_APMRESUMESUSPEND):
                self._suspended = False
                self._display_off = False      # a display-on notice follows; assume on
                self._set("PC woke up")
            elif wparam == PBT_POWERSETTINGCHANGE and lparam:
                s = ctypes.cast(lparam, ctypes.POINTER(POWERBROADCAST_SETTING)).contents
                if bytes(s.PowerSetting) == bytes(GUID_CONSOLE_DISPLAY_STATE):
                    state = s.Data[0]           # 0 off, 1 on, 2 dimmed
                    self._display_off = state == 0
                    self._set("monitor " + ("off" if state == 0 else "on"))
            return 1
        if msg == WM_QUERYENDSESSION:
            return 1                            # don't block shutdown
        if msg == WM_ENDSESSION:
            if wparam:
                self._suspended = True
                self._set("PC shutting down / signing out")
            return 0
        return self._user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _run(self):
        try:
            u = self._user32 = ctypes.WinDLL("user32", use_last_error=True)
            k = ctypes.WinDLL("kernel32", use_last_error=True)
            u.DefWindowProcW.restype = LRESULT
            u.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            u.CreateWindowExW.restype = wintypes.HWND
            u.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                          ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                          wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
            u.RegisterPowerSettingNotification.restype = wintypes.HANDLE
            u.RegisterPowerSettingNotification.argtypes = [wintypes.HANDLE, ctypes.POINTER(GUID), wintypes.DWORD]
            u.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
            k.GetModuleHandleW.restype = wintypes.HMODULE
            self._wndproc = WNDPROC(self._proc)          # keep a reference
            hinst = k.GetModuleHandleW(None)
            wc = WNDCLASSW()
            wc.lpfnWndProc = self._wndproc
            wc.hInstance = hinst
            wc.lpszClassName = "TenetNowPlayingPower"
            if not u.RegisterClassW(ctypes.byref(wc)):
                raise ctypes.WinError(ctypes.get_last_error())
            # a normal (never shown) top-level window: message-only windows don't get power broadcasts
            hwnd = u.CreateWindowExW(0, wc.lpszClassName, "TENET Now Playing", 0, 0, 0, 0, 0,
                                     None, None, hinst, None)
            if not hwnd:
                raise ctypes.WinError(ctypes.get_last_error())
            u.RegisterPowerSettingNotification(hwnd, ctypes.byref(GUID_CONSOLE_DISPLAY_STATE), 0)
            msg = wintypes.MSG()
            while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                u.TranslateMessage(ctypes.byref(msg))
                u.DispatchMessageW(ctypes.byref(msg))
        except Exception as e:
            self.log("power watcher unavailable (screen won't follow sleep/shutdown):", e)


def single_instance(name="TenetNowPlaying", wait=0):
    """False if another copy is already running (Windows named mutex). `wait`: seconds to give that
    copy to exit (the previous version, after an update)."""
    if sys.platform != "win32":
        return True
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateMutexW.restype = wintypes.HANDLE
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    deadline = time.monotonic() + wait
    while True:
        h = k.CreateMutexW(None, False, "Local\\" + name)
        if ctypes.get_last_error() != 183:                                # ERROR_ALREADY_EXISTS
            single_instance._h = h                                        # keep it open
            return True
        k.CloseHandle(h)                                                  # or this handle keeps it alive
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.25)
