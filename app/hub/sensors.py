"""Temperatures without a separate program: TENET Hub's sensor helper (Windows).

Windows only lets programs read CPU (and most disk) temperatures through a signed kernel driver,
and only as administrator. TENET Hub runs as the user, so a small helper does that part:

  TenetHub.exe --sensors        the helper. Reads sensors with the bundled LibreHardwareMonitorLib
                                (which talks to the PawnIO driver) and serves them on
                                http://127.0.0.1:8086/data.json in LibreHardwareMonitor's own format,
                                so stats.py reads it the same way as LibreHardwareMonitor's web server.
  TenetHub.exe --sensors-setup  run once as administrator (one Windows prompt): installs PawnIO if it
                                isn't there, registers a scheduled task that starts the helper as
                                administrator at every login, and starts it.
  TenetHub.exe --sensors-remove the opposite (the PawnIO driver stays: other programs may use it).

The bundled files (build/lhm, see tools/fetch_lhm.py) sit next to the code as lhm/.
"""
import ctypes, json, os, subprocess, sys, tempfile, threading, time, urllib.request
from xml.sax.saxutils import escape

TASK = "TENET Hub sensors"
PORT = 8086
URL = f"http://127.0.0.1:{PORT}/data.json"
NO_WINDOW = 0x08000000                                # CREATE_NO_WINDOW


def lib_dir():
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, "lhm")
    return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                        "build", "lhm")


def available():
    """The helper can run here: Windows, with the sensor library bundled."""
    return sys.platform == "win32" and os.path.isfile(os.path.join(lib_dir(), "LibreHardwareMonitorLib.dll"))


def command(flag):
    """(program, arguments) that run this TENET Hub with `flag`, frozen .exe or from source."""
    if getattr(sys, "frozen", False):
        return sys.executable, flag
    exe = sys.executable
    w = os.path.join(os.path.dirname(exe), "pythonw.exe")
    script = os.path.abspath(sys.argv[0])
    return (w if os.path.isfile(w) else exe), f'"{script}" {flag}'


def alive(timeout=0.3):
    try:
        with urllib.request.urlopen(URL, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


# --- the helper ------------------------------------------------------------------------------------
def open_computer():
    import clr                                        # pythonnet
    clr.AddReference(os.path.join(lib_dir(), "LibreHardwareMonitorLib.dll"))
    from LibreHardwareMonitor.Hardware import Computer
    c = Computer()
    c.IsCpuEnabled = True
    c.IsGpuEnabled = True
    c.IsStorageEnabled = True
    c.Open()
    return c


def tree(computer):
    """The temperatures as LibreHardwareMonitor's data.json tree (what stats.lhm_temps reads)."""
    from LibreHardwareMonitor.Hardware import SensorType

    def node(hw):
        hw.Update()
        children = []
        for s in hw.Sensors:
            if s.SensorType == SensorType.Temperature and s.Value is not None:
                children.append({"Text": str(s.Name), "Type": "Temperature", "SensorId": str(s.Identifier),
                                 "Value": f"{float(s.Value):.1f} °C", "Children": []})
        for sub in hw.SubHardware:
            children.append(node(sub))
        return {"Text": str(hw.Name), "Children": children}

    return {"Text": "Sensor", "Children": [{"Text": "TENET Hub", "Children": [node(h) for h in computer.Hardware]}]}


def serve(log=print, every=2.0):
    """The helper: keeps the latest temperatures and answers GET /data.json on 127.0.0.1 only."""
    import http.server
    latest = {"body": b'{"Text": "Sensor", "Children": []}'}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.split("?")[0] != "/data.json":
                self.send_error(404)
                return
            body = latest["body"]
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    try:
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        log("sensor helper: already running")
        return 0
    computer = open_computer()
    threading.Thread(target=srv.serve_forever, name="sensors-http", daemon=True).start()
    log("sensor helper: running on", URL, "| admin:", is_admin())
    while True:
        try:
            latest["body"] = json.dumps(tree(computer)).encode("utf-8")
        except Exception as e:
            log("sensor helper: read failed:", e)
        time.sleep(every)


# --- setup (runs as administrator) ------------------------------------------------------------------
def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def pawnio_installed():
    import winreg
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            winreg.CloseKey(winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                           r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO",
                                           0, winreg.KEY_READ | view))
            return True
        except OSError:
            pass
    return False


