# TENET Hub

Firmware updates and screen packages for the **Metakey TENET 70** keyboard (Hexcore PCB) and its 256×32 OLED screen.

- **Packages** draw live content on the keyboard's screen, like the song that's playing. Background packages can also react to PC events. Official packages and their updates come from this repository; **community packages** come from other people's GitHub repos that you add (Packages tab → *Community sources…*). Making your own: [docs/community-packages.md](docs/community-packages.md).
- **Screens**: turn the keyboard's built-in screens on or off, put them in any order, and give every screen package a screen of its own.
- **Image screen**: store an image or an animation on the keyboard and it shows by itself, even with the PC off. Before uploading you can set a frame-rate cap, brightness/contrast/gamma/invert, fit/zoom/position and trimming, with a live preview.
- **Boot logo**: the keyboard shows a TENET 70 logo when it powers on. Put your own image there instead, or go back to the TENET 70 logo, from the Boot logo tab.
- **Firmware**: installs **REVIVER**, our custom firmware for the TENET 70. It adds the image screen, a changeable boot logo, live drawing, tear-free scrolling and more. You can go back to the official firmware at any time.
- **Lighting**: every key one colour, or all off (Lighting tab). Works on any firmware.
- **Burn-in care**: the screen turns off while your PC sleeps or shuts down, or after you've been away for a while, and package screens shift by a pixel or two every few minutes.

## Install

1. Download `TenetHub.exe` from [Releases](https://github.com/yuniverse-au/tenet-hub/releases/latest) and run it. It sits in the system tray and starts when you log in (you can turn that off in Settings).
2. **Close Hexcore Link.** Only one program can talk to the keyboard at a time.
3. Open the **Firmware** tab and install the latest **REVIVER** firmware. It takes about a minute; keep the keyboard plugged in.
4. On the **Packages** tab, turn on the packages you want. Now Playing is included. Each screen package gets a screen of its own on the keyboard; press the keyboard's screen button to get there.

### Screens
The **Screens** tab sets which screens the keyboard's screen button goes through, and in what order. Built-in screens (Dashboard, KPM, clock, the Hexcore Link logo, image screen, typing animation) can be turned on or off and moved; every screen package gets a screen of its own in the same list, up to 24 screens in all. The list is stored on the keyboard, so the button works the same with the PC off. Package screens only come up while TENET Hub is running, so the keyboard never stops on a blank screen. Needs REVIVER v21 or newer.

### PC stats
The **PC stats** screen (built in, REVIVER v24+) shows three numbers plus the date and day. In the **PC stats** tab, pick what each number measures: CPU, GPU or disk usage or temperature, or memory usage. On REVIVER v25+ the label under each number follows your choice (CPU, GPU, RAM, DISK). Usage and memory come from Windows.

Temperatures: Windows only lets programs read CPU and most disk temperatures through a signed driver, as administrator. Click **Set up** under *Temperature sensors* in the PC stats tab and approve the one Windows prompt: TENET Hub installs the [PawnIO](https://pawnio.eu/) driver if it isn't there and registers a small helper (`TenetHub.exe --sensors`, a scheduled task) that starts as administrator at login and reads the sensors with the bundled [LibreHardwareMonitorLib](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor) (MPL 2.0). **Remove** undoes it. A running LibreHardwareMonitor with its web server on also works, and NVIDIA GPU temperatures work without either. TENET Hub feeds the screen, so it's only in the button cycle while TENET Hub runs.

### Clock and Dashboard
TENET Hub sets the keyboard's clock from your PC (the same thing Hexcore Link does), so the **Dashboard** shows the time, date and day and the **Clock** screen shows the right time. It syncs when the keyboard connects, after the PC wakes and once a minute. Choose 12- or 24-hour (or *Same as Windows*) in Settings. This works on the official firmware too.

### Image screen
1. Open the **Image screen** tab and open an image (PNG, JPG, BMP...) or a GIF, or pick an animation package.
2. Adjust it while watching the preview. The line under the preview shows how much of the keyboard's storage it uses.
3. Click *Put it on the keyboard*. Uploads only rewrite frames that changed, and every frame is verified.
4. Press the screen button until you reach the image screen.

How many frames fit depends on the keyboard's flash chip (about 190 on a 1 MB chip, 440 on 2 MB); the tab shows the real number. Big changes between frames (more than half the screen) play at around 5-8 fps; small changes play faster. *Smoothness vs typing* trades the two.

### Boot logo
1. Open the **Boot logo** tab. The preview shows the built-in TENET 70 logo.
2. Open an image and adjust it (fit, zoom, position, brightness/contrast/gamma, invert). For a GIF, the first frame is used.
3. Click *Put it on the keyboard*. You'll see it the next time the keyboard powers on. *Restore the TENET 70 logo* goes back to the built-in one.

The boot logo has its own 8 KB of the keyboard's flash chip, so it never uses image screen frames. It needs REVIVER v20 or newer.

### Updates
TENET Hub checks GitHub for new packages, firmware and TENET Hub versions every few hours (Settings → *Check GitHub for new packages and updates automatically*). Opening the **Firmware** tab checks again and tells you if a newer REVIVER is available for your keyboard. Both the Firmware and Settings tabs also have a *Check for updates* button. Official packages can update themselves; community packages update when you click Update (or automatically, if you turn that on in Settings); firmware never installs without you clicking Install.

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
| `docs/community-packages.md` | how to publish packages from your own repo |

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
