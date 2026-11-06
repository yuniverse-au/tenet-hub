"""End-to-end tests against a fake keyboard and a local copy of the catalogue.

    python tests/test_hub.py /path/to/official_3.07.bin /path/to/v14.bin
"""
import functools, hashlib, http.server, io, json, os, shutil, struct, sys, tempfile, threading, time, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="tenethub-test-")
os.environ["LOCALAPPDATA"] = os.path.join(TMP, "appdata")
sys.path.insert(0, os.path.join(ROOT, "app"))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import fake_hid                                            # noqa: E402
sys.modules["hid"] = fake_hid

from PIL import Image                                      # noqa: E402
from hub import firmware, flasher, screen                  # noqa: E402
from hub.config import Config, data_dir                    # noqa: E402
from hub.packages import Registry                          # noqa: E402
from hub.host import Host                                  # noqa: E402

OFFICIAL, PATCHED = sys.argv[1], sys.argv[2]
ok = True


def check(name, cond):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name)


# --- a local "GitHub": a copy of the repo served over HTTP --------------------------------------
REPO = os.path.join(TMP, "repo")
shutil.copytree(ROOT, REPO, ignore=shutil.ignore_patterns("tests", "__pycache__", ".git"))
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(
    type("Q", (http.server.SimpleHTTPRequestHandler,), {"log_message": lambda *a: None}), directory=REPO))
threading.Thread(target=srv.serve_forever, daemon=True).start()
INDEX = f"http://127.0.0.1:{srv.server_port}/index.json"
official_bytes = open(OFFICIAL, "rb").read()
shutil.copy(OFFICIAL, os.path.join(REPO, "official.bin"))
firmware.OFFICIAL_307 = dict(firmware.OFFICIAL_307, url=f"http://127.0.0.1:{srv.server_port}/official.bin")

logs = []
log = lambda *a: logs.append(" ".join(map(str, a)))
cfg = Config()
cfg.set("index_url", INDEX)
reg = Registry(cfg, log)

# --- catalogue -----------------------------------------------------------------------------------
new, upd = reg.refresh()
check(f"catalogue fetched: new={new} updates={upd} firmware={[f['id'] for f in reg.firmware_releases()]}",
      new == ["nowplaying"] and upd == [] and reg.firmware_releases()[0]["patch_version"] == 25)
m = reg.install("nowplaying")
check(f"installed {m['id']} {m['version']}; no longer 'new'", "nowplaying" in reg.installed and reg.new_ids() == [])

# an update appears in the catalogue
pj = os.path.join(REPO, "packages", "nowplaying", "package.json")
d = json.load(open(pj))
d["version"] = "99.0.0"
d["changelog"] = "test update"
json.dump(d, open(pj, "w"))
os.system(f"cd {REPO} && {sys.executable} tools/build_index.py >/dev/null")
new, upd = reg.refresh()
check(f"update detected: {upd}", upd == ["nowplaying"])
reg.install("nowplaying")
check(f"updated to {reg.installed['nowplaying']['version']}", reg.installed["nowplaying"]["version"] == "99.0.0")

# a brand-new package appears
os.makedirs(os.path.join(REPO, "packages", "hello"))
json.dump({"id": "hello", "name": "Hello", "version": "0.1.0", "type": "screen", "default_mode": 5,
           "requires_firmware": 1, "description": "test"}, open(os.path.join(REPO, "packages", "hello", "package.json"), "w"))
open(os.path.join(REPO, "packages", "hello", "main.py"), "w").write(
    "from hub import api\nfrom PIL import Image, ImageDraw\n"
    "class Package(api.ScreenPackage):\n    fps = 5\n"
    "    def frame(self, now):\n        img = Image.new('L', (256, 32), 0)\n"
    "        ImageDraw.Draw(img).rectangle((0, 0, 63, 31), fill=255)\n        return img\n")
os.system(f"cd {REPO} && {sys.executable} tools/build_index.py >/dev/null")
new, upd = reg.refresh()
check(f"new package recognised automatically: {new}", new == ["hello"])

# tampered download is refused
idx = json.load(open(os.path.join(REPO, "index.json")))
z = os.path.join(REPO, idx["packages"][0]["url"])
good = open(z, "rb").read()
open(z, "ab").write(b"x")
try:
    reg.install(idx["packages"][0]["id"])
    check("tampered package refused", False)
