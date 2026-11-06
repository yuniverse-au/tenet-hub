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

Community sources: other people's catalogues (the same index.json format, in their own GitHub repo),
added by the user in the Packages tab. cfg["community_sources"] = [index.json URL, ...]. They can only
offer packages: their "firmware" and "hub" entries are ignored, and a package can't take the id of an
official one. Each installed package remembers its source (cfg.pkg(id)["source"], None = official);
it only ever updates from that source, and community packages update when the user says so (official
ones update automatically if the user allows it).
"""
import importlib.util, io, json, os, re, shutil, sys, threading, time, zipfile

from . import VERSION, net
from .config import data_dir

ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")
GITHUB_RE = re.compile(r"^https?://(?:www\.)?github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?(?:/(?:tree|blob)/([^/]+))?(/.*)?$")
RAW_RE = re.compile(r"^https://raw\.githubusercontent\.com/([^/]+)/([^/]+)/")


def source_url(link):
    """A community source as the user typed it -> its index.json URL.
    github.com/owner/repo[/tree/branch][/folder] -> raw.githubusercontent.com/owner/repo/HEAD/index.json"""
    link = link.strip().strip("/")
    if not link:
        raise ValueError("paste a link to a GitHub repo or to an index.json")
    if not re.match(r"^https?://", link):
        link = "https://" + link
    m = GITHUB_RE.match(link)
    if m:
        owner, repo, branch, rest = m.groups()
        rest = (rest or "").strip("/")
        path = rest if rest.endswith(".json") else "/".join(x for x in (rest, "index.json") if x)
        return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch or 'HEAD'}/{path}"
    return link if link.endswith(".json") else link + "/index.json"


def source_label(url):
    """index.json URL -> "owner/repo" (or the host), for showing to the user."""
    m = RAW_RE.match(url or "")
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    m = re.match(r"^https?://([^/]+)", url or "")
    return m.group(1) if m else (url or "")


def source_page(url):
    m = RAW_RE.match(url or "")
    return f"https://github.com/{m.group(1)}/{m.group(2)}" if m else url


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
        self.community = {}           # source index URL -> its catalogue
        self.bundled_ids = set()      # official packages that come inside TENET Hub
        self.source_errors = {}       # source index URL -> why it couldn't be read
        cache = os.path.join(data_dir(), "index_cache.json")
        try:
            self.index = json.load(open(cache, encoding="utf-8"))
        except (OSError, ValueError):
            pass
        try:
            cached = json.load(open(os.path.join(data_dir(), "community_cache.json"), encoding="utf-8"))
            self.community = {u: i for u, i in cached.items() if u in self.sources()}
        except (OSError, ValueError, AttributeError):
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
            self.bundled_ids.add(m["id"])
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
        if pid in (self.cfg.get("packages") or {}):
            self.cfg.pkg(pid).pop("source", None)      # may come back from any source later
            self.cfg.save()
        self.scan()

    # --- catalogue -----------------------------------------------------------------------
    def sources(self):
        return [u for u in (self.cfg.get("community_sources") or []) if isinstance(u, str)]

    def fetch_source(self, url):
        """Reads one community catalogue. -> its index (packages only), or raises."""
        idx = net.get_json(url)
        if not isinstance(idx, dict) or idx.get("format") != 1 or not isinstance(idx.get("packages"), list):
            raise ValueError("that isn't a TENET Hub package catalogue (index.json)")
        return {"format": 1, "name": str(idx.get("name") or ""), "_url": url,
                "packages": [p for p in idx["packages"] if isinstance(p, dict)]}

    def add_source(self, link):
        """-> (index URL, its catalogue). Raises if it can't be read; nothing is saved then."""
        url = source_url(link)
        if url == self.cfg["index_url"]:
            raise ValueError("that's the official catalogue")
        idx = self.fetch_source(url)
        with self.lock:
            self.community[url] = idx
            self.source_errors.pop(url, None)
        if url not in self.sources():
            self.cfg.set("community_sources", self.sources() + [url])
        self._save_community()
        return url, idx

    def remove_source(self, url):
        """Stops listing a source. Its installed packages stay, but no longer get updates."""
        self.cfg.set("community_sources", [u for u in self.sources() if u != url])
        with self.lock:
            self.community.pop(url, None)
            self.source_errors.pop(url, None)
        self._save_community()

    def _save_community(self):
        try:
            with open(os.path.join(data_dir(), "community_cache.json"), "w", encoding="utf-8") as f:
                json.dump(self.community, f)
        except OSError:
            pass

    def refresh(self):
        """Fetches index.json and the community sources. Returns (new_ids, updatable_ids)."""
        for url in self.sources():
            try:
                idx = self.fetch_source(url)
                with self.lock:
                    self.community[url] = idx
                    self.source_errors.pop(url, None)
            except Exception as e:
                self.source_errors[url] = str(e)
                self.log(f"couldn't read community source {url}:", e)
        self._save_community()
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

    def official(self):
        return {p["id"]: p for p in (self.index or {}).get("packages", []) if ID_RE.match(p.get("id", ""))}

    def from_source(self, url):
        """A community source's packages, tagged with where they're from. Ids of official packages
        are left out (a source can't replace or update an official package)."""
        idx = self.community.get(url) or {}
        off = self.official()
        out = {}
        for p in idx.get("packages", []):
            pid = p.get("id", "")
            if ID_RE.match(pid) and pid not in off and pid not in out and p.get("url") and p.get("sha256"):
                out[pid] = dict(p, _source=url, _source_label=source_label(url))
        return out

    def source_of(self, pid):
        """Where an installed package came from: None = official (or a folder the user added)."""
        return (self.cfg.get("packages") or {}).get(pid, {}).get("source")

    def available(self):
        """Everything installable: official first, then each community source in the order added.
        An installed community package is only offered by its own source."""
        out = dict(self.official())
        for url in self.sources():
            for pid, p in self.from_source(url).items():
                out.setdefault(pid, p)
        for pid in self.installed:
            src = self.source_of(pid)
            if src:
                own = self.from_source(src).get(pid)
                if own:
                    out[pid] = own
                elif pid in out and out[pid].get("_source"):
                    del out[pid]
        return out

    def is_community(self, pid):
        if pid in self.installed:
            return bool(self.source_of(pid))
        return bool(self.available().get(pid, {}).get("_source"))

    def new_ids(self):
        seen = set(self.cfg["seen"])
        return [pid for pid in self.available() if pid not in seen and pid not in self.installed]

    def mark_seen(self, ids):
        self.cfg.set("seen", sorted(set(self.cfg["seen"]) | set(ids)))

    def updatable(self):
        av = self.available()
        return [pid for pid, m in self.installed.items()
                if pid in av and av[pid].get("_source") == self.source_of(pid)
                and vtuple(av[pid]["version"]) > vtuple(m["version"])]

    def auto_updatable(self):
        """Updates that may install by themselves: official packages only."""
        return [pid for pid in self.updatable() if not self.source_of(pid)]

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
        src = entry.get("_source")
        if pid in self.installed and src != self.source_of(pid):
            raise ValueError(f"{entry.get('name', pid)} is installed from somewhere else; remove it first")
        base = src or (self.index["_url"] if "_url" in self.index else self.cfg["index_url"])
        raw = net.get(net.resolve(base, entry["url"]))
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
        self.cfg.pkg(pid)["source"] = src
        self.cfg.save()
        self.mark_seen([pid])
        self.scan()
        self.log(f"installed {pid} {m['version']}" + (f" from {source_label(src)}" if src else ""))
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
