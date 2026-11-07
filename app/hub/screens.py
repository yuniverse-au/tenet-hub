"""The keyboard's screen list (REVIVER v21+): which screens the screen button goes through, in
what order. Built-in screens can be turned off; every screen package gets a screen of its own.

The keyboard keeps the list in its flash, so the button works the same with the PC off. Package
screens are only part of the cycle while TENET Hub is running (it sends a heartbeat); otherwise
the keyboard skips them, so it never stops on a blank screen.

Settings: cfg["screens"] = [{"id": "stock:1" | "pkg:<package id>", "on": bool}, ...] in button
order. A package screen is on when the package is enabled (one switch for both).
"""
MIN_PATCH = 21
MAX = 24                      # entries the keyboard holds (6 built-in + up to 18 package screens)
PKG = 0x80                    # keyboard code of package screen k = 0x80 + k

STATS = 8                     # PC stats (REVIVER v24+): the stock Monitor screen, fed by TENET Hub
STATS_PATCH = 24
STOCK = {                     # in default order
    1: "Dashboard",
    2: "Keystrokes per minute",
    3: "Clock",
    4: "Logo (the image Hexcore Link uploaded)",
    6: "Image screen",
    8: "PC stats",
    7: "Typing animation",
}
DEFAULT_ON = {1, 6, 7, 8}
NEEDS_HUB = {STATS}           # like package screens: only in the cycle while TENET Hub runs
IMG = 0x40                    # stored image slot s (REVIVER v26+): code 0x40 + s, s = 1..7
IMG_PATCH = 26


def _screen_packages(reg):
    """Packages that get a screen: screen packages (drawn live) and animation packages (stored on
    the keyboard, v26+)."""
    return [pid for pid, m in sorted(reg.installed.items(), key=lambda x: x[1].get("name", x[0]).lower())
            if m.get("type") in ("screen", "animation")]


def entries(cfg, reg):
    """The full ordered list: [{"id", "on", "name", "kind"}]. Adds new built-in screens and newly
    installed screen packages, drops uninstalled packages."""
    pkgs = _screen_packages(reg)
    saved = [e for e in (cfg.get("screens") or []) if isinstance(e, dict) and "id" in e]
    out, seen = [], set()
    for e in saved:
        i = e["id"]
        if i in seen:
            continue
        if i.startswith("stock:") and i[6:].isdigit() and int(i[6:]) in STOCK:
            out.append({"id": i, "on": bool(e.get("on"))})
        elif i.startswith("pkg:") and i[4:] in pkgs:
            out.append({"id": i})
        else:
            continue
        seen.add(i)
    if not out:                                   # first run: the v20 order, packages after Dashboard
        out = [{"id": f"stock:{k}", "on": k in DEFAULT_ON} for k in STOCK]
        seen = {e["id"] for e in out}
    for pid in pkgs:
        if f"pkg:{pid}" not in seen:
            if reg.installed[pid].get("type") == "animation":     # stored images: after the Image screen
                at = next((n + 1 for n, e in enumerate(out) if e["id"] == "stock:6"), len(out))
                while at < len(out) and out[at]["id"].startswith("pkg:") and \
                        reg.installed.get(out[at]["id"][4:], {}).get("type") == "animation":
                    at += 1
                out.insert(at, {"id": f"pkg:{pid}"})
            else:                                 # screen packages: after the last package screen
                last = max([n for n, e in enumerate(out) if e["id"].startswith("pkg:")] + [0])
                out.insert(last + 1, {"id": f"pkg:{pid}"})
    for k in STOCK:
        if f"stock:{k}" not in seen and not any(e["id"] == f"stock:{k}" for e in out):
            if k == STATS:                        # new in v24: after the image screen, on
                at = next((n + 1 for n, e in enumerate(out) if e["id"] == "stock:6"), len(out))
                out.insert(at, {"id": f"stock:{k}", "on": True})
            else:
                out.append({"id": f"stock:{k}", "on": False})
    for e in out:
        if e["id"].startswith("pkg:"):
            pid = e["id"][4:]
            e["on"] = bool(cfg.pkg(pid).get("enabled"))
            e["name"] = reg.installed[pid].get("name", pid)
            e["type"] = reg.installed[pid].get("type")
            e["kind"] = "Package (stored)" if e["type"] == "animation" else "Package"
        else:
            e["name"] = STOCK[int(e["id"][6:])]
            e["kind"] = "Built in"
    return out


def save(cfg, items):
    """Store the order and the built-in on/off switches (package switches are the packages' own)."""
    cfg.set("screens", [{"id": e["id"], "on": bool(e["on"])} if e["id"].startswith("stock:") else {"id": e["id"]}
                        for e in items])


def keyboard_list(items, patch=99, stored=None):
    """-> (codes for 70 21, {package id: code}). Always has a built-in screen the keyboard can show
    on its own; at most MAX entries. `patch`: the keyboard's REVIVER version (older ones refuse
    codes they don't know, e.g. PC stats before v24). `stored`: {animation package id: image slot}
    for the animations already stored on the keyboard (others are left out until they are)."""
    codes, slots, k = [], {}, 0
    stored = stored or {}
    for e in items:
        if not e["on"] or len(codes) >= MAX:
            continue
        if e["id"].startswith("stock:"):
            code = int(e["id"][6:])
            if code == STATS and patch < STATS_PATCH:
                continue
            codes.append(code)
        elif e.get("type") == "animation":
            pid = e["id"][4:]
            if patch >= IMG_PATCH and pid in stored:
                slots[pid] = IMG + stored[pid]
                codes.append(IMG + stored[pid])
        elif k < MAX:
            slots[e["id"][4:]] = PKG + k
            codes.append(PKG + k)
            k += 1
    if not any(c < IMG and c not in NEEDS_HUB for c in codes) and not any(IMG < c < PKG for c in codes):
        codes = ([1] + codes)[:MAX]
    slots = {p: c for p, c in slots.items() if c in codes}
    return codes, slots


def name_of(code, items):
    if code < PKG:
        return STOCK.get(code, f"Screen {code}")
    _, slots = keyboard_list(items)
    for e in items:
        if e["id"].startswith("pkg:") and slots.get(e["id"][4:]) == code:
            return e["name"]
    return "Package screen"