except ValueError as e:
    check(f"tampered package refused ({e})", "checksum" in str(e))
open(z, "wb").write(good)

# --- community packages: someone else's repo, added by link ----------------------------------------
from hub import packages as pk
check("repo link -> index.json URL",
      pk.source_url("github.com/alice/tenet-pkgs") == "https://raw.githubusercontent.com/alice/tenet-pkgs/HEAD/index.json"
      and pk.source_url("https://github.com/alice/tenet-pkgs/tree/dev/sub/") ==
      "https://raw.githubusercontent.com/alice/tenet-pkgs/dev/sub/index.json"
      and pk.source_url("https://example.com/x/index.json") == "https://example.com/x/index.json"
      and pk.source_label("https://raw.githubusercontent.com/alice/tenet-pkgs/HEAD/index.json") == "alice/tenet-pkgs")


def community_repo(name, pkgs):
    """pkgs: {id: version}. A community repo inside the served folder, built with --community."""
    root = os.path.join(REPO, name)
    for pid, ver in pkgs.items():
        d = os.path.join(root, "packages", pid)
        os.makedirs(d, exist_ok=True)
        json.dump({"id": pid, "name": pid.title(), "version": ver, "type": "screen", "author": name,
                   "requires_firmware": 1, "description": "community test"}, open(os.path.join(d, "package.json"), "w"))
        open(os.path.join(d, "main.py"), "w").write(
            "from hub import api\nclass Package(api.ScreenPackage):\n    def frame(self, now):\n        return None\n")
    os.system(f'cd {REPO} && {sys.executable} tools/build_index.py --community --root "{root}" --name "{name}" >/dev/null')
    return f"http://127.0.0.1:{srv.server_port}/{name}/index.json"

alice = community_repo("alice", {"alice-clock": "1.0.0", "nowplaying": "50.0.0"})
cidx = json.load(open(os.path.join(REPO, "alice", "index.json")))
check("community catalogue has no firmware / hub entries", "firmware" not in cidx and "hub" not in cidx
      and cidx["name"] == "alice")
fw_before = [f["id"] for f in reg.firmware_releases()]
url, _ = reg.add_source(alice)
check(f"source added: {pk.source_label(url)}", url == alice and alice in cfg["community_sources"])
av = reg.available()
check("its package is listed, tagged with the source", av.get("alice-clock", {}).get("_source") == alice)
check("it can't take an official package's id", av["nowplaying"].get("_source") is None
      and "nowplaying" not in reg.from_source(alice))
check("community sources can't offer firmware", [f["id"] for f in reg.firmware_releases()] == fw_before)
m = reg.install("alice-clock")
check("installed from the community source; the source is remembered",
      m["version"] == "1.0.0" and reg.source_of("alice-clock") == alice and reg.is_community("alice-clock"))
community_repo("alice", {"alice-clock": "1.1.0"})
reg.refresh()
check(f"community update found but not automatic: updatable={reg.updatable()} auto={reg.auto_updatable()}",
      "alice-clock" in reg.updatable() and "alice-clock" not in reg.auto_updatable())
check("...unless the user opted in to automatic community updates",
      "alice-clock" in reg.auto_updatable(community=True) and reg.auto_updatable(official=False, community=False) == [])
mallory = community_repo("mallory", {"alice-clock": "9.9.9"})
reg.add_source(mallory)
check("another source can't update or replace it",
      reg.available()["alice-clock"]["_source"] == alice and reg.available()["alice-clock"]["version"] == "1.1.0")
reg.install("alice-clock")
check("the user's update installs from its own source", reg.installed["alice-clock"]["version"] == "1.1.0")
reg.remove_source(alice)
check("removing a source keeps its packages, without updates",
      "alice-clock" in reg.installed and "alice-clock" not in reg.available() and alice not in reg.sources())
try:
    reg.install("alice-clock")                         # now only mallory offers that id
    check("a package from another source isn't installed over it", False)
except ValueError as e:
    check(f"a package from another source isn't installed over it ({e})", ("somewhere else" in str(e) or "catalogue" in str(e)) and reg.installed["alice-clock"]["version"] == "1.1.0")
