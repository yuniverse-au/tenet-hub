# TENET Hub

Firmware updates and screen packages for the **Metakey TENET 70** keyboard (Hexcore PCB) and its 256×32 OLED screen.

- **Packages** draw live content on the keyboard's screen, like the song that's playing. Background packages can also react to PC events. New packages and updates are found automatically from this repository.
- **GIF screen**: store an animation on the keyboard and it plays by itself, even with the PC off. Before uploading you can set a frame-rate cap, brightness/contrast/gamma/invert, fit/zoom/position and trimming, with a live preview.
- **Firmware**: installs **REVIVER**, our custom firmware for the TENET 70. It adds the GIF screen, live drawing, tear-free scrolling, a custom boot logo and more. You can go back to the official firmware at any time.
- **Burn-in care**: the screen turns off while your PC sleeps or shuts down, or after you've been away for a while, and package screens shift by a pixel or two every few minutes.

## Install

1. Download `TenetHub.exe` from [Releases](https://github.com/yuniverse-au/tenet-hub/releases/latest) and run it. It sits in the system tray and starts when you log in (you can turn that off in Settings).
2. **Close Hexcore Link.** Only one program can talk to the keyboard at a time.
3. Open the **Firmware** tab and install the latest **REVIVER** firmware. It takes about a minute; keep the keyboard plugged in.
4. On the **Packages** tab, turn on the packages you want. Now Playing is included and shows on the keyboard's *Custom Text* screen. Press the keyboard's screen button to get there.

### GIF screen
1. Open the **GIF screen** tab and pick a GIF (or an animation package).
2. Adjust it while watching the preview. The line under the preview shows how much of the keyboard's storage it uses.
3. Click *Put it on the keyboard*. Uploads only rewrite frames that changed, and every frame is verified.
4. Press the screen button until you reach the GIF screen (it replaces the old Monitor screen).

How many frames fit depends on the keyboard's flash chip (about 190 on a 1 MB chip, 440 on 2 MB); the tab shows the real number. Big changes between frames (more than half the screen) play at around 5-8 fps; small changes play faster. *Smoothness vs typing* trades the two. *Save as a package* keeps a GIF and its adjustments as a local animation package.

### If something goes wrong
Unplug the keyboard, **hold Esc while plugging it back in** (it starts in update mode), then install the official firmware from the Firmware tab. Typing keeps working on every REVIVER version.

## How REVIVER updates work
REVIVER is built on top of Hexcore's official 3.0.7 firmware, and this repository contains **no Hexcore firmware**. A REVIVER release is only the bytes it changes (`firmware/<id>/patch.json`). TENET Hub:
1. downloads the official 3.0.7 firmware from Hexcore's own server (the same file Hexcore Link installs) and checks its SHA-256
2. applies REVIVER's changes, then checks the result's SHA-256 against the release
3. checks the keyboard model and layout over the update protocol, then flashes it

## Repository layout
| Path | What |
|---|---|
| `app/` | the app (`tenet_hub.py` + the `hub` package) |
| `packages/<id>/` | package sources (`package.json` + code) |
| `catalog/` | built package zips (made by `tools/build_index.py`) |
| `firmware/<id>/` | firmware releases: `release.json` + `patch.json` |
| `index.json` | the catalogue the app reads |
| `tools/` | `build_index.py`, `make_patch.py` |
| `tests/` | a fake keyboard and end-to-end tests |
| `docs/packages.md` | how to write a package |

## Publishing
- **Package change:** bump `version` in its `package.json`, run `python tools/build_index.py`, then commit and push. Users get it on their next check (every 6 hours, or *Check for updates*).
- **New firmware:** `python tools/make_patch.py official_3.07.bin patched.bin v14 --name "..." --patch-version 14 --notes "..."`, then `build_index.py`, then commit and push.
- **New app version:** bump `VERSION` in `app/hub/__init__.py`, run `build_index.py`, commit, then `git tag v1.0.1 && git push --tags`. GitHub Actions builds `TenetHub.exe` and attaches it to a Release, and the app shows users a download banner.

## Development
```
pip install -r requirements.txt
python app/tenet_hub.py          # window + tray
python tests/test_hub.py official_3.07.bin patched.bin
```
