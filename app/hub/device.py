"""Talking to the TENET 70's vendor interface.

Hard rule (learned 2026-11-05): never send a vendor command before the previous one has been
answered. Flooding the keyboard overflowed its input buffer and jammed its command channel until
the firmware was re-flashed. Every packet here goes through Keyboard.cmd(), which holds a lock and
waits for the reply.
"""
import threading, time

VID, PID = 0x3311, 0xF005
VENDOR_USAGE_PAGE = 0xFF00
MODEL_TENET70 = 0x0C


def hid_module():
    import hid
    return hid


def enumerate_keyboards():
    """Returns ('app', path) / ('iap', path) / (None, None)."""
    hid = hid_module()
    for d in hid.enumerate(VID, PID):
        if "IAP" in (d.get("product_string") or ""):
            return "iap", d["path"]
    for d in hid.enumerate(VID, PID):
        if d["usage_page"] == VENDOR_USAGE_PAGE:
            return "app", d["path"]
    return None, None


class FirmwareInfo:
    """What the keyboard is running, worked out from which commands it answers."""

    def __init__(self, kind, patch=None, exact=False):
        self.kind = kind          # "official" | "patch"
        self.patch = patch        # patch number (lower bound when not exact)
        self.exact = exact

    def at_least(self, n):
        return self.kind == "patch" and self.patch is not None and self.patch >= n

    def __str__(self):
        if self.kind != "patch":
            return "Official firmware (no patch)"
        return f"REVIVER v{self.patch}" + ("" if self.exact else "+ (older REVIVER)")


class Keyboard:
    def __init__(self, path):
        hid = hid_module()
        self.dev = hid.device()
        self.dev.open_path(path)
        self.dev.set_nonblocking(0)
        self.lock = threading.RLock()
        while self.dev.read(64, timeout_ms=20):      # drop anything stale
            pass

    def close(self):
        try:
            self.dev.close()
        except Exception:
            pass

    def cmd(self, sub, payload=b"", timeout=300):
        """One 70-xx packet; waits for the ACK. Returns the reply payload or None."""
        p = bytes([0x7B, 0x12, 0x31, 0x00, 2 + len(payload), 0, 0, 0x7D, 0x70, sub]) + bytes(payload)
        with self.lock:
            self.dev.write(b"\x00" + p.ljust(64, b"\x00"))
            end = time.time() + timeout / 1000
            while True:
                left = int((end - time.time()) * 1000)
                if left <= 0:
                    return None
                r = bytes(self.dev.read(64, timeout_ms=left) or b"")
                if not r:
                    return None
                if r[8:10] == bytes([0x70, sub]):
                    return r[10:10 + r[4] - 2]
                # an unrelated report (e.g. the keyboard talking to Hexcore Link): keep waiting

    # --- firmware identification ---------------------------------------------------------
    def probe(self):
        """Sub-commands above 0x0A are ignored by the official firmware, so these are safe."""
        r = self.cmd(0x14)                                   # v13+: patch version
        if r and len(r) >= 4 and r[0] == 0 and r[1:3] == b"TH":
            return FirmwareInfo("patch", r[3], exact=True)
        if self.cmd(0x12) is not None:                       # v6..v12: mode get
            return FirmwareInfo("patch", 6)
        if self.cmd(0x10, bytes([0, 0, 1, 0])) is not None:  # v4/v5: band buffer
            return FirmwareInfo("patch", 4)
        if self.cmd(0x0C, bytes([1, 0xE3])) is not None:     # v2/v3: raw OLED (NOP)
            return FirmwareInfo("patch", 2)
        return FirmwareInfo("official")

    # --- helpers used by the host ---------------------------------------------------------
    def mode(self):
        r = self.cmd(0x12)
        return r[1] if r and len(r) >= 2 and r[0] == 0 else None

    def set_mode(self, m):
        return self.cmd(0x13, bytes([m])) == b"\x00"

    def oled(self, *command_bytes):
        """Raw OLED controller command (REVIVER v2+)."""
        return self.cmd(0x0C, bytes([len(command_bytes)]) + bytes(command_bytes)) == b"\x00"

    def set_i2c(self, sh, sl):
        return self.cmd(0x0E, bytes([sh & 255, sh >> 8, sl & 255, sl >> 8])) == b"\x00"
