"""Firmware releases.

The repository never contains Hexcore's firmware. A release is a *patch*: the bytes that differ
from the official image. The app downloads the official firmware from Hexcore's own server (the
same file Hexcore Link installs), checks its SHA-256, applies the patch, and checks the result's
SHA-256 before anything is flashed.

Patch file (JSON):
  {"format": 1, "base_sha256": "...", "base_size": n, "result_sha256": "...", "result_size": n,
   "fill": "ff", "segments": [{"offset": n, "data": "<base64>"}, ...]}
"""
import base64, hashlib, os

from . import net
from .config import data_dir

OFFICIAL_307 = {
    "version": "3.0.7",
    "url": "https://s5.hexcore.xyz/releases/firmware/apxtenet/3.07/apx_tenet_KEY_APP.bin",
    "sha256": "9AAB23C1098FA05D7EE0645826DA099D403A1A1285BAD8E70750202577DF4F56",
    "size": 86620,
}


class FirmwareError(Exception):
    pass


def sha256(b):
    return hashlib.sha256(b).hexdigest().upper()


def make_patch(base, result, gap=16):
    """Bytes that differ from the official image (runs closer than `gap` bytes are merged)."""
    diff = [i >= len(base) or base[i] != result[i] for i in range(len(result))]
    runs, i, n = [], 0, len(result)
    while i < n:
        if not diff[i]:
            i += 1
            continue
        s = i
        while i < n and diff[i]:
            i += 1
        if runs and s - runs[-1][1] < gap:
            runs[-1][1] = i
        else:
            runs.append([s, i])
    segs = [{"offset": s, "data": base64.b64encode(result[s:e]).decode()} for s, e in runs]
    return {"format": 1, "base_sha256": sha256(base), "base_size": len(base),
            "result_sha256": sha256(result), "result_size": len(result), "fill": "ff",
            "segments": segs}


def apply_patch(base, patch):
    if patch.get("format") != 1:
        raise FirmwareError("This firmware release needs a newer version of TENET Hub.")
    if sha256(base) != patch["base_sha256"].upper():
        raise FirmwareError("The official firmware doesn't match what this patch was made for.")
    out = bytearray(base[:patch["result_size"]])
    out += bytes.fromhex(patch.get("fill", "ff")) * (patch["result_size"] - len(out))
    for s in patch["segments"]:
        d = base64.b64decode(s["data"])
        out[s["offset"]:s["offset"] + len(d)] = d
    out = bytes(out)
    if sha256(out) != patch["result_sha256"].upper():
        raise FirmwareError("The patched firmware didn't come out as expected. Not flashing.")
    return out


def official(entry=OFFICIAL_307, local_path=None):
    """The official image, verified. Cached after the first download."""
    cache = os.path.join(data_dir("firmware"), f"official_{entry['version']}.bin")
    for path in (local_path, cache):
        if path and os.path.exists(path):
            data = open(path, "rb").read()
            if sha256(data) == entry["sha256"].upper():
                if path != cache:
                    open(cache, "wb").write(data)
                return data
    try:
        data = net.get(entry["url"], timeout=60)
    except Exception as e:
        raise FirmwareError(f"Couldn't download the official firmware from Hexcore ({e}).")
    if sha256(data) != entry["sha256"].upper():
        raise FirmwareError("The official firmware download didn't match its checksum.")
    open(cache, "wb").write(data)
    return data


def build(release, index_url, local_official=None):
    """Firmware bytes for an index 'firmware' entry, fully verified."""
    base = official(release.get("base", OFFICIAL_307), local_official)
    patch_url = net.resolve(index_url, release["patch"]["url"])
    raw = net.get(patch_url)
    if sha256(raw) != release["patch"]["sha256"].upper():
        raise FirmwareError("The patch download didn't match its checksum.")
    import json
    patch = json.loads(raw.decode())
    if patch["result_sha256"].upper() != release["result_sha256"].upper():
        raise FirmwareError("The patch doesn't match the release listing.")
    return apply_patch(base, patch)
