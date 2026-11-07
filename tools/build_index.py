"""Builds the catalogue the app reads: index.json + catalog/<package>-<version>.zip.

    python tools/build_index.py
    python tools/build_index.py --community --root ../my-tenet-packages [--name "My packages"]

The second form builds a community catalogue in someone's own repo (packages only: no firmware or
TENET Hub entries). See docs/community-packages.md.

Run it after changing a package or adding a firmware release, then commit index.json and catalog/.
Packages come from packages/<id>/ (package.json + code). Firmware releases come from
firmware/<id>/release.json + patch.json (make one with tools/make_patch.py).
"""
import hashlib, json, os, sys, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
from hub import VERSION                      # noqa: E402
from hub.packages import Manifest            # noqa: E402

HUB_RELEASES = "https://github.com/yuuusername/tenet-hub/releases/latest"


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
                info.create_system, info.external_attr = 3, 0o644 << 16   # same metadata on every OS
                z.writestr(info, open(p, "rb").read())      # fixed timestamps: same zip each build


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Build index.json + catalog/ from packages/")
    ap.add_argument("--root", default=ROOT, help="the repo to build (default: this one)")
    ap.add_argument("--community", action="store_true", help="a community catalogue: packages only")
    ap.add_argument("--name", default="", help="community catalogue name shown in TENET Hub")
    a = ap.parse_args()
    base = os.path.abspath(a.root)
    if not os.path.isdir(os.path.join(base, "packages")):
        sys.exit(f"no packages/ folder in {base}")
    os.makedirs(os.path.join(base, "catalog"), exist_ok=True)
    packages = []
    for pid in sorted(os.listdir(os.path.join(base, "packages"))):
        folder = os.path.join(base, "packages", pid)
        if not os.path.isfile(os.path.join(folder, "package.json")):
            continue
        m = Manifest.load(folder)
        rel = f"catalog/{m['id']}-{m['version']}.zip"
        zip_package(folder, os.path.join(base, rel))
        data = open(os.path.join(base, rel), "rb").read()
        entry = {k: m[k] for k in ("id", "name", "version", "type", "description", "requires_firmware")}
        for k in ("author", "default_mode", "changelog", "requires_hub"):
            if k in m:
                entry[k] = m[k]
        entry.update(url=rel, sha256=sha(data), size=len(data))
        packages.append(entry)
        print(f"package  {m['id']} {m['version']}  {len(data)} bytes")

    if a.community:
        index = {"format": 1, "name": a.name, "packages": packages}
        return write(base, index)

    releases = []
    fwroot = os.path.join(base, "firmware")
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
    write(base, index)


def write(base, index):
    with open(os.path.join(base, "index.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print("wrote", os.path.join(base, "index.json"))


if __name__ == "__main__":
    main()
