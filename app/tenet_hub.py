"""TENET Hub - firmware updates and screen packages for the Metakey TENET 70.

    python tenet_hub.py            # open the window (and tray icon)
    python tenet_hub.py --tray     # start hidden in the tray (used at login)
    python tenet_hub.py --headless # no window: just run the packages (for testing)
    python tenet_hub.py --sensors  # the temperature helper (started as administrator at login)
    python tenet_hub.py --sensors-setup USER / --sensors-remove   (as administrator; see hub/sensors.py)
"""
import datetime, os, sys, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from hub import APP_NAME, VERSION, power                 # noqa: E402
from hub.config import Config, data_dir, set_start_at_login   # noqa: E402
from hub.packages import Registry                         # noqa: E402
from hub.host import Host                                 # noqa: E402
from hub import _bundle                                   # noqa: E402,F401  (stdlib for packages)

_log_lock = threading.Lock()


def make_logger(name="hub.log"):
    path = os.path.join(data_dir(), name)
    if os.path.exists(path) and os.path.getsize(path) > 1_000_000:
        os.replace(path, path + ".old")
    f = open(path, "a", encoding="utf-8", buffering=1)
    console = sys.stdout

    def log(*a):
        line = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} " + " ".join(str(x) for x in a)
        with _log_lock:
            f.write(line + "\n")
            if console:
                try:
                    console.write(line + "\n")
                except Exception:
                    pass
    return log, path


def bundled_packages_dir():
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(HERE)
    return os.path.join(base, "packages")


def selftest():
    """For CI: run inside the built .exe and load every bundled package the way the app does."""
    import tempfile
    os.environ["LOCALAPPDATA"] = tempfile.mkdtemp()
    from hub import api, render, gif, ui  # noqa: F401
    cfg = Config()
    reg = Registry(cfg, print)
    reg.bundled(bundled_packages_dir())
    assert reg.installed, "no bundled packages found"
    for m in reg.installed.values():
        if m["type"] != "animation":
            cls = reg.load_class(m)
            assert issubclass(cls, (api.ScreenPackage, api.BackgroundPackage)), m["id"]
    extra = ""
    if sys.platform == "win32":                       # PC stats: Windows performance counters work
        from hub import stats
        pdh = stats._Pdh()
        time.sleep(1.1)
        s = pdh.sample()
        assert s.get("cpu") is not None and s.get("disk") is not None, s
        extra = f" | stats {s}"
        from hub import sensors                       # the bundled sensor library loads (no admin here)
        assert sensors.available(), "sensor library not bundled"
        assert os.path.isfile(os.path.join(sensors.lib_dir(), "PawnIO_setup.exe")), "PawnIO setup not bundled"
        c = sensors.open_computer()
        t = sensors.tree(c)
        hw = [h["Text"] for h in t["Children"][0]["Children"]]
        c.Close()
        assert hw, "the sensor library found no hardware"
        extra += f" | sensors {hw}"
    open(os.path.join(os.getcwd(), "selftest.ok"), "w").write(", ".join(sorted(reg.installed)) + extra)


def sensors_main():
    """The temperature helper and its setup (hub/sensors.py). Not the app: no single-instance lock."""
    from hub import sensors
    log, _ = make_logger("sensors.log")
    if "--sensors-setup" in sys.argv:
        i = sys.argv.index("--sensors-setup")
        user = sys.argv[i + 1] if len(sys.argv) > i + 1 else sensors.current_user()
        return sensors.setup(user, log)
    if "--sensors-remove" in sys.argv:
        return sensors.remove(log)
    if not sensors.available():
        log("sensor helper: the sensor library isn't bundled")
        return 2
    return sensors.serve(log)


def main():
    if "--selftest" in sys.argv:
        return selftest()
    if any(a.startswith("--sensors") for a in sys.argv):
        sys.exit(sensors_main() or 0)
    log, log_path = make_logger()
    if not power.single_instance("TenetHub"):
        log("already running")
        return
    log(f"--- {APP_NAME} {VERSION} starting")
    cfg = Config()
    reg = Registry(cfg, log)
    reg.bundled(bundled_packages_dir())
    if cfg.get("first_run_done") is None:
        try:
            set_start_at_login(cfg["start_at_login"])
        except Exception as e:
            log("start-at-login not set:", e)
        cfg.set("first_run_done", True)

    if "--headless" in sys.argv:
        host = Host(cfg, reg, log)
        host.start()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            host.stop()
        return

    from hub.ui import App
    app = None
    host = Host(cfg, reg, log, notify=lambda t, m: app and app.notify(t, m))
    host.start()
    app = App(cfg, reg, host, log, log_path, hidden="--tray" in sys.argv)
    app.run()


if __name__ == "__main__":
    main()
