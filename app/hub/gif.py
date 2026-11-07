"""The keyboard's image screen (screen 6, REVIVER v16+).

The animation is stored on the keyboard's flash chip and the firmware plays it by itself, even
with the PC off. This module turns any GIF into keyboard frames (with the user's adjustments) and
uploads them.

Storage layout (offsets from the GIF area, which starts at 0x40000 on the flash chip):
  0x0000  header: "TGIF", version 1, flags, frame count u16, burst u16, 6 spare bytes,
          then one u16 delay per frame in 10 ms ticks
  0x1000  frame 0, then frame 1, ...: 4096 bytes each, the screen in controller order

REVIVER v26 stores several images side by side ("slots"), each its own screen:
  slot 0     = the Image screen; header at 0x0000 (as above)
  slots 1..7 = more stored images (list codes 0x41..0x47); headers in the chip's last 7 sectors
  All frames come from one pool starting at 0x1000. A version 2 header adds the slot's first pool
  frame (u16 at bytes 10..11); version 1 (slot 0 only) means pool frame 0.
"""
import io, struct, zlib

from PIL import Image, ImageOps, ImageSequence

from .screen import to_4bpp

W, H = 256, 32
MAGIC = b"TGIF"
FRAME_BYTES = 4096
MIN_PATCH = 16         # v14 crashed switching to the image screen; v15 refused uploads
SLOTS_PATCH = 26       # several stored images, each its own screen
SLOTS = 8

DEFAULTS = {
    "fps_cap": 12,          # never faster than this (lower it if playback stutters)
    "brightness": 0,        # -100..100
    "contrast": 100,        # percent
    "gamma": 100,           # percent (100 = unchanged; higher = darker mid-tones)
    "invert": False,
    "fit": "contain",       # contain | cover | stretch | original
    "zoom": 100,            # percent, on top of the fit
    "rotate": 0,            # degrees clockwise, 0-360 (before the fit, so it fits the turned picture)
    "x": 0, "y": 0,         # move the picture (pixels)
    "trim_start": 0,        # first source frame to use
    "trim_end": 0,          # source frames to drop from the end
    "burst": 192,           # work per burst before the firmware lets the keyboard breathe:
                            # higher = smoother animation, lower = typing always comes first
}
FITS = {"contain": "Fit inside", "cover": "Fill and crop", "stretch": "Stretch", "original": "Original size"}


class GifError(Exception):
    pass


def load(source):
    """source: path, bytes or PIL image. Returns [(RGBA image, duration ms)]."""
    img = source if isinstance(source, Image.Image) else Image.open(
        io.BytesIO(source) if isinstance(source, (bytes, bytearray)) else source)
    out = []
    for fr in ImageSequence.Iterator(img):
        d = fr.info.get("duration", img.info.get("duration", 100)) or 100
        out.append((fr.convert("RGBA"), max(10, int(d))))
    if not out:
        raise GifError("That file has no frames.")
    return out


