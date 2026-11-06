"""Turns a REVIVER firmware build into a firmware release (just the changed bytes).

    python tools/make_patch.py official_3.07.bin tenet_3.07_live13_yuniverse.bin v13 \
        --name "REVIVER v15" --patch-version 15 --notes "..."

Writes firmware/<id>/patch.json and release.json. Neither .bin is committed: the app downloads
the official firmware from Hexcore and rebuilds the patched one on the user's PC.
"""
import argparse, json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
from hub import firmware                     # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("official")
    ap.add_argument("patched")
    ap.add_argument("id")
    ap.add_argument("--name", required=True)
    ap.add_argument("--patch-version", type=int, required=True)
    ap.add_argument("--notes", default="")
    a = ap.parse_args()
    base, result = open(a.official, "rb").read(), open(a.patched, "rb").read()
    if firmware.sha256(base) != firmware.OFFICIAL_307["sha256"]:
        sys.exit("The first file isn't the official 3.0.7 firmware.")
    patch = firmware.make_patch(base, result)
    assert firmware.apply_patch(base, patch) == result
    out = os.path.join(ROOT, "firmware", a.id)
    os.makedirs(out, exist_ok=True)
    json.dump(patch, open(os.path.join(out, "patch.json"), "w"), indent=1)
    json.dump({"name": a.name, "patch_version": a.patch_version, "notes": a.notes,
               "base": firmware.OFFICIAL_307}, open(os.path.join(out, "release.json"), "w"), indent=2)
    print(f"{a.id}: {len(patch['segments'])} segments, result {patch['result_sha256'][:16]}...")


if __name__ == "__main__":
    main()
