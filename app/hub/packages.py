"""Installed packages and the online catalogue (index.json in the GitHub repo).

index.json:
{
  "format": 1,
  "hub": {"version": "1.0.0", "url": "https://github.com/.../releases/latest"},
  "packages": [{"id", "name", "version", "type", "description", "requires_firmware",
                "url": "catalog/nowplaying-1.0.0.zip", "sha256": "...", "size": n, "changelog": "..."}],
  "firmware": [{"id": "v13", "name", "patch_version": 13, "notes", "base": {...official...},
                "patch": {"url": "firmware/v13.patch.json", "sha256": "..."},
                "result_sha256": "...", "result_size": n}]
}
URLs may be relative to index.json. Every download's SHA-256 is checked before it's used.

Packages run as normal Python code on the PC, so the app only installs them from the catalogue
the user has configured (or folders the user put in the packages folder themselves).
"""
import importlib.util, io, json, os, re, shutil, sys, threading, time, zipfile

from . import VERSION, net
from .config import data_dir

ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")


def vtuple(v):
    out = []
    for p in str(v).split("."):
        m = re.match(r"\d+", p)
        out.append(int(m.group()) if m else 0)
    return tuple(out)


class Manifest(dict):
    @classmethod
    def load(cls, folder):
        with open(os.path.join(folder, "package.json"), encoding="utf-8") as f:
            m = cls(json.load(f))
        m.folder = folder
        m.validate()
        return m

    def validate(self):
        if not ID_RE.match(str(self.get("id", ""))):
            raise ValueError(f"bad package id {self.get('id')!r}")
        if self.get("type") not in ("screen", "background", "animation"):
            raise ValueError(f"{self['id']}: type must be 'screen', 'background' or 'animation'")
        if self["type"] == "animation":
            f = self.get("file", "animation.gif")
            if ".." in f or os.path.isabs(f):
                raise ValueError(f"{self['id']}: bad animation file")
            self.setdefault("file", f)
        self.setdefault("name", self["id"])
        self.setdefault("version", "0.0.0")
        self.setdefault("entry", "main.py")
        self.setdefault("class", "Package")
        self.setdefault("requires_firmware", 0)
        self.setdefault("settings", [])
        if ".." in self["entry"] or os.path.isabs(self["entry"]):
            raise ValueError(f"{self['id']}: bad entry file")

    def animation_path(self):
        return os.path.join(self.folder, self["file"])

    def defaults(self):
        return {s["key"]: s.get("default") for s in self["settings"] if "key" in s}