def task_xml(user):
    exe, args = command("--sensors")
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>TENET Hub: reads CPU, GPU and disk temperatures for the keyboard's PC stats screen.</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled><UserId>{escape(user)}</UserId></LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{escape(user)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Hidden>true</Hidden>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec><Command>{escape(exe)}</Command><Arguments>{escape(args)}</Arguments></Exec>
  </Actions>
</Task>
"""


def _run(args, timeout=120):
    r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW)
    return r.returncode, (r.stdout + r.stderr).strip()


def setup(user, log=print):
    """As administrator: PawnIO if missing, the logon task for `user`, and start it. -> exit code."""
    if not is_admin():
        log("sensor setup: not running as administrator")
        return 5
    if not pawnio_installed():
        setup_exe = os.path.join(lib_dir(), "PawnIO_setup.exe")
        code, out = _run([setup_exe, "-install", "-silent"], timeout=300)
        log("sensor setup: PawnIO install ->", code, out)
        if not pawnio_installed():
            return 3
    path = os.path.join(tempfile.mkdtemp(prefix="tenethub-"), "task.xml")
    with open(path, "w", encoding="utf-16") as f:
        f.write(task_xml(user))
    code, out = _run(["schtasks", "/Create", "/TN", TASK, "/XML", path, "/F"])
    log("sensor setup: task ->", code, out)
    if code:
        return 4
    code, out = _run(["schtasks", "/Run", "/TN", TASK])
    log("sensor setup: start ->", code, out)
    return 0


def remove(log=print):
    _run(["schtasks", "/End", "/TN", TASK])
    code, out = _run(["schtasks", "/Delete", "/TN", TASK, "/F"])
    log("sensor helper removed ->", code, out)
    return 0 if code == 0 or "cannot find" in out.lower() else 4


# --- asking Windows for administrator rights (from the Hub, which runs as the user) -----------------
class _ShellExecuteInfo(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint32), ("fMask", ctypes.c_ulong), ("hwnd", ctypes.c_void_p),
                ("lpVerb", ctypes.c_wchar_p), ("lpFile", ctypes.c_wchar_p), ("lpParameters", ctypes.c_wchar_p),
                ("lpDirectory", ctypes.c_wchar_p), ("nShow", ctypes.c_int), ("hInstApp", ctypes.c_void_p),
                ("lpIDList", ctypes.c_void_p), ("lpClass", ctypes.c_wchar_p), ("hkeyClass", ctypes.c_void_p),
                ("dwHotKey", ctypes.c_uint32), ("hIconOrMonitor", ctypes.c_void_p), ("hProcess", ctypes.c_void_p)]


def run_as_admin(flag, extra=""):
    """Runs this TENET Hub with `flag` as administrator (Windows asks the user) and waits.
    -> its exit code, or None if the user said no."""
    exe, args = command(flag)
    info = _ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x00000040 | 0x00000100              # SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC
    info.lpVerb, info.lpFile = "runas", exe
    info.lpParameters = f"{args} {extra}".strip()
    info.nShow = 0                                    # SW_HIDE
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)):
        return None                                   # declined (ERROR_CANCELLED) or couldn't start
    k32 = ctypes.windll.kernel32
    k32.WaitForSingleObject(ctypes.c_void_p(info.hProcess), 0xFFFFFFFF)
    code = ctypes.c_uint32()
    k32.GetExitCodeProcess(ctypes.c_void_p(info.hProcess), ctypes.byref(code))
    k32.CloseHandle(ctypes.c_void_p(info.hProcess))
    return code.value


def current_user():
    dom, user = os.environ.get("USERDOMAIN", ""), os.environ.get("USERNAME", "")
    return f"{dom}\\{user}" if dom else user