def _place(fr, s):
    """Scale/crop/position one frame onto the 256x32 screen (greyscale)."""
    bg = Image.new("RGBA", fr.size, (0, 0, 0, 255))
    g = Image.alpha_composite(bg, fr).convert("L")
    angle = float(s.get("rotate", 0)) % 360
    if angle:
        g = g.rotate(-angle, resample=Image.BICUBIC, expand=True, fillcolor=0)
    w, h = g.size
    fit = s["fit"]
    if fit == "stretch":
        nw, nh = W, H
    elif fit == "original":
        nw, nh = w, h
    else:
        k = (min if fit == "contain" else max)(W / w, H / h)
        nw, nh = w * k, h * k
    z = max(0.05, s["zoom"] / 100)
    nw, nh = max(1, round(nw * z)), max(1, round(nh * z))
    g = g.resize((nw, nh), Image.LANCZOS if nw < w else Image.BICUBIC)
    canvas = Image.new("L", (W, H), 0)
    canvas.paste(g, ((W - nw) // 2 + int(s["x"]), (H - nh) // 2 + int(s["y"])))
    return canvas


def _tone(img, s):
    b, c, gm = s["brightness"] / 100 * 255, s["contrast"] / 100, max(0.1, s["gamma"] / 100)
    lut = []
    for v in range(256):
        x = (v - 128) * c + 128 + b
        x = min(255.0, max(0.0, x))
        x = 255 * (x / 255) ** gm
        lut.append(int(round(x)))
    img = img.point(lut)
    if s["invert"]:
        img = ImageOps.invert(img)
    return img.point(lambda v: (v * 15 + 127) // 255 * 17)       # the screen's 16 grey levels


def convert(source, settings=None):
    """-> list of (256x32 "L" image, delay in 10 ms ticks), with the user's adjustments applied."""
    s = dict(DEFAULTS)
    s.update(settings or {})
    frames = load(source)
    a, b = int(s["trim_start"]), len(frames) - int(s["trim_end"])
    frames = frames[max(0, a):max(a + 1, b)] or frames[:1]
    # resample onto a timeline no faster than the fps cap, then merge repeats
    step = 1000 / max(1, min(50, float(s["fps_cap"])))
    total = sum(d for _, d in frames)
    starts, t = [], 0
    for _, d in frames:
        starts.append(t)
        t += d
    picked, t, i = [], 0.0, 0
    if min(d for _, d in frames) >= step * 0.9:      # already within the cap (GIFs store 10 ms steps)
        picked = [[k, d] for k, (_, d) in enumerate(frames)]
        total = 0
    while t < total - 1e-6:
        while i + 1 < len(frames) and starts[i + 1] <= t:
            i += 1
        if picked and picked[-1][0] == i:
            picked[-1][1] += step
        else:
            picked.append([i, step])
        t += step
    if len(frames) == 1:
        picked = [[0, 1000]]          # still image: the keyboard only re-checks it once a second
    cache, out = {}, []
    for idx, ms in picked:
        if idx not in cache:
            cache[idx] = _tone(_place(frames[idx][0], s), s)
        out.append((cache[idx], max(1, round(ms / 10))))
    # adjacent frames that came out identical (e.g. after trimming colours) -> one longer frame
    merged = []
    for img, d in out:
        if merged and merged[-1][0].tobytes() == img.tobytes():
            merged[-1] = (merged[-1][0], min(65535, merged[-1][1] + d))
        else:
            merged.append((img, d))
    if len(merged) > 1 and merged[0][0].tobytes() == merged[-1][0].tobytes():
        merged[0] = (merged[0][0], min(65535, merged[0][1] + merged.pop()[1]))
    return merged


def header(frames, burst, first=None):
    """Version 1 (first=None, before v26) or version 2 with the first pool frame."""
    n = len(frames)
    b = max(32, min(4096, int(burst)))
    if first is None:
        h = MAGIC + struct.pack("<BBHH6x", 1, 0, n, b)
    else:
        h = MAGIC + struct.pack("<BBHHH4x", 2, 0, n, b, first)
    return h + b"".join(struct.pack("<H", d) for _, d in frames)


# --- talking to the keyboard (hub.device.Keyboard) -------------------------------------------
def _o24(v):
    return bytes([v & 255, (v >> 8) & 255, (v >> 16) & 255])


def info(kb):
    """-> dict(chip, capacity, frames) or None if the firmware has no image screen."""
    r = kb.cmd(0x15)
    if not r or r[0] != 0 or len(r) < 13:
        return None
    chip, base, cap, frames = struct.unpack("<IIHH", r[1:13])
    out = {"chip": chip, "base": base, "capacity": cap, "frames": frames, "slots": 1, "pool": cap}
    if len(r) >= 17 and r[14] >= 2:                      # v26: image slots
        out["slots"], out["pool"] = r[14], struct.unpack("<H", r[15:17])[0]
    return out


def slot_offset(inf, s):
    """Header offset of slot s, from the start of the GIF area."""
    return 0 if s == 0 else inf["chip"] - s * 0x1000 - inf["base"]


def _read(kb, off, n):
    r = kb.cmd(0x18, _o24(off) + bytes([n]), timeout=2000)
    return r[1:1 + n] if r and r[0] == 0 and len(r) >= 1 + n else None


def read_slots(kb, inf=None):
    """-> {slot: (first pool frame, frame count)} for the slots holding an image."""
    inf = inf or info(kb)
    out = {}
    if not inf:
        return out
    for s in range(inf["slots"]):
        h = _read(kb, slot_offset(inf, s), 12)
        if not h or h[:4] != MAGIC:
            continue
        ver, n = h[4], struct.unpack("<H", h[6:8])[0]
        first = struct.unpack("<H", h[10:12])[0] if ver == 2 else 0
        if n and (ver == 2 or (ver == 1 and s == 0)) and first + n <= max(inf["pool"], inf["capacity"]):
            out[s] = (first, n)
    return out


def _place_frames(inf, used, n, own=None):
    """First pool frame for n frames, not overlapping other slots. Keeps the slot's current place
    if it still fits (unchanged frames then skip rewriting)."""
    taken = sorted(r for s, r in used.items() if s != own)
    def free(a):
        return a + n <= inf["pool"] and all(a + n <= f or a >= f + k for f, k in taken)
    if own in used and free(used[own][0]):
        return used[own][0]
    for a in [0] + [f + k for f, k in taken]:
        if free(a):
            return a
    return None


def _crc(kb, off, length):
    r = kb.cmd(0x19, _o24(off) + _o24(length), timeout=3000)
    return struct.unpack("<I", r[1:5])[0] if r and r[0] == 0 else None


def _write(kb, off, data):
    for p in range(0, len(data), 48):
        part = data[p:p + 48]
        r = kb.cmd(0x17, _o24(off + p) + bytes([len(part)]) + part, timeout=2000)
        if r != b"\x00":
            raise GifError("The keyboard didn't answer a write." if r is None else
                           f"The keyboard refused a write (reply {r.hex()}).")


def _erase(kb, off):
    r = kb.cmd(0x16, _o24(off), timeout=8000)
    if r is None:
        raise GifError("The keyboard didn't answer the erase command (in 8 s).")
    if r != b"\x00":
        raise GifError(f"The keyboard refused to erase its GIF area (reply {r.hex()}).")


def upload(kb, frames, burst, progress=lambda frac, msg: None, slot=0):
    """Stores the animation on the keyboard (in image slot `slot`, v26+). Unchanged frames are
    skipped (checked by CRC)."""
    inf = info(kb)
    if inf is None:
        raise GifError(f"This keyboard's firmware has no image screen (needs REVIVER v{MIN_PATCH}+).")
    if slot and inf["slots"] <= slot:
        raise GifError(f"Storing more than one image needs REVIVER v{SLOTS_PATCH} or newer (Firmware tab).")
    if inf["capacity"] == 0:
        raise GifError("This keyboard's flash chip has no room for a GIF.")
    n = len(frames)
    first = None
    if inf["slots"] > 1:
        used = read_slots(kb, inf)
        first = _place_frames(inf, used, n, own=slot)
        if first is None:
            free = inf["pool"] - sum(k for s, (_, k) in used.items() if s != slot)
            raise GifError(f"{n} frames won't fit: the keyboard has room for {max(0, free)} more "
                           f"(it holds {inf['pool']} in all, shared by every stored image). "
                           "Lower the frame-rate cap, trim the GIF or remove another stored image.")
    elif n > inf["capacity"]:
        raise GifError(f"{n} frames won't fit; the keyboard holds {inf['capacity']}. "
                       "Lower the frame-rate cap or trim the GIF.")
    hdr = header(frames, burst, first)
    hoff = slot_offset(inf, slot) if inf["slots"] > 1 else 0
    progress(0.0, "Preparing...")
    _erase(kb, hoff)                                # no valid header while frames change
    for i, (img, _) in enumerate(frames):
        data = to_4bpp(img)
        off = 0x1000 * ((first or 0) + i + 1)
        if _crc(kb, off, FRAME_BYTES) != zlib.crc32(data):
            for attempt in range(2):
                _erase(kb, off)
                _write(kb, off, data)
                if _crc(kb, off, FRAME_BYTES) == zlib.crc32(data):
                    break
            else:
                raise GifError(f"Frame {i + 1} didn't verify after writing.")
        progress(0.02 + 0.95 * (i + 1) / n, f"Frame {i + 1} of {n}")
    _write(kb, hoff + 16, hdr[16:])                 # delays first,
    _write(kb, hoff, hdr[:16])                      # then the part with the magic: the commit
    if _crc(kb, hoff, len(hdr)) != zlib.crc32(hdr):
        raise GifError("The animation header didn't verify.")
    progress(1.0, f"Stored {n} frames on the keyboard.")


def clear(kb, slot=0):
    if slot:
        inf = info(kb)
        if inf and inf["slots"] > slot:
            _erase(kb, slot_offset(inf, slot))
        return
    _erase(kb, 0)
