"""TENET Hub - firmware updates and screen packages for the Metakey TENET 70.

    python tenet_hub.py            # open the window (and tray icon)
    python tenet_hub.py --tray     # start hidden in the tray (used at login)
    python tenet_hub.py --headless # no window: just run the packages (for testing)
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


def make_logger():
    path = os.path.join(data_dir(), "hub.log")
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


def main():
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
