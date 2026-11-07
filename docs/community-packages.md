# Making community packages

Anyone can make packages for TENET Hub and share them from their own GitHub repo. People add your repo in TENET Hub (**Packages tab → Community sources… → paste the link**) and your packages show up next to the official ones, marked with your repo's name.

This repository only holds the official packages that come with TENET Hub (like Now Playing). Community packages live in their authors' repos; nothing needs to be sent here. If a community package becomes popular and should ship with TENET Hub, open an issue about making it official.

## 1. Write the package

A package is a folder with a `package.json` and some Python. **[docs/packages.md](packages.md)** explains the three kinds (screen, background, animation), the `package.json` fields, settings, the drawing API and what you can import. Start there.

Pick an `id` that's unlikely to clash with anyone else's, for example `yourname-weather` rather than `weather`. Ids are lowercase letters, digits, `-` and `_`. A community package can't use the id of an official package (TENET Hub ignores it).

## 2. Try it on your own keyboard

Copy the package folder into `%LOCALAPPDATA%\TenetHub\packages\` and restart TENET Hub. It shows in the Packages tab with the source **Local**. Turn it on, and check `hub.log` in `%LOCALAPPDATA%\TenetHub\` for errors (the Packages tab also shows a package's error as its status).

Remove that copy (select it, **Remove**) before installing your published version: TENET Hub won't install a package over one that came from somewhere else.

## 3. Set up your repo

Make a public GitHub repo with your packages in a `packages/` folder:

```
my-tenet-packages/
  packages/
    yourname-weather/
      package.json
      main.py
    yourname-cpu-graph/
      package.json
      main.py
  index.json        <- made by the build step below
  catalog/          <- made by the build step below
  README.md
```

One repo can hold any number of packages.

## 4. Build the catalogue

TENET Hub reads `index.json` (the list of your packages, their versions and checksums) and downloads each package as a zip from `catalog/`. Make both with the same tool the official catalogue uses. You need Python 3.10+ and a copy of this repo:

```
git clone https://github.com/yuuusername/tenet-hub
python tenet-hub/tools/build_index.py --community --root my-tenet-packages --name "Your name's packages"
```

It checks every `package.json`, zips each package into `catalog/<id>-<version>.zip` and writes `index.json`. Commit and push all three: `packages/`, `catalog/` and `index.json`. Run it again every time you change a package.

Always rebuild before pushing. TENET Hub checks the SHA-256 of every download against `index.json` and refuses anything that doesn't match, so a package changed without rebuilding won't install.

## 5. Share it

Give people your repo link, for example `https://github.com/yourname/my-tenet-packages`. In TENET Hub they open **Packages → Community sources…**, paste it and click **Add**. TENET Hub reads `index.json` from your repo's default branch. Other forms that work: a link to a branch (`…/tree/dev`) or a folder in the repo, or a direct link to an `index.json` anywhere on the web.

Say in your README what each package does, what it needs (for example "needs REVIVER v21 or newer", an API key, or another program) and what data it reads or sends.

## 6. Release updates

1. Change the package and **raise its `version`** in `package.json` (`1.0.0` → `1.0.1`). TENET Hub only offers an update when the version goes up.
2. Optionally add a `"changelog": "What changed"` line to `package.json`. TENET Hub shows it next to the Update button.
3. Rebuild the catalogue and push.

People get an "Updates available" notification within a few hours (TENET Hub checks at start-up and every few hours), and the update installs when they click **Update**. People who turned on *Update community packages automatically* in Settings (it's off by default) get it without clicking.

A package installed from your repo only ever updates from your repo. Nobody else's repo can replace it, even with the same id.

## Rules

- **Your package runs as a program on other people's PCs, with their permissions.** TENET Hub doesn't check community packages, and tells people so before they install one. Don't break that trust: no hidden downloads or code you fetch and run later, no collecting data you don't say you collect, nothing that slows the PC down.
- **Don't talk to the keyboard yourself.** TENET Hub owns the USB connection and sends exactly one packet per reply. Sending your own has jammed a keyboard before. Draw through `frame()` and use what `ctx` offers.
- **Keep it light.** Screen packages run inside TENET Hub; heavy work in `frame()` slows every screen. Do slow things (web requests and so on) on your own thread and keep the last result for `frame()`.
- **Clean up in `stop()`.** Your package is stopped and started again when its settings change, when it's updated and when the keyboard reconnects.
- Community catalogues can only offer packages. Firmware and TENET Hub updates only come from the official catalogue; TENET Hub ignores them anywhere else.

## Checklist before you push

- [ ] Works when copied into `%LOCALAPPDATA%\TenetHub\packages\` (and that copy is removed again)
- [ ] Unique `id`, `name`, `author`, a clear `description`, and `requires_firmware` set to the lowest REVIVER version it needs
- [ ] `version` raised since the last release
- [ ] `build_index.py --community` run, and `packages/`, `catalog/` and `index.json` all committed
- [ ] README says what it does and what it needs