reg.remove("alice-clock"); reg.remove_source(mallory)
check("removed: source forgotten", reg.source_of("alice-clock") is None and not reg.sources())

# a zip that tries to write outside its folder is refused
evil = io.BytesIO()
with zipfile.ZipFile(evil, "w") as zf:
    zf.writestr("package.json", json.dumps({"id": "evil", "type": "screen", "version": "1"}))
    zf.writestr("../../escape.txt", "x")
open(os.path.join(REPO, "catalog", "evil.zip"), "wb").write(evil.getvalue())
idx["packages"].append({"id": "evil", "name": "evil", "version": "1", "type": "screen",
                        "url": "catalog/evil.zip", "sha256": hashlib.sha256(evil.getvalue()).hexdigest()})
json.dump(idx, open(os.path.join(REPO, "index.json"), "w"))
reg.refresh()
try:
    reg.install("evil")
    check("path-escaping zip refused", False)
except ValueError as e:
    check(f"path-escaping zip refused ({e})", not os.path.exists(os.path.join(TMP, "appdata", "escape.txt")))

# --- firmware: download official, patch, verify, flash -------------------------------------------
rel = reg.firmware_releases()[0]
rel = dict(rel, base=dict(rel["base"], url=firmware.OFFICIAL_307["url"]))   # local copy instead of Hexcore
img = firmware.build(rel, INDEX)
check(f"v25 rebuilt from official + patch, sha {firmware.sha256(img)[:16]}", img == open(PATCHED, "rb").read())
bad = dict(rel, patch=dict(rel["patch"], sha256="00" * 32))
try:
    firmware.build(bad, INDEX)
    check("tampered patch refused", False)
except firmware.FirmwareError as e:
    check(f"tampered patch refused ({e})", True)

B = fake_hid.BOARD
B.patch, B.state = 0, "app"            # official firmware on the keyboard
steps = []
flasher.flash(img, {rel["result_sha256"]}, lambda f, msg: steps.append(msg))
check(f"flashed: {len(B.flash)} bytes match v25, back in normal mode, no pipelined packets",
      bytes(B.flash[:len(img)]) == img and B.state == "app" and B.violations == 0)
try:
    flasher.flash(img + b"x", {rel["result_sha256"]})
    check("unknown image refused", False)
except flasher.FlashError:
    check("unknown image refused before touching the keyboard", True)

# --- host: packages drawing on the keyboard -----------------------------------------------------
B.patch, B.state, B.mode, B.cmds, B.oled_log = 20, "app", 5, [], []
cfg.pkg("hello")["enabled"] = True
cfg.pkg("nowplaying")["enabled"] = False
cfg.data["burn_in_shift"] = False
cfg.save()
reg.install("hello")
host = Host(cfg, reg, log)
host._tick()
check(f"connected: {host.fw}; running {sorted(host.running)}; problems {host.problems}",
      str(host.fw) == "REVIVER v20" and sorted(host.running) == ["hello"])
for _ in range(10):
    host._tick()
ref = Image.new("L", (256, 32), 0)
__import__("PIL.ImageDraw", fromlist=["Draw"]).Draw(ref).rectangle((0, 0, 63, 31), fill=255)
check("screen 5: 'hello' package drawn on the keyboard", bytes(B.screen) == screen.to_4bpp(ref))
n = len(B.cmds)
for _ in range(5):
    host._tick()
check(f"unchanged frames send nothing ({len(B.cmds) - n} packets, all mode reads)",
      all(c in ("7012", "7007") for c in B.cmds[n:]))
cfg.pkg("nowplaying")["enabled"] = True
cfg.save(); host._tick()
check(f"two screen packages on screen 5: second one waits ({host.problems})",
      any("used by" in v for v in host.problems.values()))
cfg.pkg("hello")["enabled"] = False
cfg.save(); host.reload()
B.mode = 5
for _ in range(15):
    host._tick()
    time.sleep(0.05)
np_img = host.running["nowplaying"].obj.frame(time.time())
crisp = 16
want = screen.to_4bpp(np_img)
check("mode 5: Now Playing drawn ('Nothing playing' here: no media on Linux)",
      bytes(B.screen)[:(32 - crisp) * 128] == want[:(32 - crisp) * 128] and "700b" in B.cmds)
