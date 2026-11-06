"""A fake TENET 70 for tests: speaks the vendor protocol (REVIVER v23) and the IAP protocol.
Install with sys.modules['hid'] = fake_hid. Records everything and flags pipelined packets."""
import struct, threading, time

VID, PID = 0x3311, 0xF005


class Board:
    def __init__(self, patch=23):
        self.patch = patch
        self.state = "app"          # "app" | "iap" | "gone"
        self.mode = 5
        self.flash = bytearray()
        self.screen = bytearray(4096)
        self.oled_log = []
        self.cmds = []
        self.pending = None
        self.violations = 0
        self.lock = threading.Lock()
        self.chip = 2 * 1024 * 1024
        self.spi = bytearray(b"\xff" * self.chip)
        self.gif_writes = 0
        self.scr = [1, 0x80, 6, 7]       # v21 screen list
        self.slot = 0
        self.hub_alive = False
        self.list_writes = 0

    def _gif(self, sub, a):
        import zlib
        base, size = 0x40000, self.chip - 0x40000
        if sub >= 0x1B:                                # v20: same ops on the boot-logo area
            base, size, sub = 0x3E000, 0x2000, sub - 5
        o24 = lambda b: b[0] | b[1] << 8 | b[2] << 16
        ok = lambda off, n: n <= size and off <= size - n
        if sub == 0x15:
            h = self.spi[base:base + 8]
            frames = struct.unpack_from("<H", h, 6)[0] if h[:4] == b"TGIF" and h[4] == 1 else 0
            r = b"\x00" + struct.pack("<IIHH", self.chip, base, (self.chip - base - 0x1000) // 4096, frames)
            if self.patch >= 20:
                r += bytes([self.spi[0x3E000:0x3E004] == b"TBOT" and self.spi[0x3E004] == 1])
            return r
        off = o24(a)
        if sub == 0x16 and off % 4096 == 0 and ok(off, 4096):
            self.spi[base + off:base + off + 4096] = b"\xff" * 4096
            return b"\x00"
        if sub == 0x17 and 0 < a[3] <= 48 and ok(off, a[3]):
            for i in range(a[3]):                      # (this module's enumerate() is the HID one)
                self.spi[base + off + i] &= a[4 + i]
            self.gif_writes += 1
            return b"\x00"
        if sub == 0x18 and 0 < a[3] <= 48 and ok(off, a[3]):
            return b"\x00" + bytes(self.spi[base + off:base + off + a[3]])
        if sub == 0x19:
            n = o24(a[3:6])
            if ok(off, n):
                return b"\x00" + struct.pack("<I", zlib.crc32(bytes(self.spi[base + off:base + off + n])))
        return b"\x01"

    def _screens(self, sub, a):
        ok = True
        valid = lambda e: (1 <= e <= 7 and e != 5) or 0x80 <= e < 0x98
        if sub == 0x20:
            self.hub_alive = True
        elif sub == 0x21:
            n, l = a[0], list(a[1:1 + a[0]])
            ok = 1 <= n <= 24 and all(map(valid, l)) and len(set(l)) == n and any(e < 0x80 for e in l)
            if ok:
                self.scr, self.list_writes = l, self.list_writes + 1
        elif sub == 0x22:
            ok = valid(a[0])
            if ok:
                if a[0] >= 0x80:
                    self.mode, self.slot = 5, a[0] - 0x80
                else:
                    self.mode = a[0]
        if not ok:
            return b"\x01"
        cur = 0x80 + self.slot if self.mode == 5 else self.mode
        return bytes([0, self.mode, cur, self.hub_alive, len(self.scr)] + self.scr)

    def press(self, forward=True):
        """The screen button, as the v21 firmware does it."""
        l = self.scr
        cur = 0x80 + self.slot if self.mode == 5 else self.mode
        at = l.index(cur) if cur in l else (len(l) - 1 if forward else 0)
        for k in range(1, len(l) + 1):
            e = l[(at + (k if forward else -k)) % len(l)]
            if e >= 0x80 and not self.hub_alive:
                continue
            if e >= 0x80:
                self.mode, self.slot = 5, e - 0x80
            else:
                self.mode = e
            return

    def reply(self, hdr, cmd, payload=b""):
        body = cmd + payload
        return hdr + bytes([len(body), 0, 0, 0x7D]) + body

    def handle(self, p):
        with self.lock:
            if self.pending is not None:
                self.violations += 1             # sent before reading the previous answer
            self.pending = self._handle(p)

    def _handle(self, p):
        cmd = p[8:10]
        args = p[10:10 + p[4] - 2]
        self.cmds.append(cmd.hex())
        if self.state == "app":
            R = lambda c, pl=b"": self.reply(bytes([0x7B, 0x12, 0x13, 0x00]), c, pl)
            if cmd == b"\x02\x01" and args[:1] == b"\x01":
                self.state = "iap"
                return None
            if cmd[0] != 0x70 or self.patch == 0:
                return None
            sub = cmd[1]
            if sub == 0x0B:
                i = args[0]
                self.screen[i * 32:(i + 1) * 32] = args[1:33]
                return R(cmd, b"\x00")
            if sub == 0x0C:
                self.oled_log.append(bytes(args[1:1 + args[0]]).hex())
                return R(cmd, b"\x00")
            if sub in (0x0E, 0x10, 0x11) and self.patch >= 3:
                return R(cmd, b"\x00")
            if sub == 0x12 and self.patch >= 6:
                return R(cmd, bytes([0, self.mode]))
            if sub == 0x13 and self.patch >= 6:
                self.mode = args[0]
                return R(cmd, b"\x00")
            if sub == 0x14 and self.patch >= 13:
                return R(cmd, b"\x00TH" + bytes([self.patch, 0]))
            if 0x15 <= sub <= 0x19 and self.patch >= 14:
                return R(cmd, self._gif(sub, args))
            if 0x1B <= sub <= 0x1E and self.patch >= 20:
                return R(cmd, self._gif(sub, args))
            if 0x1F <= sub <= 0x22 and self.patch >= 21:
                return R(cmd, self._screens(sub, args))
            return None
        R = lambda c, pl=b"": self.reply(bytes([0x7B, 0x10, 0x13, 0x10]), c, pl)
        if cmd == b"\x01\x01":
            return R(cmd, b"\x0c\x01")
        if cmd == b"\x02\x02":
            return R(cmd, b"\x01\x01")
        if cmd == b"\x02\x03":
            return R(cmd, b"\x00\x00" + struct.pack("<I", 0x4000) + bytes(26))
        if cmd == b"\x02\x43":
            self.flash = bytearray()
            return R(cmd, b"\x00")
        if cmd == b"\x02\x31":
            addr = struct.unpack_from("<I", args)[0] - 0x4000
            data = bytes(args[4:])
            if len(self.flash) < addr:
                self.flash += b"\xff" * (addr - len(self.flash))
            self.flash[addr:addr + len(data)] = data
            return R(cmd, b"\x00\x00")
        if cmd == b"\x02\x01" and args[:1] == b"\x02":
            self.state = "app"
            return R(cmd, b"\x00\x01")
        return None


BOARD = Board()


def enumerate(vid=0, pid=0):
    b = BOARD
    if b.state == "gone":
        return []
    if b.state == "iap":
        return [dict(vendor_id=VID, product_id=PID, usage_page=1, usage=0, interface_number=0,
                     product_string="APX-TENET IAP", path=b"iap")]
    return [dict(vendor_id=VID, product_id=PID, usage_page=1, usage=6, interface_number=0,
                 product_string="TENET 70", path=b"kbd"),
            dict(vendor_id=VID, product_id=PID, usage_page=0xFF00, usage=1, interface_number=1,
                 product_string="TENET 70", path=b"vendor")]


class device:
    def open_path(self, path):
        if BOARD.state == "gone":
            raise OSError("open failed")
        self.path = path

    def set_nonblocking(self, v):
        pass

    def write(self, data):
        if BOARD.state == "gone":
            raise OSError("write failed")
        assert data[0] == 0 and len(data) == 65, len(data)
        BOARD.handle(bytes(data[1:]))
        return len(data)

    def read(self, n, timeout_ms=0):
        with BOARD.lock:
            r, BOARD.pending = BOARD.pending, None
        if r is None:
            time.sleep(min(timeout_ms, 50) / 1000)
            return []
        return list(r.ljust(64, b"\x00"))

    def close(self):
        pass
