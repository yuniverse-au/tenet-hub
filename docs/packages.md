# Writing a TENET Hub package

A package is a folder with a `package.json` and a Python file. To try one, copy the folder into `%LOCALAPPDATA%\TenetHub\packages\` and restart TENET Hub.

**Sharing it:** publish it from your own GitHub repo as a community package. **[docs/community-packages.md](community-packages.md)** walks through it. This repository's `packages/` folder only holds the official packages that come with TENET Hub; after changing one, run `python tools/build_index.py`.

## Screen package
Gets a screen of its own on the keyboard (REVIVER v21+; the user places it in the **Screens** tab). On older firmware it draws on the *Custom Text* screen, one screen package at a time. `ctx.show()` brings the package's screen up (for example when music starts).

`package.json`
```json
{
  "id": "hello",
  "name": "Hello",
  "version": "1.0.0",
  "type": "screen",
  "description": "Says hello.",
  "author": "you",
  "default_mode": 5,
  "requires_firmware": 6,
  "settings": [
    {"key": "text", "type": "str", "default": "Hello!", "label": "Text to show"}
  ]
}
```

`main.py`
```python
from PIL import Image, ImageDraw
from hub import api, render

class Package(api.ScreenPackage):
    fps = 1                                  # frame() calls per second while visible

    def start(self, ctx):
        self.ctx = ctx                       # ctx.settings, ctx.log(), ctx.data_dir, ctx.firmware
        self.font = render.load_font(14)

    def frame(self, now):
        img = Image.new("L", (api.WIDTH, api.HEIGHT), 0)       # 256x32, 0 = off, 255 = brightest
        d = ImageDraw.Draw(img)
        render.draw_text(d, (4, 8), self.ctx.settings["text"], self.font, 255)
        return img                           # or None: nothing to show

    def stop(self):
        pass
```

The host only sends the parts of the image that changed, so drawing the whole frame every time is fine. Other options on the class:
- **`crisp_rows = 16`:** the top 16 rows go through the firmware's off-screen buffer. They're pure black/white but update without tearing, which is good for scrolling text. Needs REVIVER v4+.
- **`nothing_off_sec = 30`:** turn the screen off once `frame()` has returned `None` for 30 s.
- **`visible(shown)`:** called when the package's screen comes into or goes out of view.
- **`ctx.request_mode(ctx.screen_mode)`:** switch the keyboard to your screen, e.g. when something happens.

The screen shows 16 grey levels. The host also handles burn-in shifting, idle-off and sleep, so packages don't need to.

## Background package
```json
{"id": "notify-sound", "name": "...", "version": "1.0.0", "type": "background", "requires_firmware": 0}
```
```python
from hub import api

class Package(api.BackgroundPackage):
    def start(self, ctx):
        self.ctx = ctx

    def on_event(self, name, data=None):
        # 'connected' (firmware info), 'disconnected', 'mode' (screen number), 'sleep', 'wake'
        self.ctx.log("event", name, data)
```

## Animation package
A GIF for the keyboard's image screen, with the adjustments it looks best with. It doesn't run any code: it shows up in the Image screen tab.
```json
{
  "id": "my-loop", "name": "My loop", "version": "1.0.0", "type": "animation",
  "file": "animation.gif",
  "gif_settings": {"fps_cap": 10, "brightness": 0, "contrast": 100, "gamma": 100, "invert": false,
                   "fit": "contain", "zoom": 100, "x": 0, "y": 0, "trim_start": 0, "trim_end": 0, "burst": 192}
}
```
Keep a package private by putting its folder in `%LOCALAPPDATA%\TenetHub\packages\` instead of this repository. The app finds it there but it is never published.

## Settings types
- `bool`, `int` (with `min`/`max`), `str`, and `choice` (with `choices`)
- Values arrive in `ctx.settings` with your defaults filled in.
- When the user changes them, the package is restarted.

## What you can import
Packages run inside TENET Hub's own Python. You can use:
- the common standard-library modules
- Pillow, numpy, soundcard
- the Windows media controls (`winrt.windows.media.control`)
- `hub.api` and `hub.render`

Extra `.py` files in your package folder can be imported relatively (`from . import helper`).

## Rules
- Don't talk to the keyboard yourself. The host owns the connection and sends strictly one packet per reply. Sending faster has jammed a keyboard before.
- Packages run as normal programs on the user's PC. TENET Hub installs from the official catalogue (this repository: review package code before merging it), from community sources the user added themselves, and from folders the user put in the packages folder. Official packages update automatically; community packages when the user clicks Update, or automatically if they opted in (Settings).