B.mode = 6
host._tick()
n = len(B.cmds)
host._tick()
check("mode 6 (firmware's dither): host leaves the screen alone", all(c in ("7012", "7007") for c in B.cmds[n:]))
host._sleep()
n = len(B.cmds)
for _ in range(3):
    host._tick()
check(f"PC asleep: screen off ({B.oled_log[-1]}) and nothing drawn", B.oled_log[-1] == "ae" and len(B.cmds) == n)
host._wake()
B.mode = 5
host._tick(); host._tick()
check(f"woke: screen back on ({B.oled_log[-2:]}) and redrawn", "af" in B.oled_log[-3:])
B.state = "gone"
try:
    host._tick()
except Exception:
    host._disconnect("lost")
check("unplugged: host notices", host.kb is None)
B.state = "app"
host._tick()
check("replugged: reconnects", host.kb is not None and str(host.fw) == "REVIVER v20")
B.patch = 4
host._disconnect("x"); host._tick()
check(f"older patch on the keyboard: problems {host.problems}",
      "Needs REVIVER v6" in host.problems.get("nowplaying", "") and "hello" not in host.problems)

# --- PC stats sources (pure parts; the Windows counters run in the .exe self-test on CI) ------------
from hub import stats as pcstats
lhm = {"Text": "Sensor", "Children": [{"Text": "DESKTOP", "Children": [
    {"Text": "AMD Ryzen 7 5800X", "Children": [{"Text": "Temperatures", "Children": [
        {"Text": "Core (Tctl/Tdie)", "Value": "61.3 °C", "Type": "Temperature", "SensorId": "/amdcpu/0/temperature/2", "Children": []},
        {"Text": "CCD1 (Tdie)", "Value": "70.0 °C", "Type": "Temperature", "SensorId": "/amdcpu/0/temperature/3", "Children": []}]}]},
    {"Text": "NVIDIA GeForce RTX 3080", "Children": [{"Text": "Temperatures", "Children": [
        {"Text": "GPU Core", "Value": "54,0 °C", "Type": "Temperature", "SensorId": "/gpu-nvidia/0/temperature/0", "Children": []},
        {"Text": "GPU Hot Spot", "Value": "66.0 °C", "Type": "Temperature", "SensorId": "/gpu-nvidia/0/temperature/2", "Children": []}]}]},
    {"Text": "Samsung SSD 980 PRO 1TB", "Children": [{"Text": "Temperatures", "Children": [
        {"Text": "Temperature", "Value": "38.0 °C", "Type": "Temperature", "SensorId": "/nvme/0/temperature/0", "Children": []},
        {"Text": "Warning Temperature", "Value": "82.0 °C", "Type": "Temperature", "SensorId": "/nvme/0/temperature/5", "Children": []}]}]}]}]}
t = pcstats.lhm_temps(lhm)
check(f"LibreHardwareMonitor temps: {t}", t == {"cpu": 61.3, "gpu": 54.0, "disk": 38.0})
check("units bits: CPU and disk usage -> 0b101", pcstats.units_bits({"cpu": "usage", "gpu": "temp", "disk": "usage"}) == 0b101)
cfg.data.pop("stats_sources", None); cfg.data["stats_kinds"] = {"cpu": "temp", "gpu": "usage"}
check("old stats_kinds settings still read", pcstats.sources(cfg) == {"cpu": "cpu_temp", "gpu": "gpu_usage", "disk": "disk_usage"})
cfg.data["stats_sources"] = {"cpu": "mem_usage", "gpu": "cpu_temp", "disk": "bogus"}
check("stats_sources: memory in the left slot, unknown ids fall back",
      pcstats.sources(cfg) == {"cpu": "mem_usage", "gpu": "cpu_temp", "disk": "disk_usage"}
      and pcstats.units_bits(pcstats.kinds(cfg)) == 0b101)
check("labels follow the sources (right box fits DISK)",
      pcstats.labels(cfg) == {"cpu": "RAM", "gpu": "CPU", "disk": "DISK"})
