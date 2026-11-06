"""The API packages are written against.

A package is a folder with a package.json and a Python entry file (default main.py) that defines
a class named `Package` (or the name in "class"), subclassing ScreenPackage or BackgroundPackage.

package.json:
{
  "id": "nowplaying",                 # lowercase letters, digits, - and _
  "name": "Now Playing",
  "version": "1.0.0",
  "type": "screen",                   # "screen" or "background"
  "description": "...",
  "author": "...",
  "entry": "main.py",                 # optional
  "default_mode": 5,                  # screen packages: which keyboard screen they draw on (5)
  "requires_firmware": 13,            # minimum REVIVER version (0 = official is fine)
  "requires_hub": "1.0.0",            # optional
  "settings": [                       # optional; shown in the app's package settings dialog
     {"key": "spectrum", "type": "bool", "default": true, "label": "Audio spectrum"},
     {"key": "fps", "type": "int", "default": 12, "min": 1, "max": 20, "label": "Spectrum FPS"},
     {"key": "name", "type": "str", "default": "", "label": "Something"},
     {"key": "style", "type": "choice", "default": "a", "choices": ["a", "b"], "label": "Style"}
  ]
}

Screen packages draw 256x32 greyscale PIL images ("L", 0 = off, 255 = brightest; the screen
shows 16 levels). Only the parts that change are sent to the keyboard.
"""
WIDTH, HEIGHT = 256, 32

# Keyboard screens a package can take over (before REVIVER v21). From v21 every screen package gets a
# screen of its own in the keyboard's screen list (Screens tab), so this only matters on older
# firmware. The firmware draws nothing on mode 5 (REVIVER v12+), so a package's drawing stays.
PACKAGE_MODES = {5: "Custom Text screen"}


class Context:
    """Handed to a package's start(). Don't keep references to the keyboard yourself."""

    def __init__(self, package_id, settings, data_dir, log, firmware, request):
        self.id = package_id
        self.settings = settings          # dict, updated live when the user changes settings
        self.data_dir = data_dir          # a folder the package may write to
        self.log = log                    # log("message")
        self.firmware = firmware          # hub.device.FirmwareInfo or None while disconnected
        self._request = request

    def request_mode(self, mode):
        """Ask the keyboard to switch to a screen (e.g. when music starts). Returns True if sent."""
        return self._request("mode", mode)

    def show(self):
        """Bring this package's own screen up on the keyboard (e.g. when music starts)."""
        return self._request("mode", 5)


class ScreenPackage:
    fps = 2                # how often frame() is called while the package's screen is showing
    crisp_rows = 0         # top N rows drawn as pure black/white through the off-screen buffer
                           # (tear-free scrolling; REVIVER v4+). 0 = normal 16-grey drawing.
    nothing_off_sec = 0    # screen off after frame() has returned None this long (0 = never)

    def start(self, ctx):
        self.ctx = ctx

    def frame(self, now):
        """Return a 256x32 "L" image, or None when there's nothing to show."""
        return None

    def visible(self, shown):
        """Called when the package's screen comes into / goes out of view."""

    def stop(self):
        pass


class BackgroundPackage:
    def start(self, ctx):
        self.ctx = ctx

    def on_event(self, name, data=None):
        """Events: 'connected' (FirmwareInfo), 'disconnected', 'mode' (int), 'sleep', 'wake'."""

    def stop(self):
        pass
