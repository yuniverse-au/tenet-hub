**TENET Hub 1.2.0**: animation packages get stored screens of their own, and they play with the PC off.

Download **TenetHub.exe** below and run it (Windows 10 or 11, no installer). If you already have TENET Hub, replace the old TenetHub.exe with this one. If you set up the built-in temperature sensors and the new exe is in a different place, click **Set up again** in the PC stats tab.

## New

- **Stored animations (needs REVIVER v26, in the Firmware tab):** turn an animation package on in the Packages tab and TENET Hub stores it on the keyboard, with progress shown in that tab. It gets its own screen, which you place in the Screens tab, and it plays even with the PC off. Turn the package off to free its space.
- **Room for 7 of them, next to your Image screen:** they all share the keyboard's frame space (about 180 frames on a 1 MB chip). If an image won't fit, TENET Hub tells you how much room is left. Your current Image screen stays as it is.
- **New home:** TENET Hub now lives at [yuuusername/tenet-hub](https://github.com/yuuusername/tenet-hub). This version updates its saved catalogue address by itself.

## Fixed

- The app no longer stalls waiting on the keyboard while an image is being stored.
- Now Playing 1.0.2: "Nothing playing" is no longer cut off at the top.

## Everything from earlier versions

- Lighting tab: one colour or off, with a brightness slider
- Optional automatic updates for community packages
- Now Playing, community packages from other people's GitHub repos ([how to make your own](https://github.com/yuuusername/tenet-hub/blob/main/docs/community-packages.md))
- Screens list, PC stats with built-in temperature sensors, image screen with rotation, boot logo, keyboard clock sync, burn-in care

If the keyboard ever stops responding after a firmware update: unplug it, hold **Esc** while plugging it back in (it starts in update mode), then install the official firmware from the Firmware tab. Typing keeps working on every REVIVER version.