cfg.data["stats_sources"] = {"cpu": "disk_temp", "gpu": "gpu_usage", "disk": "mem_usage"}
check("disk in a 3-character box is DSK", pcstats.labels(cfg) == {"cpu": "DSK", "gpu": "GPU", "disk": "RAM"})
check("70 24 payload", pcstats.label_payload("disk", "RAM") == bytes([2]) + b"RAM\0")
cfg.data.pop("stats_sources"); cfg.data.pop("stats_kinds")
check("values clamp to 0-99", [pcstats.to_byte(v) for v in (None, -3, 42.6, 100, 250)] == [0, 0, 43, 99, 99])
# the built-in sensor helper (hub/sensors.py) serves LibreHardwareMonitor's format; it comes first
from hub import sensors
helper = http.server.ThreadingHTTPServer(("127.0.0.1", 0), type("H", (http.server.BaseHTTPRequestHandler,), {
    "do_GET": lambda self: (self.send_response(200), self.end_headers(), self.wfile.write(json.dumps(lhm).encode())),
    "log_message": lambda *a: None}))
threading.Thread(target=helper.serve_forever, daemon=True).start()
real_url, sensors.URL = sensors.URL, f"http://127.0.0.1:{helper.server_port}/data.json"
cfg.data["lhm_url"] = "http://127.0.0.1:9/data.json"                 # nothing there
cfg.data["stats_sources"] = {"cpu": "cpu_temp", "gpu": "gpu_temp", "disk": "disk_temp"}
got = pcstats.Sampler(cfg, log).sample()
check(f"temperatures from the sensor helper: {got}",
      got == {"cpu": (61.3, "°C", "TENET Hub sensors"), "gpu": (54.0, "°C", "TENET Hub sensors"),
              "disk": (38.0, "°C", "TENET Hub sensors")})
helper.shutdown(); sensors.URL = real_url
cfg.data.pop("stats_sources"); cfg.data.pop("lhm_url")
x = sensors.task_xml("PC\\dylan")
check("sensor task: logon trigger for the user, highest rights, no time limit, runs --sensors",
      "<UserId>PC\\dylan</UserId>" in x and "<RunLevel>HighestAvailable</RunLevel>" in x
      and "<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>" in x and "--sensors</Arguments>" in x
      and "<DisallowStartIfOnBatteries>false" in x)
smp = pcstats.Sampler(cfg, log)
check(f"sampler without sources (Linux): {smp.sample()}", all(v[0] is None for v in smp.sample().values()))

# --- keyboard clock (stock 70 07 / 70 0A, what Hexcore Link sends) ------------------------------
import datetime
B.patch, B.state = 0, "app"                       # works on the official firmware too
host._disconnect("x"); B.time = None; B.settings_writes = 0
cfg.data["clock_format"] = "24"; host._tick()
t = datetime.datetime.now()
check(f"clock set on connect (official firmware): {B.time}", B.time is not None and B.time[:3] == (t.month, t.day, t.isoweekday())
      and abs((B.time[3] * 3600 + B.time[4] * 60 + B.time[5]) - (t.hour * 3600 + t.minute * 60 + t.second)) <= 2)
check(f"24-hour sent once: 12h={B.clock_12h}, {B.settings_writes} write", B.clock_12h is False and B.settings_writes == 1)
n = B.cmds.count("7007")
for _ in range(3):
    host._tick()
check("not resent every tick", B.cmds.count("7007") - n <= 1 and B.settings_writes == 1)
host._clock_at -= 61; host._tick()
check("resent after a minute", B.cmds.count("7007") > n)
cfg.data["clock_format"] = "12"; host._tick()
check("switching to 12-hour sends it", B.clock_12h is True and B.settings_writes == 2)
cfg.data["sync_clock"] = False; host._clock_at = 0; n = B.cmds.count("7007"); host._tick()
check("sync off: no clock packets", B.cmds.count("7007") == n)
cfg.data["sync_clock"] = True; cfg.data["clock_format"] = "auto"

# --- v21: screen list, every screen package on a screen of its own ----------------------------
from hub import screens as screenlist
B.patch, B.mode, B.scr, B.hub_alive, B.list_writes = 24, 1, [1, 0x80, 6, 7], False, 0
cfg.pkg("hello")["enabled"] = True
cfg.pkg("nowplaying")["enabled"] = True
cfg.set("screens", [])
cfg.save()
host._disconnect("x"); host.reload()
for _ in range(3):
    host._tick()
