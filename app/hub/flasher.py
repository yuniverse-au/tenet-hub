"""Firmware flashing over the keyboard's own update (IAP) protocol.

The packets are byte-for-byte the ones Hexcore Link sends (decoded from USB captures and proven on
hardware). Recovery if anything goes wrong: unplug the keyboard, hold Esc while plugging it back
in (it starts in update mode), then flash the official firmware again.
"""
import hashlib, struct, time

from .device import VID, PID, MODEL_TENET70, hid_module, enumerate_keyboards

CHUNK = 48
MAX_APP = 0x1C000
APP_HDR = bytes([0x7B, 0x12, 0x31, 0x00])
IAP_HDR = bytes([0x7B, 0x10, 0x31, 0x10])


class FlashError(Exception):
    pass


def _pkt(header, cmd, payload=b""):
    body = cmd + payload
    return (header + bytes([len(body), 0, 0, 0x7D]) + body).ljust(64, b"\x00")


class _Dev:
    def __init__(self, path):
        self.h = hid_module().device()
        self.h.open_path(path)
        self.h.set_nonblocking(0)
        while self.h.read(64, timeout_ms=20):
            pass

    def xfer(self, p, cmd, timeout=2000):
        self.h.write(b"\x00" + p)
        end = time.time() + timeout / 1000
        while time.time() < end:
            r = bytes(self.h.read(64, timeout_ms=200) or b"")
            if len(r) >= 10 and r[0] == 0x7B and r[7] == 0x7D and r[8:10] == cmd:
                return r[10:10 + r[4] - 2]
        raise FlashError(f"The keyboard didn't answer command {cmd.hex()}.")

    def close(self):
        try:
            self.h.close()
        except Exception:
            pass


def check_image(data):
    sp, reset = struct.unpack_from("<II", data, 0)
    if not (0x20000000 <= sp <= 0x20008000 and 0x4000 <= (reset & ~1) < 0x4000 + len(data)
            and len(data) <= MAX_APP):
        raise FlashError("This doesn't look like a TENET 70 firmware image.")


def flash(data, allowed_sha256, progress=lambda frac, msg: None):
    """Flashes `data`. It must match one of `allowed_sha256` (upper-case hex). Blocks ~30 s."""
    sha = hashlib.sha256(data).hexdigest().upper()
    if sha not in {s.upper() for s in allowed_sha256}:
        raise FlashError("Firmware checksum doesn't match a known release. Not flashing.")
    check_image(data)

    kind, path = enumerate_keyboards()
    if kind is None:
        raise FlashError("Keyboard not found. Is it plugged in, and is Hexcore Link closed?")
    if kind == "app":
        progress(0.0, "Switching the keyboard to update mode...")
        d = _Dev(path)
        d.h.write(b"\x00" + _pkt(APP_HDR, b"\x02\x01", b"\x01"))
        d.close()
        for _ in range(30):
            time.sleep(0.5)
            kind, path = enumerate_keyboards()
            if kind == "iap":
                break
        else:
            raise FlashError("The keyboard didn't come back in update mode. Nothing was written; "
                             "unplug it, plug it back in and try again.")
        time.sleep(0.6)

    d = _Dev(path)
    try:
        model = d.xfer(_pkt(IAP_HDR, b"\x01\x01"), b"\x01\x01")
        if model[:1] != bytes([MODEL_TENET70]):
            raise FlashError(f"Unexpected keyboard model ({model.hex()}); this only supports the "
                             "TENET 70. Nothing was written.")
        d.xfer(_pkt(IAP_HDR, b"\x02\x02"), b"\x02\x02")
        info = d.xfer(_pkt(IAP_HDR, b"\x02\x03"), b"\x02\x03")
        start = struct.unpack_from("<I", info, 2)[0]
        if start != 0x4000:
            raise FlashError("Unexpected keyboard layout; stopping before erasing anything.")

        progress(0.02, "Erasing...")
        st = d.xfer(_pkt(IAP_HDR, b"\x02\x43", struct.pack("<I", start)), b"\x02\x43", 10000)
        if st[:1] != b"\x00":
            raise FlashError("Erase failed. The keyboard stays in update mode: flash the official "
                             "firmware from the Firmware tab to recover.")
        n = (len(data) + CHUNK - 1) // CHUNK
        for i in range(n):
            chunk = data[i * CHUNK:(i + 1) * CHUNK]
            st = d.xfer(_pkt(IAP_HDR, b"\x02\x31", struct.pack("<I", start + i * CHUNK) + chunk),
                        b"\x02\x31")
            if st[:1] != b"\x00":
                raise FlashError("Write failed. The keyboard stays in update mode: flash the "
                                 "official firmware from the Firmware tab to recover.")
            if i % 20 == 0 or i == n - 1:
                progress(0.03 + 0.95 * (i + 1) / n, f"Writing... {100 * (i + 1) // n}%")
        d.xfer(_pkt(IAP_HDR, b"\x02\x01", b"\x02"), b"\x02\x01")
    finally:
        d.close()
    progress(1.0, "Done. The keyboard is restarting.")
