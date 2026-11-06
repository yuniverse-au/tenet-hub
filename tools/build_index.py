"""Builds the catalogue the app reads: index.json + catalog/<package>-<version>.zip.

    python tools/build_index.py

Run it after changing a package or adding a firmware release, then commit index.json and catalog/.
Packages come from packages/<id>/ (package.json + code). Firmware releases come from
firmware/<id>/release.json + patch.json (make one with tools/make_patch.py).
"""
import hashlib, json, os, sys, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
from hub import VERSION                      # noqa: E402
from hub.packages import Manifest            # noqa: E402

HUB_RELEASES = "https://github.com/yuniverse-au/tenet-hub/releases/latest"


def sha(b):
    return hashlib.sha256(b).hexdigest().upper()


def zip_package(folder, out_path):
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_STORED) as z:     # stored: identical bytes on every OS
        for base, dirs, files in os.walk(folder):
            dirs[:] = sorted(d for d in dirs if d != "__pycache__" and not d.startswith("."))
            for f in sorted(files):
                if f.endswith((".pyc", ".pyo")) or f.startswith("."):
                    continue
                p = os.path.join(base, f)
                info = zipfile.ZipInfo(os.path.relpath(p, folder).replace(os.sep, "/"), (2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_STORED
                z.writestr(info, open(p, "rb").read())      # fixed timestamps: same zip each build


def main():
    os.makedirs(os.path.join(ROOT, "catalog"), exist_ok=True)
    packages = []
    for pid in sorted(os.listdir(os.path.join(ROOT, "packages"))):
        folder = os.path.join(ROOT, "packages", pid)
        if not os.path.isfile(os.path.join(folder, "package.json")):
            continue
        m = Manifest.load(folder)
        rel = f"catalog/{m['id']}-{m['version']}.zip"
        zip_package(folder, os.path.join(ROOT, rel))
        data = open(os.path.join(ROOT, rel), "rb").read()
        entry = {k: m[k] for k in ("id", "name", "version", "type", "description", "requires_firmware")}
        for k in ("author", "default_mode", "changelog", "requires_hub"):
            if k in m:
                entry[k] = m[k]
        entry.update(url=rel, sha256=sha(data), size=len(data))
        packages.append(entry)
        print(f"package  {m['id']} {m['version']}  {len(data)} bytes")

    releases = []
    fwroot = os.path.join(ROOT, "firmware")
    for fid in sorted(os.listdir(fwroot)) if os.path.isdir(fwroot) else []:
        rpath = os.path.join(fwroot, fid, "release.json")
        if not os.path.isfile(rpath):
            continue
        rel = json.load(open(rpath, encoding="utf-8"))
        praw = open(os.path.join(fwroot, fid, "patch.json"), "rb").read()
        patch = json.loads(praw)
        rel.update(id=fid, patch={"url": f"firmware/{fid}/patch.json", "sha256": sha(praw)},
                   result_sha256=patch["result_sha256"], result_size=patch["result_size"],
                   base_sha256=patch["base_sha256"])
        releases.append(rel)
        print(f"firmware {fid}  REVIVER v{rel.get('patch_version')}")

    index = {"format": 1, "hub": {"version": VERSION, "url": HUB_RELEASES},
             "packages": packages, "firmware": releases}
    with open(os.path.join(ROOT, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print("wrote index.json")


if __name__ == "__main__":
    main()