items = screenlist.entries(cfg, reg)
check(f"default order: {[e['id'] for e in items]}",
      [e["id"] for e in items] == ["stock:1", "pkg:hello", "pkg:nowplaying", "stock:2", "stock:3", "stock:4", "stock:6", "stock:8", "stock:7"]
      and [e["on"] for e in items] == [True, True, True, False, False, False, True, True, True])
check(f"both screen packages run, no conflicts ({sorted(host.running)}, {host.problems})",
      sorted(host.running) == ["hello", "nowplaying"] and not host.problems)
check(f"list sent to the keyboard once: {[hex(x) for x in B.scr]} ({B.list_writes} write)",
      B.scr == [1, 0x80, 0x81, 6, 8, 7] and B.list_writes == 1 and B.hub_alive)
for _ in range(3):
    host._tick()
check("not rewritten while it matches", B.list_writes == 1)
B.press()
for _ in range(4):
    host._tick()
check(f"button -> hello's screen ({B.mode}, slot {B.slot}); hello drawn", B.mode == 5 and B.slot == 0
      and bytes(B.screen) == screen.to_4bpp(ref) and host.running["hello"].shown and not host.running["nowplaying"].shown)
B.press()
for _ in range(15):
    host._tick(); time.sleep(0.05)
np_img = host.running["nowplaying"].obj.frame(time.time())
check("button -> Now Playing's own screen, drawn", B.slot == 1 and host.running["nowplaying"].shown
      and not host.running["hello"].shown and bytes(B.screen)[:16 * 128] == screen.to_4bpp(np_img)[:16 * 128])
B.press(); check("button -> image screen", B.mode == 6 and not B.stats_on)
# --- v24 PC stats screen ---
check("PC stats sampler running (the screen is in the list)", host.stats.running())
host.stats.values = lambda: bytes([12, 61, 4])
cfg.set("stats_kinds", {"cpu": "usage", "gpu": "temp", "disk": "usage"})
B.press(); host._stats_at = 0
for _ in range(2):
    host._tick()
check(f"button -> PC stats; values sent {B.stats_values}, units {B.stats_units:#05b}",
      B.mode == 6 and B.stats_on and B.stats_values == (12, 61, 4) and B.stats_units == 0b101)
cfg.set("stats_kinds", {"cpu": "temp", "gpu": "temp", "disk": "temp"}); host._tick()
check("changing a slot to temperature updates the units", B.stats_units == 0)
cfg.set("stats_sources", {"cpu": "mem_usage", "gpu": "gpu_temp", "disk": "disk_temp"}); host._tick()
check("memory usage in a slot shows %", B.stats_units == 0b001)
check("v24 keyboard: no labels sent", B.stats_labels == [b""] * 3 and "7024" not in B.cmds)
B.patch = 25; host.fw = type(host.fw)("patch", 25, True); host._tick()
check(f"v25 keyboard: labels sent {B.stats_labels}", B.stats_labels == [b"RAM", b"GPU", b"DISK"])
n = B.cmds.count("7024"); host._tick()
check("labels only resent when they change", B.cmds.count("7024") == n)
B.patch = 24; host.fw = type(host.fw)("patch", 24, True)
cfg.data.pop("stats_sources")
# --- key lights (stock 20 03 FF 01 r g b, any firmware) ---
B.lights = None; host._tick()
check("lighting 'keep': nothing sent", B.lights is None and "2003" not in B.cmds)
cfg.set("lighting", "solid"); cfg.set("lighting_color", "#FF8000"); host._tick()
check(f"lighting 'solid': every key #FF8000 ({B.lights})", B.lights == (255, 128, 0))
n = B.cmds.count("2003"); host._tick()
check("lighting only resent when it changes", B.cmds.count("2003") == n)
cfg.set("lighting", "off"); host._tick()
check("lighting 'off': black", B.lights == (0, 0, 0))
cfg.set("lighting", "keep"); host._tick()
check("back to 'keep': left alone", B.lights == (0, 0, 0) and B.cmds.count("2003") == n + 1)
host._bright_at = 0; host._tick()
check(f"brightness read from the keyboard: {host.brightness}", host.brightness == 80 and B.brightness_writes == 0)
host.request_brightness(35); host._tick()
check(f"brightness set: keyboard {B.brightness}, one write", B.brightness == 35 and host.brightness == 35
      and B.brightness_writes == 1)
