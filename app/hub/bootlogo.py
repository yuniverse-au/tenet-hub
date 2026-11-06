"""The boot logo (REVIVER v20+): what the screen shows for 3 s when the keyboard powers on.

By default the firmware draws its built-in TENET 70 logo. A user image replaces it; it is stored
on the keyboard's flash chip in an 8 KB area of its own (0x3E000), separate from the image screen:
  0x0000  header: "TBOT", version 1, 11 spare bytes   (written last: it's the commit)
  0x1000  the full screen, 4096 bytes in controller order
Restoring the default just erases the header.
"""
import base64, io, struct, zlib

from PIL import Image

from . import gif
from .screen import to_4bpp

MIN_PATCH = 20
MAGIC = b"TBOT"
IMAGE_OFF = 0x1000
DEFAULTS = {k: gif.DEFAULTS[k] for k in ("brightness", "contrast", "gamma", "invert", "fit", "zoom", "rotate", "x", "y")}

# The firmware's built-in logo, for the preview (the keyboard has its own copy).
_DEFAULT_PNG = "iVBORw0KGgoAAAANSUhEUgAAAQAAAAAgCAAAAADYwvk4AAACFklEQVR42u2YQUgUURjHfzPmLkpKiwmiB5Eiti51yOzQ4m2zU5duYZB4UNs6FEHpIfQgCwYtVISoHfTQQZCOW4cI8VBWBHWQIGihtSIsV0N03dWvQ7vr201rjWe8w/vBDHz/efzh/eebN/MGLBaLxWKxWLZBXFTiGTW9IR1B0WJegLCItMOWYzf3VAgrlyOKHhyPJ+cmQ6X/OBd35+Oq79xBc3fo0Zk6T1Xg9sua/90GERG5owppEfEXDEqLiHyt2LQD/EV4/k7lDxG5miv7RKSzrOmTyHSJqR1A9RWNZud3w9JItqq7BtF7y897ofGcsQFwuVqbldMFjHzPlq2l8JhfR5uhASy+pqJHm1vwAKzdypUB4D0QS8Mxj5kBSDd01OtyuwiMx3KlH5gDZB48DQYEMCMiIlN5WvQp3t4ix/6NhlPAzY16D5AEWAF8pq4B16H1kB6rCy48eVWwLGTnIQYEcNBxHMc5kS8+e4jbX+TYP1PeBgwoQgLwkjkljH0LdK9x+rgOo7M+eBtVhHfAXsD1weoHYwOYGYVmLU9A/goAk8B+YF8JTK8aGwA3klpsAodh9oGqjKUgCLQA9839EOLjXW3vwEhKVWbDcLKrrKkHXoyasBfIEFK0BEDVgmy1GwwVvReoTYksVBbcwOGMzZsaDO4Avg1oMOnYBYOL+dp6e8vE59T81KWjX+xvCovFYrFYLNvjJ5HoCNfIKlVoAAAAAElFTkSuQmCC"


def default_image():
    return Image.open(io.BytesIO(base64.b64decode(_DEFAULT_PNG))).convert("L").resize((gif.W, gif.H))


def convert(source, settings=None):
    """First frame of any image/GIF -> 256x32 "L" image in the screen's 16 grey levels."""
    s = dict(gif.DEFAULTS)
    s.update(DEFAULTS)
    s.update(settings or {})
    fr = gif.load(source)[0][0]
    return gif._tone(gif._place(fr, s), s)


def has_custom(kb):
    """True/False, or None if the firmware has no user boot logo (before v20)."""
    r = kb.cmd(0x15)
    if not r or r[0] != 0 or len(r) < 14:
        return None
    return bool(r[13])


def _o24(v):
    return gif._o24(v)


def _erase(kb, off):
    r = kb.cmd(0x1B, _o24(off), timeout=8000)
    if r != b"\x00":
        raise gif.GifError("The keyboard didn't answer the erase command." if r is None else
                           f"The keyboard refused to erase its boot logo area (reply {r.hex()}).")


def _write(kb, off, data):
    for p in range(0, len(data), 48):
        part = data[p:p + 48]
        r = kb.cmd(0x1C, _o24(off + p) + bytes([len(part)]) + part, timeout=2000)
        if r != b"\x00":
            raise gif.GifError("The keyboard didn't answer a write." if r is None else
                               f"The keyboard refused a write (reply {r.hex()}).")


def _crc(kb, off, length):
    r = kb.cmd(0x1E, _o24(off) + _o24(length), timeout=3000)
    return struct.unpack("<I", r[1:5])[0] if r and r[0] == 0 and len(r) >= 5 else None


def upload(kb, img, progress=lambda frac, msg: None):
    if has_custom(kb) is None:
        raise gif.GifError(f"This keyboard's firmware can't change the boot logo (needs REVIVER v{MIN_PATCH}+).")
    data = to_4bpp(img)
    progress(0.0, "Preparing...")
    _erase(kb, 0)                                   # back to the built-in logo while the image changes
    for attempt in range(2):
        _erase(kb, IMAGE_OFF)
        _write(kb, IMAGE_OFF, data)
        if _crc(kb, IMAGE_OFF, len(data)) == zlib.crc32(data):
            break
        progress(0.5, "Retrying...")
    else:
        raise gif.GifError("The boot logo didn't verify after writing.")
    hdr = MAGIC + bytes([1]) + bytes(11)
    _write(kb, 0, hdr)
    if _crc(kb, 0, len(hdr)) != zlib.crc32(hdr):
        raise gif.GifError("The boot logo header didn't verify.")
    progress(1.0, "Stored. You'll see it the next time the keyboard powers on.")


def restore(kb):
    """Back to the built-in TENET 70 logo."""
    if has_custom(kb) is None:
        raise gif.GifError(f"This keyboard's firmware can't change the boot logo (needs REVIVER v{MIN_PATCH}+).")
    _erase(kb, 0)
