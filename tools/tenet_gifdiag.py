"""
tenet_gifdiag.py - checks the GIF-screen commands on a TENET 70 running REVIVER v16+.

Close TENET Hub first (only one program can talk to the keyboard), then:
    python tenet_gifdiag.py

It only touches the GIF area of the keyboard's flash chip (the first frame slot), never anything
else. One command at a time, each waiting for its reply.
"""
import struct, sys, time, zlib

import hid

VID, PID = 0x3311, 0xF005


def open_kb():
    for d in hid.enumerate(VID, PID):
        if d["usage_page"] == 0xFF00 and "IAP" not in (d.get("product_string") or ""):
            h = hid.device()
            h.open_path(d["path"])
            while h.read(64, timeout_ms=20):
                pass
            return h
    sys.exit("Keyboard not found. Is it plugged in, and are TENET Hub and Hexcore Link closed?")


def cmd(h, sub, payload=b"", timeout=5000):
    p = bytes([0x7B, 0x12, 0x31, 0x00, 2 + len(payload), 0, 0, 0x7D, 0x70, sub]) + payload
    h.write(b"\x00" + p.ljust(64, b"\x00"))
    t0 = time.time()
    others = 0
    while time.time() - t0 < timeout / 1000:
        r = bytes(h.read(64, timeout_ms=200) or b"")
        if not r:
            continue
        if r[8:10] == bytes([0x70, sub]):
            return r[10:10 + r[4] - 2], (time.time() - t0) * 1000, others
        others += 1
    return None, (time.time() - t0) * 1000, others


def o24(v):
    return bytes([v & 255, (v >> 8) & 255, (v >> 16) & 255])


def show(name, res):
    r, ms, others = res
    txt = "NO REPLY" if r is None else r.hex(" ")
    extra = f"  ({others} other reports seen)" if others else ""
    print(f"{name:34s} {ms:7.0f} ms  {txt}{extra}")
    return r


h = open_kb()
show("70 14 version", cmd(h, 0x14))
r = show("70 15 GIF info", cmd(h, 0x15))
if r and len(r) >= 13 and r[0] == 0:
    chip, base, cap, frames = struct.unpack("<IIHH", r[1:13])
    print(f"{'':34s} chip {chip} bytes, GIF area at {base:#x}, room for {cap} frames, {frames} stored")
show("70 18 read header (16 B)", cmd(h, 0x18, o24(0) + bytes([16])))
show("70 19 CRC of header (16 B)", cmd(h, 0x19, o24(0) + o24(16)))
OFF = 0x1000
show("70 16 erase frame slot 0", cmd(h, 0x16, o24(OFF), timeout=8000))
show("70 18 read after erase (16 B)", cmd(h, 0x18, o24(OFF) + bytes([16])))
data = bytes(range(16))
show("70 17 write 16 B", cmd(h, 0x17, o24(OFF) + bytes([16]) + data))
r = show("70 18 read back (16 B)", cmd(h, 0x18, o24(OFF) + bytes([16])))
print(f"{'':34s} read-back {'MATCHES' if r and r[1:] == data else 'DOES NOT match'} what was written")
r = show("70 19 CRC of 4 KB frame slot", cmd(h, 0x19, o24(OFF) + o24(4096), timeout=8000))
show("70 12 mode (still alive?)", cmd(h, 0x12))
r = show("70 1A player state (v17+)", cmd(h, 0x1A, timeout=1000))
if r and len(r) >= 15 and r[0] == 0:
    age = r[13] | r[14] << 8
    print(f"{'':34s} mode {r[1]}, private stack flag {'SET' if r[2] else 'clear'} (set {age} ticks ago), "
          f"full-redraw {r[3]}, blanked {r[4]}, next frame {r[5] | r[6] << 8}, on screen {r[7] | r[8] << 8}, "
          f"frames {r[9] | r[10] << 8}, upload-pause {r[11]}, waiting {r[12]}")
h.close()
print("\nPlease send me everything above.")