host.request_brightness(35); host._tick()
check("same brightness again: not written", B.brightness_writes == 1)
host.request_brightness(250); host._tick()
check("brightness kept within 0-100", B.brightness == 100)
B.press(); B.stats_values = None; host._stats_at = 0; host._tick()
check("off the PC stats screen: no values sent", B.mode == 7 and B.stats_values is None)
host.running["hello"].ctx.request_mode(5); host._tick()
check(f"a package asking for its screen gets its own one ({B.mode}, slot {B.slot})", B.mode == 5 and B.slot == 0)
host._request("show", 3); host._tick()
check("Screens tab 'Show it now' works", B.mode == 3)
items = screenlist.entries(cfg, reg)
ids = [e["id"] for e in items]
items.insert(0, items.pop(ids.index("pkg:nowplaying")))
next(e for e in items if e["id"] == "stock:3")["on"] = True
next(e for e in items if e["id"] == "stock:7")["on"] = False
screenlist.save(cfg, items)
host._tick()
check(f"reordered + clock on + typing off: {[hex(x) for x in B.scr]}", B.scr == [0x80, 1, 0x81, 3, 6, 8])
host._tick(); host._tick()
check("package slots follow the order (Now Playing is 0x80 now)", host.slots == {"nowplaying": 0x80, "hello": 0x81})
cfg.pkg("hello")["enabled"] = False; cfg.save(); host._tick()
check(f"package turned off -> its screen leaves the list: {[hex(x) for x in B.scr]}", B.scr == [0x80, 1, 3, 6, 8])
B.hub_alive = False; B.mode = 1; B.press()
check("Hub not running: the button skips package screens", B.mode == 3)
only_pkgs = [{"id": "pkg:nowplaying", "on": True, "name": "", "kind": ""}, {"id": "stock:1", "on": False}]
check("a list with no built-in screen gets the Dashboard added", screenlist.keyboard_list(only_pkgs)[0] == [1, 0x80])
B.scr = [1, 0x80, 6, 7]                  # the keyboard lost the list (v21 without a boot animation)
host._tick()
check("the keyboard lost the list: not resent within 3 s", B.scr == [1, 0x80, 6, 7])
host._sent_at -= 4; host._tick()
check(f"... resent after 3 s: {[hex(x) for x in B.scr]}", B.scr == [0x80, 1, 3, 6, 8])
cfg.set("screens", []); cfg.pkg("hello")["enabled"] = True; cfg.save()
check(f"never pipelined ({B.violations})", B.violations == 0)

# --- image screen: convert with adjustments, upload, skip unchanged frames, clear ------------------
import zlib
from hub import gif
from hub.device import Keyboard
B.patch, B.state = 20, "app"
host._disconnect("x")
src = [Image.new("RGB", (64, 16), (k * 40, k * 40, k * 40)) for k in range(6)]
buf = io.BytesIO()
src[0].save(buf, "GIF", save_all=True, append_images=src[1:], duration=50, loop=0)
fr = gif.convert(buf.getvalue(), {"fps_cap": 10})
check(f"fps cap 10 on a 20 fps GIF: {len(fr)} frames, delays {[d for _, d in fr]}",
      all(d >= 10 for _, d in fr) and sum(d for _, d in fr) == 30)
