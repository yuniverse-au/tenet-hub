"""Opens the real window against the fake keyboard + a local catalogue and screenshots each tab.
    xvfb-run -s "-screen 0 1000x700x24" python tests/ui_smoke.py official.bin out_dir"""
import functools, http.server, os, shutil, sys, tempfile, threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="tenethub-ui-")
os.environ["LOCALAPPDATA"] = os.path.join(TMP, "appdata")
sys.path.insert(0, os.path.join(ROOT, "app"))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import fake_hid                                            # noqa: E402
sys.modules["hid"] = fake_hid
from PIL import ImageGrab                                  # noqa: E402
from hub.config import Config                              # noqa: E402
from hub.packages import Registry                          # noqa: E402
from hub.host import Host                                  # noqa: E402
from hub.ui import App                                     # noqa: E402

OUT = sys.argv[2]
os.makedirs(OUT, exist_ok=True)
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(
    type("Q", (http.server.SimpleHTTPRequestHandler,), {"log_message": lambda *a: None}), directory=ROOT))
threading.Thread(target=srv.serve_forever, daemon=True).start()
cfg = Config()
cfg.set("index_url", f"http://127.0.0.1:{srv.server_port}/index.json")
cfg.set("start_at_login", False)
reg = Registry(cfg, print)
reg.bundled(os.path.join(ROOT, "packages"))
if len(sys.argv) > 3:                                   # a local (private) package to show
    shutil.copytree(sys.argv[3], os.path.join(reg.root, os.path.basename(sys.argv[3])))
    reg.scan()
fake_hid.BOARD.mode = 5
host = Host(cfg, reg, print)
host.start()
app = App(cfg, reg, host, print, os.path.join(TMP, "log.txt"))
app.check_updates(quiet=True)


def shot(name):
    app.root.update()
    x, y = app.root.winfo_rootx(), app.root.winfo_rooty()
    w, h = app.root.winfo_width(), app.root.winfo_height()
    ImageGrab.grab(bbox=(x, y, x + w, y + h), xdisplay=os.environ["DISPLAY"]).save(os.path.join(OUT, name))


def step(i=0):
    tabs = app.nb.tabs()
    if i == 0:
        app.refresh_packages()
        app.pkg_tv.selection_set("nowplaying")
    if i < len(tabs):
        app.nb.select(tabs[i])
        wait = 600
        if app.nb.tab(tabs[i], "text") == "GIF screen" and app.gif_sources:
            app.gif_src.set(list(app.gif_sources)[0])
            app._gif_source_changed()
            wait = 6000
        app.root.after(wait, lambda: (shot(f"tab{i}.png"), app.root.after(100, step, i + 1)))
    elif i == len(tabs):
        app.nb.select(tabs[0])
        app.pkg_tv.selection_set("nowplaying")
        app.pkg_settings()
        app.root.after(700, lambda: (shot_dialog(), app.root.after(100, step, i + 1)))
    else:
        print("running:", sorted(host.running), "problems:", host.problems, "status:", host.status)
        app.quit()


def shot_dialog():
    top = [w for w in app.root.winfo_children() if w.winfo_class() == "Toplevel"][0]
    top.update()
    x, y, w, h = top.winfo_rootx(), top.winfo_rooty(), top.winfo_width(), top.winfo_height()
    ImageGrab.grab(bbox=(x, y, x + w, y + h), xdisplay=os.environ["DISPLAY"]).save(os.path.join(OUT, "settings_dialog.png"))
    top.destroy()


app.root.after(2500, step)
app.run()
srv.shutdown()
