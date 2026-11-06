"""Draws package images on the keyboard without saving anything to its memory.

Only 32-byte pieces that changed since the last frame are sent (70 0B, REVIVER v1+), strictly one
packet per ACK. Packages can ask for their top rows to go through the firmware's off-screen
buffer instead (70 10 / 70 11, REVIVER v4+): those rows are pure black/white but update in one burst,
so scrolling text doesn't tear.
"""
from PIL import Image, ImageOps

W, H = 256, 32
I2C_DEFAULT = (48, 60)        # OLED bus SCL periods at power-up (400 kHz)


def to_4bpp(img):
    """256x32 "L" image -> 4096 bytes in the controller's order (rows bottom-up, 2 px/byte)."""
    img = ImageOps.flip(img.convert("L"))
    px = img.load()
    out = bytearray()
    for y in range(H):
        for x in range(0, W, 2):
            out.append((px[x, y] >> 4) << 4 | (px[x + 1, y] >> 4))
    return bytes(out)


class Screen:
    def __init__(self, kb, fw, contrast=0x3D, i2c_speed=2.0):
        self.kb, self.fw = kb, fw
        self.contrast = contrast
        self.last = [None] * 128
        self.last_band = None
        self.is_on = True
        self.hold_off = False         # PC asleep: nothing may switch the screen on
        self.can_raw = fw.at_least(2)
        self.can_band = fw.at_least(4)
        if self.can_band and i2c_speed and i2c_speed != 1:
            sh, sl = (round(v / i2c_speed) for v in I2C_DEFAULT)
            kb.set_i2c(sh, sl)
        if self.can_raw:              # display on (a sleep/shutdown may have left it off)
            kb.oled(0xAE if self.hold_off else 0xAF)
            kb.oled(0x81, contrast)

    def forget(self):
        """Something else drew on the screen: send everything next time."""
        self.last = [None] * 128
        self.last_band = None

    def power(self, on):
        """Returns True if the state changed."""
        if not self.can_raw or on == self.is_on or (on and self.hold_off):
            return False
        self.kb.oled(0xAF if on else 0xAE)
        if on:
            self.kb.oled(0x81, self.contrast)
            self.forget()
        self.is_on = on
        return True

    def _send_band(self, img, rows):
        px = img.crop((0, 0, W, rows)).point(lambda v: 255 if v > 127 else 0).convert("1")
        band = b"".join(px.crop((0, 31 - r, W, 32 - r)).tobytes() for r in range(32 - rows, 32))
        if band == self.last_band:
            return True
        for off in range(0, len(band), 51):
            part = band[off:off + 51]
            if self.kb.cmd(0x10, bytes([off & 255, off >> 8, len(part)]) + part) != b"\x00":
                return False
        if self.kb.cmd(0x11, bytes([32 - rows, 31, 15]), timeout=500) != b"\x00":
            return False
        self.last_band = band
        return True

    def show(self, img, crisp_rows=0, full=False):
        """Returns False if the keyboard stopped answering (caller reconnects)."""
        if img.size != (W, H):
            img = img.resize((W, H))
        img = img.convert("L")
        buf = to_4bpp(img)
        if full:
            self.forget()
        crisp_rows = crisp_rows if self.can_band else 0
        crisp_rows = min(26, max(0, crisp_rows)) & ~3          # whole 32-byte rows of chunks
        skip = range((32 - crisp_rows) * 4, 128) if crisp_rows else range(0)
        if crisp_rows and not self._send_band(img, crisp_rows):
            self.forget()
            return False
        changed = [i for i in range(128) if i not in skip and buf[i * 32:(i + 1) * 32] != self.last[i]]
        changed.sort(key=lambda i: (-(i // 4), i % 4))           # top of the screen first
        for i in changed:
            chunk = buf[i * 32:(i + 1) * 32]
            if self.kb.cmd(0x0B, bytes([i]) + chunk) is None:  # one per ACK. Never pipeline.
                self.forget()
                return False
            self.last[i] = chunk
        return True