fr2 = gif.convert(buf.getvalue(), {"fps_cap": 30, "invert": True, "fit": "stretch"})
check("invert + stretch: first (black) frame becomes fully lit", fr2[0][0].getextrema() == (255, 255))
fr3 = gif.convert(buf.getvalue(), {"fps_cap": 30, "fit": "original", "x": 50})
bbox = fr3[-1][0].getbbox()
check(f"original size moved right by 50: lit area {bbox}", bbox == (96 + 50, 8, 160 + 50, 24))
fr4 = gif.convert(buf.getvalue(), {"fps_cap": 30, "trim_start": 2, "trim_end": 1})
check(f"trim 2 at the start and 1 at the end: {len(fr4)} frames", len(fr4) == 3)
fr5 = gif.convert(buf.getvalue(), {"fps_cap": 30, "brightness": 100})
check("brightness +100 saturates", all(i.getextrema()[0] == 255 for i, _ in fr5))
still = io.BytesIO(); Image.new("RGB", (300, 60), (200, 200, 200)).save(still, "JPEG")
fr6 = gif.convert(still.getvalue(), {})
check(f"a still JPEG becomes one frame: {len(fr6)} frame, size {fr6[0][0].size}", len(fr6) == 1 and fr6[0][0].size == (256, 32))
kind, path = fake_hid.enumerate()[1]["usage_page"], b"vendor"
kb = Keyboard(b"vendor")
inf = gif.info(kb)
check(f"keyboard GIF info: {inf}", inf and inf["capacity"] == (2 * 1024 * 1024 - 0x41000) // 4096 and inf["frames"] == 0)
steps = []
gif.upload(kb, fr, 192, lambda f, m: steps.append(m))
inf = gif.info(kb)
hdr = bytes(B.spi[0x40000:0x40000 + 16 + 2 * len(fr)])
check(f"uploaded: keyboard reports {inf['frames']} frames, header {hdr[:4]} burst {struct.unpack_from('<H', hdr, 8)[0]}",
      inf["frames"] == len(fr) and hdr == gif.header(fr, 192))
check("every stored frame matches", all(bytes(B.spi[0x41000 + i * 4096:0x42000 + i * 4096]) == screen.to_4bpp(img)
                                        for i, (img, _) in enumerate(fr)))
w = B.gif_writes
gif.upload(kb, fr, 192)
check(f"re-uploading the same animation rewrites only the header ({B.gif_writes - w} writes)", B.gif_writes - w <= 2)
big = [(Image.new("L", (256, 32), 0), 1)] * (inf["capacity"] + 1)
try:
    gif.upload(kb, big, 192)
    check("too many frames refused", False)
except gif.GifError as e:
    check(f"too many frames refused before writing ({e})", True)
gif.clear(kb)
check("cleared: no animation on the keyboard", gif.info(kb)["frames"] == 0)
kb.close()
check(f"never sent a packet before the previous reply (violations={B.violations})", B.violations == 0)

# --- boot logo (v20): convert, upload, restore, refused on older firmware ---------------------
from hub import bootlogo
d = bootlogo.default_image()
check(f"built-in logo preview is 256x32 with something lit: {d.size} {d.getbbox()}", d.size == (256, 32) and d.getbbox())
logo_src = io.BytesIO(); Image.new("RGB", (128, 64), (255, 255, 255)).save(logo_src, "PNG")
bi = bootlogo.convert(logo_src.getvalue(), {"fit": "contain"})
check(f"boot image fits inside: lit {bi.getbbox()}", bi.size == (256, 32) and bi.getbbox() == (96, 0, 160, 32))
kb = Keyboard(b"vendor")
check("no custom boot logo yet", bootlogo.has_custom(kb) is False)
spi_before = bytes(B.spi)
bootlogo.upload(kb, bi)
check("custom boot logo stored", bootlogo.has_custom(kb) is True and bytes(B.spi[0x3F000:0x40000]) == screen.to_4bpp(bi)
      and bytes(B.spi[0x3E000:0x3E005]) == b"TBOT\x01")
changed = [i for i in range(len(spi_before)) if spi_before[i] != B.spi[i]]
check(f"only the boot area changed ({hex(min(changed))}..{hex(max(changed))})", 0x3E000 <= min(changed) and max(changed) < 0x40000)
check("the image screen is untouched", gif.info(kb)["frames"] == 0)
bootlogo.restore(kb)
check("restored: back to the built-in logo", bootlogo.has_custom(kb) is False)
kb.close()
B.patch = 19
kb = Keyboard(b"vendor")
try:
    bootlogo.upload(kb, bi)
    check("v19 refuses the boot logo", False)
except gif.GifError as e:
    check(f"v19: clear message ({e})", "v20" in str(e))
kb.close()
B.patch = 20
check(f"still no pipelined packets (violations={B.violations})", B.violations == 0)

print("ALL PASS" if ok else "SOME FAILED")
srv.shutdown()
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(0 if ok else 1)