class Registry:
    def __init__(self, config, log=print):
        self.cfg, self.log = config, log
        self.root = data_dir("packages")
        self.lock = threading.RLock()
        self.index = None             # last fetched catalogue
        self.index_error = None
        self.installed = {}           # id -> Manifest
        cache = os.path.join(data_dir(), "index_cache.json")
        try:
            self.index = json.load(open(cache, encoding="utf-8"))
        except (OSError, ValueError):
            pass
        self.scan()

    # --- installed -----------------------------------------------------------------------
    def scan(self):
        """Finds every package folder, including ones the user dropped in by hand."""
        found = {}
        for name in sorted(os.listdir(self.root)):
            folder = os.path.join(self.root, name)
            if name.startswith(".") or not os.path.isfile(os.path.join(folder, "package.json")):
                continue
            try:
                m = Manifest.load(folder)
                found[m["id"]] = m
            except Exception as e:
                self.log(f"skipping package folder {name}: {e}")
        with self.lock:
            self.installed = found
        return found

    def bundled(self, folder):
        """Packages shipped inside the app: installed on first run, and upgraded if newer."""
        if not os.path.isdir(folder):
            return
        for name in os.listdir(folder):
            src = os.path.join(folder, name)
            try:
                m = Manifest.load(src)
            except Exception:
                continue
            cur = self.installed.get(m["id"])
            if cur is None or vtuple(m["version"]) > vtuple(cur["version"]):
                self._install_folder(src, m["id"])
                first = cur is None
                self.scan()
                if first and m["id"] not in self.cfg["seen"]:
                    p = self.cfg.pkg(m["id"])
                    p["enabled"] = True
                    self.cfg.set("seen", self.cfg["seen"] + [m["id"]])

    def _install_folder(self, src, pid):
        dest = os.path.join(self.root, pid)
        tmp = dest + ".new"
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.copytree(src, tmp, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.rmtree(dest, ignore_errors=True)
        os.replace(tmp, dest)

    def remove(self, pid):
        shutil.rmtree(os.path.join(self.root, pid), ignore_errors=True)
        self.scan()

    # --- catalogue -----------------------------------------------------------------------
    def refresh(self):
        """Fetches index.json. Returns (new_ids, updatable_ids)."""
        url = self.cfg["index_url"]
        try:
            idx = net.get_json(url)
            if idx.get("format") != 1:
                raise ValueError("this catalogue needs a newer TENET Hub")
            idx["_url"] = url
            with self.lock:
                self.index, self.index_error = idx, None
            with open(os.path.join(data_dir(), "index_cache.json"), "w", encoding="utf-8") as f:
                json.dump(idx, f)
            self.cfg.set("last_check", time.time())
        except Exception as e:
            self.index_error = str(e)
            self.log("couldn't check for updates:", e)
        return self.new_ids(), self.updatable()

    def available(self):
        return {p["id"]: p for p in (self.index or {}).get("packages", []) if ID_RE.match(p.get("id", ""))}

    def new_ids(self):
        seen = set(self.cfg["seen"])
        return [pid for pid in self.available() if pid not in seen and pid not in self.installed]

    def mark_seen(self, ids):
        self.cfg.set("seen", sorted(set(self.cfg["seen"]) | set(ids)))

    def updatable(self):
        av = self.available()
        return [pid for pid, m in self.installed.items()
                if pid in av and vtuple(av[pid]["version"]) > vtuple(m["version"])]

    def hub_update(self):
        h = (self.index or {}).get("hub") or {}
        return h if h.get("version") and vtuple(h["version"]) > vtuple(VERSION) else None

    def firmware_releases(self):
        return sorted((self.index or {}).get("firmware", []), key=lambda f: -f.get("patch_version", 0))

    def install(self, pid):
        """Download, verify and install (or update) a package from the catalogue."""
        entry = self.available().get(pid)
        if not entry:
            raise ValueError(f"{pid} isn't in the catalogue")
        if vtuple(entry.get("requires_hub", "0")) > vtuple(VERSION):
            raise ValueError(f"{entry.get('name', pid)} needs a newer TENET Hub")
        raw = net.get(net.resolve(self.index["_url"] if "_url" in self.index else self.cfg["index_url"],
                                  entry["url"]))
        import hashlib
        if hashlib.sha256(raw).hexdigest().upper() != entry["sha256"].upper():
            raise ValueError("download didn't match its checksum; not installed")
        tmp = os.path.join(self.root, f".{pid}.download")
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp)
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            for info in z.infolist():
                target = os.path.realpath(os.path.join(tmp, info.filename))
                if not target.startswith(os.path.realpath(tmp) + os.sep):
                    raise ValueError("bad path inside the package; not installed")
            z.extractall(tmp)
        inner = tmp
        if not os.path.exists(os.path.join(inner, "package.json")):      # zipped folder
            subs = [d for d in os.listdir(tmp) if os.path.isdir(os.path.join(tmp, d))]
            if len(subs) == 1:
                inner = os.path.join(tmp, subs[0])
        m = Manifest.load(inner)
        if m["id"] != pid:
            raise ValueError("package id doesn't match the catalogue")
        with self.lock:
            self._install_folder(inner, pid)
        shutil.rmtree(tmp, ignore_errors=True)
        self.mark_seen([pid])
        self.scan()
        return self.installed[pid]

    # --- loading code ----------------------------------------------------------------------
    def load_class(self, m):
        name = "tenet_pkg_" + m["id"].replace("-", "_")
        for k in [k for k in sys.modules if k == name or k.startswith(name + ".")]:
            del sys.modules[k]                         # reload after an update
        spec = importlib.util.spec_from_file_location(
            name, os.path.join(m.folder, m["entry"]), submodule_search_locations=[m.folder])
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return getattr(mod, m["class"])
