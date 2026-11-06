"""The window (tkinter) and tray icon (pystray)."""
import os, queue, subprocess, sys, threading, time, traceback, webbrowser
import tkinter as tk
import tkinter.simpledialog
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageDraw

from . import APP_NAME, VERSION, api, firmware, flasher, gif
from .device import Keyboard, enumerate_keyboards
from .config import data_dir, set_start_at_login

RECOVERY = ("If the keyboard ever stops responding after an update: unplug it, hold Esc while "
            "plugging it back in (it starts in update mode), then install the official firmware "
            "from this tab. Typing keeps working on every REVIVER version.")


def tray_image():
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((2, 14, 62, 50), 8, fill=(20, 20, 20, 255), outline=(200, 200, 200, 255), width=3)
    for i, x in enumerate(range(10, 56, 6)):
        h = (5, 9, 14, 8, 12, 6, 10, 13)[i % 8]
        d.rectangle((x, 44 - h * 2, x + 3, 44), fill=(220, 220, 220, 255))
    return img


class App:
    def __init__(self, cfg, reg, host, log, log_path, hidden=False):
        self.cfg, self.reg, self.host, self.log, self.log_path = cfg, reg, host, log, log_path
        self.q = queue.Queue()
        self.busy = False
        self.local_official = None
        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.geometry("900x660")
        self.root.minsize(700, 480)
        try:
            from PIL import ImageTk
            self._icon = ImageTk.PhotoImage(tray_image())
            self.root.iconphoto(True, self._icon)
        except Exception:
            pass
        self.root.protocol("WM_DELETE_WINDOW", self.hide)
        self.tray = None
        self._build()
        self._start_tray()
        if hidden and self.tray:
            self.root.withdraw()
        self.root.after(300, self._pump)
        threading.Thread(target=self._update_loop, name="updates", daemon=True).start()

    # --- plumbing --------------------------------------------------------------------------
    def run(self):
        self.root.mainloop()

    def ui(self, fn, *a):
        """Run fn on the UI thread."""
        self.q.put((fn, a))

    def _pump(self):
        try:
            while True:
                fn, a = self.q.get_nowait()
                try:
                    fn(*a)
                except Exception:
                    self.log(traceback.format_exc())
        except queue.Empty:
            pass
        self.status_var.set(self.host.status)
        now = time.time()
        if now - getattr(self, "_gif_info_at", 0) > 5 and not self.busy and self.host.fw and self.host.fw.at_least(gif.MIN_PATCH):
            self._gif_info_at = now
            try:
                with self.host.lock:
                    if self.host.kb:
                        self.gif_kbinfo = gif.info(self.host.kb) or self.gif_kbinfo
            except Exception:
                pass
        if not self.busy:
            self.fw_status.set(self._fw_text())
        self.root.after(500, self._pump)

    def show(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self.refresh_packages()

    def hide(self):
        if self.tray:
            self.root.withdraw()
        else:
            self.quit()

    def quit(self):
        if self.busy and not messagebox.askyesno(APP_NAME, "A firmware update is running. Quit anyway?"):
            return
        self.host.stop()
        if self.tray:
            self.tray.stop()
        self.root.destroy()

    def notify(self, title, msg):
        if self.tray:
            try:
                self.tray.notify(msg, title)
            except Exception:
                pass

    def _start_tray(self):
        try:
            import pystray
            menu = pystray.Menu(
                pystray.MenuItem("Open TENET Hub", lambda: self.ui(self.show), default=True),
                pystray.MenuItem("Check for updates", lambda: self.ui(self.check_updates)),
                pystray.MenuItem("Quit", lambda: self.ui(self.quit)))
            self.tray = pystray.Icon("tenet-hub", tray_image(), APP_NAME, menu)
            self.tray.run_detached()
        except Exception as e:
            self.log("no tray icon:", e)
            self.tray = None

    def _thread(self, fn, done=None):
        def run():
            try:
                r = fn()
                if done:
                    self.ui(done, r, None)
            except Exception as e:
                self.log(traceback.format_exc())
                if done:
                    self.ui(done, None, e)
        threading.Thread(target=run, daemon=True).start()

    # --- layout -------------------------------------------------------------------------------
    def _build(self):
        top = ttk.Frame(self.root, padding=(10, 8))
        top.pack(fill="x")
        ttk.Label(top, text=APP_NAME, font=("Segoe UI", 14, "bold")).pack(side="left")
        self.status_var = tk.StringVar()
        ttk.Label(top, textvariable=self.status_var, foreground="#555").pack(side="left", padx=14)
        self.banner = ttk.Label(self.root, foreground="#0a5", cursor="hand2")
        self.banner.bind("<Button-1>", lambda e: self._open_hub_release())
        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.nb = nb
        self._build_packages(nb)
        self._build_gif(nb)
        self._build_firmware(nb)
        self._build_settings(nb)

    # --- packages tab ---------------------------------------------------------------------
    def _build_packages(self, nb):
        f = ttk.Frame(nb, padding=8)
        nb.add(f, text="Packages")
        cols = ("installed", "latest", "screen", "status")
        tv = ttk.Treeview(f, columns=cols, show="tree headings", height=9, selectmode="browse")
        tv.heading("#0", text="Package")
        for c, w, label in zip(cols, (80, 80, 150, 260), ("Installed", "Latest", "Screen", "Status")):
            tv.heading(c, text=label)
            tv.column(c, width=w, anchor="w")
        tv.column("#0", width=180)
        tv.tag_configure("new", foreground="#0a5")
        tv.tag_configure("problem", foreground="#b40")
        tv.pack(fill="x")
        tv.bind("<<TreeviewSelect>>", lambda e: self._pkg_selected())
        self.pkg_tv = tv
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=6)
        self.b_install = ttk.Button(bar, text="Install", command=self.pkg_install)
        self.b_toggle = ttk.Button(bar, text="Turn on", command=self.pkg_toggle)
        self.b_settings = ttk.Button(bar, text="Settings...", command=self.pkg_settings)
        self.b_remove = ttk.Button(bar, text="Remove", command=self.pkg_remove)
        for b in (self.b_install, self.b_toggle, self.b_settings, self.b_remove):
            b.pack(side="left", padx=(0, 6))
        ttk.Button(bar, text="Check for updates", command=self.check_updates).pack(side="right")
        ttk.Button(bar, text="Open packages folder",
                   command=lambda: self._open(self.reg.root)).pack(side="right", padx=6)
        self.pkg_info = tk.Text(f, height=8, wrap="word", relief="flat", background=self.root.cget("bg"))
        self.pkg_info.pack(fill="both", expand=True)
        self.pkg_info.configure(state="disabled")
        self.refresh_packages()

    def _pkg_rows(self):
        av, inst = self.reg.available(), self.reg.installed
        new = set(self.reg.new_ids())
        upd = set(self.reg.updatable())
        for pid in sorted(set(av) | set(inst), key=lambda p: (inst.get(p) or av[p]).get("name", p).lower()):
            m, a = inst.get(pid), av.get(pid)
            info = m or a
            pc = self.cfg.pkg(pid)
            screen = ""
            if info.get("type") == "screen":
                mode = pc.get("mode") or info.get("default_mode")
                screen = api.PACKAGE_MODES.get(mode, "-")
            elif info.get("type") == "animation":
                screen = "GIF screen"
            else:
                screen = "(background)"
            if not m:
                status, tag = ("New - not installed" if pid in new else "Not installed"), ("new" if pid in new else "")
            elif m["type"] == "animation":
                status, tag = "Animation (use it from the GIF screen tab)", ""
            elif pid in self.host.problems:
                status, tag = self.host.problems[pid], "problem"
            elif pid in self.host.running:
                status, tag = "Running", ""
            elif pc.get("enabled"):
                status, tag = "Waiting for the keyboard", ""
            else:
                status, tag = "Off", ""
            if pid in upd:
                status += "  (update available)"
                tag = tag or "new"
            yield pid, info.get("name", pid), m["version"] if m else "", a["version"] if a else "", screen, status, tag

    def refresh_packages(self):
        tv = self.pkg_tv
        sel = tv.selection()
        tv.delete(*tv.get_children())
        for pid, name, iv, av, screen, status, tag in self._pkg_rows():
            tv.insert("", "end", iid=pid, text=name, values=(iv, av, screen, status), tags=(tag,) if tag else ())
        if sel and tv.exists(sel[0]):
            tv.selection_set(sel[0])
        self._pkg_selected()
        if hasattr(self, "gif_combo"):
            self.refresh_gif_sources()

    def _sel(self):
        s = self.pkg_tv.selection()
        return s[0] if s else None

    def _pkg_selected(self):
        pid = self._sel()
        m, a = (self.reg.installed.get(pid), self.reg.available().get(pid)) if pid else (None, None)
        info = m or a or {}
        self.b_install.configure(state="normal" if a and (not m or pid in self.reg.updatable()) else "disabled",
                                 text="Update" if m else "Install")
        anim = bool(info) and info.get("type") == "animation"
        self.b_toggle.configure(state="normal" if m and not anim else "disabled",
                                text="Turn off" if m and self.cfg.pkg(pid).get("enabled") else "Turn on")
        self.b_settings.configure(state="normal" if m and not anim else "disabled")
        self.b_remove.configure(state="normal" if m else "disabled")
        lines = []
        if info:
            lines.append(f"{info.get('name', pid)}  {info.get('version', '')}"
                         + (f"  by {info['author']}" if info.get("author") else ""))
            lines.append("")
            lines.append(info.get("description", ""))
            need = int(info.get("requires_firmware") or 0)
            if need:
                lines.append("")
                lines.append(f"Needs REVIVER v{need} or newer (Firmware tab).")
            if a and a.get("changelog") and (not m or pid in self.reg.updatable()):
                lines += ["", "What's new: " + a["changelog"]]
            if pid in self.reg.new_ids():
                self.reg.mark_seen([pid])
        self.pkg_info.configure(state="normal")
        self.pkg_info.delete("1.0", "end")
        self.pkg_info.insert("1.0", "\n".join(lines))
        self.pkg_info.configure(state="disabled")

    def pkg_install(self):
        pid = self._sel()
        if not pid:
            return
        name = (self.reg.available().get(pid) or {}).get("name", pid)
        first = pid not in self.reg.installed

        def done(m, err):
            if err:
                messagebox.showerror(APP_NAME, f"Couldn't install {name}:\n{err}")
            else:
                if first:
                    self.cfg.pkg(pid)["enabled"] = True
                    self.cfg.save()
                self.host.reload()
            self.refresh_packages()
        self._thread(lambda: self.reg.install(pid), done)

    def pkg_toggle(self):
        pid = self._sel()
        if not pid:
            return
        p = self.cfg.pkg(pid)
        p["enabled"] = not p.get("enabled")
        self.cfg.save()
        self.host.reload()
        self.root.after(800, self.refresh_packages)

    def pkg_remove(self):
        pid = self._sel()
        m = self.reg.installed.get(pid)
        if not m or not messagebox.askyesno(APP_NAME, f"Remove {m['name']}?"):
            return
        self.cfg.pkg(pid)["enabled"] = False
        self.cfg.save()
        self.host.reload()
        self.reg.remove(pid)
        self.refresh_packages()

    def pkg_settings(self):
        pid = self._sel()
        m = self.reg.installed.get(pid)
        if not m:
            return
        PackageSettings(self, m)


    # --- GIF screen tab ----------------------------------------------------------------------
    GIF_CONTROLS = [
        ("fps_cap", "Frame-rate cap (fps)", 1, 30),
        ("brightness", "Brightness", -100, 100),
        ("contrast", "Contrast (%)", 25, 300),
        ("gamma", "Gamma (%)", 30, 300),
        ("zoom", "Zoom (%)", 10, 400),
        ("x", "Move left / right", -256, 256),
        ("y", "Move up / down", -32, 32),
        ("trim_start", "Skip frames at the start", 0, 300),
        ("trim_end", "Skip frames at the end", 0, 300),
        ("burst", "Smoothness vs typing", 64, 1024),
    ]

    def _build_gif(self, nb):
        f = ttk.Frame(nb, padding=8)
        nb.add(f, text="GIF screen")
        top = ttk.Frame(f)
        top.pack(fill="x")
        ttk.Label(top, text="Animation:").pack(side="left")
        self.gif_src = tk.StringVar()
        self.gif_combo = ttk.Combobox(top, textvariable=self.gif_src, state="readonly", width=36)
        self.gif_combo.pack(side="left", padx=6)
        self.gif_combo.bind("<<ComboboxSelected>>", lambda e: self._gif_source_changed())
        ttk.Button(top, text="Open a GIF...", command=self.gif_open).pack(side="left")
        ttk.Button(top, text="Save as a package...", command=self.gif_save_package).pack(side="left", padx=6)
        self.gif_view = tk.Label(f, background="black")
        self.gif_view.pack(pady=8)
        self.gif_info = tk.StringVar(value="Pick an animation or open a GIF.")
        ttk.Label(f, textvariable=self.gif_info).pack(anchor="w")
        grid = ttk.Frame(f)
        grid.pack(fill="x", pady=6)
        self.gif_vars = {}
        for i, (key, label, lo, hi) in enumerate(self.GIF_CONTROLS):
            r, c = i // 2, (i % 2) * 3
            ttk.Label(grid, text=label).grid(row=r, column=c, sticky="w", padx=(0, 4))
            v = tk.IntVar(value=gif.DEFAULTS[key])
            val = ttk.Label(grid, width=5)
            sc = ttk.Scale(grid, from_=lo, to=hi, length=170, variable=v,
                           command=lambda x, v=v, val=val: (val.configure(text=str(int(float(x)))),
                                                            self._gif_changed()))
            sc.grid(row=r, column=c + 1, sticky="w")
            val.grid(row=r, column=c + 2, sticky="w", padx=(2, 16))
            val.configure(text=str(gif.DEFAULTS[key]))
            self.gif_vars[key] = (v, val)
        ttk.Label(f, text="Smoothness vs typing: higher plays big changes faster; lower keeps the keyboard "
                          "snappier while a busy GIF plays. The frame-rate cap is the most it will ever play.",
                  foreground="#555", wraplength=820, justify="left").pack(anchor="w")
        row = ttk.Frame(f)
        row.pack(fill="x", pady=(4, 0))
        ttk.Label(row, text="Fit:").pack(side="left")
        self.gif_fit = tk.StringVar(value=gif.FITS["contain"])
        cb = ttk.Combobox(row, textvariable=self.gif_fit, values=list(gif.FITS.values()), state="readonly", width=16)
        cb.pack(side="left", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self._gif_changed())
        self.gif_invert = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text="Invert", variable=self.gif_invert, command=self._gif_changed).pack(side="left", padx=10)
        ttk.Button(row, text="Reset adjustments", command=self.gif_reset).pack(side="left")
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(10, 2))
        self.b_gif_upload = ttk.Button(bar, text="Put it on the keyboard", command=self.gif_upload)
        self.b_gif_upload.pack(side="left")
        ttk.Button(bar, text="Show the GIF screen", command=lambda: self.host._request("mode", 6)).pack(side="left", padx=6)
        ttk.Button(bar, text="Remove from keyboard", command=self.gif_clear).pack(side="left")
        self.gif_progress = ttk.Progressbar(f, mode="determinate", maximum=1.0)
        self.gif_progress.pack(fill="x", pady=(6, 0))
        self.gif_msg = tk.StringVar()
        ttk.Label(f, textvariable=self.gif_msg, foreground="#555").pack(anchor="w")
        self.gif_frames, self.gif_gen, self.gif_job, self.gif_anim = [], 0, None, None
        self.gif_sources, self.gif_kbinfo = {}, None
        self.refresh_gif_sources()

    def refresh_gif_sources(self):
        src = {}
        for pid, m in sorted(self.reg.installed.items()):
            if m["type"] == "animation":
                src[f"{m['name']} (package)"] = ("pkg", pid)
        for p in self.cfg.get("gif_files", [])[-5:]:
            if os.path.exists(p):
                src[os.path.basename(p)] = ("file", p)
        self.gif_sources = src
        self.gif_combo.configure(values=list(src))
        cur = self.cfg.get("gif_source")
        if cur in src and not self.gif_src.get():
            self.gif_src.set(cur)
            self._gif_source_changed()

    def _gif_key(self):
        s = self.gif_sources.get(self.gif_src.get())
        return f"{s[0]}:{s[1]}" if s else None

    def _gif_source_changed(self):
        key = self._gif_key()
        if not key:
            return
        self.cfg.set("gif_source", self.gif_src.get())
        saved = (self.cfg.get("gif_settings") or {}).get(key)
        kind, ref = self.gif_sources[self.gif_src.get()]
        if saved is None and kind == "pkg":
            saved = self.reg.installed[ref].get("gif_settings")
        self._gif_apply(dict(gif.DEFAULTS, **(saved or {})))
        self._gif_changed(save=False)

    def _gif_apply(self, s):
        for key, (v, val) in self.gif_vars.items():
            v.set(int(s[key]))
            val.configure(text=str(int(s[key])))
        self.gif_fit.set(gif.FITS.get(s["fit"], gif.FITS["contain"]))
        self.gif_invert.set(bool(s["invert"]))

    def gif_settings(self):
        s = {k: int(v.get()) for k, (v, _) in self.gif_vars.items()}
        s["fit"] = next((k for k, n in gif.FITS.items() if n == self.gif_fit.get()), "contain")
        s["invert"] = bool(self.gif_invert.get())
        return s

    def gif_reset(self):
        self._gif_apply(dict(gif.DEFAULTS))
        self._gif_changed()

    def gif_open(self):
        p = filedialog.askopenfilename(title="Choose a GIF", filetypes=[("Animations", "*.gif *.webp *.png *.apng"), ("All files", "*.*")])
        if not p:
            return
        files = [x for x in self.cfg.get("gif_files", []) if x != p] + [p]
        self.cfg.set("gif_files", files[-5:])
        self.refresh_gif_sources()
        self.gif_src.set(os.path.basename(p))
        self._gif_source_changed()

    def _gif_changed(self, save=True):
        key = self._gif_key()
        if not key:
            return
        if save:
            allset = dict(self.cfg.get("gif_settings") or {})
            allset[key] = self.gif_settings()
            self.cfg.set("gif_settings", allset)
        if self.gif_job:
            self.root.after_cancel(self.gif_job)
        self.gif_job = self.root.after(250, self._gif_convert)

    def _gif_convert(self):
        self.gif_job = None
        kind, ref = self.gif_sources[self.gif_src.get()]
        path = self.reg.installed[ref].animation_path() if kind == "pkg" else ref
        s = self.gif_settings()
        self.gif_gen += 1
        gen = self.gif_gen
        self.gif_info.set("Working...")

        def done(frames, err):
            if gen != self.gif_gen:
                return
            if err:
                self.gif_frames = []
                self.gif_info.set(f"Can't use that file: {err}")
                return
            self.gif_frames = frames
            secs = sum(d for _, d in frames) / 100
            cap = self.gif_kbinfo["capacity"] if self.gif_kbinfo else None
            room = f"uses {len(frames)} of the keyboard's {cap} frames" if cap else \
                f"{len(frames)} frames"
            warn = "  - too many: lower the frame-rate cap or trim it" if cap and len(frames) > cap else ""
            self.gif_info.set(f"{len(frames)} frames, {secs:.1f} s loop, {room}{warn}")
            self._gif_play(0)
        self._thread(lambda: gif.convert(path, s), done)

    def _gif_play(self, i):
        if self.gif_anim:
            self.root.after_cancel(self.gif_anim)
            self.gif_anim = None
        if not self.gif_frames:
            return
        from PIL import ImageTk
        img, d = self.gif_frames[i % len(self.gif_frames)]
        self._gif_photo = ImageTk.PhotoImage(img.resize((768, 96), Image.NEAREST))
        self.gif_view.configure(image=self._gif_photo)
        self.gif_anim = self.root.after(d * 10, self._gif_play, i + 1)

    def _with_keyboard(self, fn, done):
        """Run fn(kb) with the keyboard to ourselves (packages pause meanwhile)."""
        def work():
            self.host.release_keyboard()
            time.sleep(0.3)
            kb = None
            try:
                kind, path = enumerate_keyboards()
                if kind != "app":
                    raise gif.GifError("Keyboard not found (plugged in? Hexcore Link closed?)")
                kb = Keyboard(path)
                return fn(kb)
            finally:
                if kb:
                    kb.close()
                self.host.resume()
        self.busy = True
        def finish(r, err):
            self.busy = False
            done(r, err)
        self._thread(work, finish)

    def gif_upload(self):
        if not self.gif_frames or self.busy:
            return
        frames, burst = list(self.gif_frames), self.gif_settings()["burst"]
        self.b_gif_upload.configure(state="disabled")

        def progress(frac, msg):
            self.ui(lambda: (self.gif_progress.configure(value=frac), self.gif_msg.set(msg)))

        def fn(kb):
            gif.upload(kb, frames, burst, progress)
            kb.set_mode(6)
            return gif.info(kb)

        def done(inf, err):
            self.b_gif_upload.configure(state="normal")
            if err:
                self.gif_msg.set(f"Not stored: {err}")
                messagebox.showerror(APP_NAME, str(err))
            else:
                self.gif_kbinfo = inf
                self.gif_msg.set(f"Stored {inf['frames']} frames. It plays on the GIF screen, even with the PC off.")
        self._with_keyboard(fn, done)

    def gif_clear(self):
        if self.busy or not messagebox.askyesno(APP_NAME, "Remove the animation stored on the keyboard?"):
            return
        self._with_keyboard(gif.clear, lambda r, err: self.gif_msg.set(
            f"Couldn't remove it: {err}" if err else "Removed. The GIF screen is blank now."))

    def gif_save_package(self):
        key = self._gif_key()
        if not key:
            return
        kind, ref = self.gif_sources[self.gif_src.get()]
        if kind == "pkg":
            messagebox.showinfo(APP_NAME, "That's already a package. Your adjustments are saved automatically.")
            return
        name = tk.simpledialog.askstring(APP_NAME, "Name for this animation package:", parent=self.root)
        if not name:
            return
        import json, re, shutil
        pid = re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-")[:40] or "animation"
        folder = os.path.join(self.reg.root, pid)
        if os.path.exists(folder) and not messagebox.askyesno(APP_NAME, f"Replace the package '{pid}'?"):
            return
        os.makedirs(folder, exist_ok=True)
        ext = os.path.splitext(ref)[1].lower() or ".gif"
        shutil.copy(ref, os.path.join(folder, "animation" + ext))
        json.dump({"id": pid, "name": name, "version": "1.0.0", "type": "animation",
                   "file": "animation" + ext, "description": "A local animation for the GIF screen.",
                   "gif_settings": self.gif_settings()},
                  open(os.path.join(folder, "package.json"), "w", encoding="utf-8"), indent=2)
        self.reg.scan()
        self.refresh_gif_sources()
        self.refresh_packages()
        self.gif_src.set(f"{name} (package)")
        self._gif_source_changed()

    # --- firmware tab ----------------------------------------------------------------------
    def _build_firmware(self, nb):
        f = ttk.Frame(nb, padding=8)
        nb.add(f, text="Firmware")
        self.fw_status = tk.StringVar()
        ttk.Label(f, textvariable=self.fw_status, font=("Segoe UI", 10, "bold")).pack(anchor="w")
        cols = ("version", "notes")
        tv = ttk.Treeview(f, columns=cols, show="tree headings", height=6, selectmode="browse")
        tv.heading("#0", text="Firmware")
        tv.heading("version", text="Patch")
        tv.heading("notes", text="What it adds")
        tv.column("#0", width=230)
        tv.column("version", width=60)
        tv.column("notes", width=460)
        tv.pack(fill="x", pady=6)
        tv.bind("<<TreeviewSelect>>", lambda e: self._fw_selected())
        self.fw_tv = tv
        self.fw_notes = tk.StringVar()
        ttk.Label(f, textvariable=self.fw_notes, wraplength=760, justify="left").pack(anchor="w", pady=(0, 6))
        bar = ttk.Frame(f)
        bar.pack(fill="x")
        self.b_flash = ttk.Button(bar, text="Install selected firmware", command=self.fw_install)
        self.b_flash.pack(side="left")
        ttk.Button(bar, text="Use a downloaded official firmware file...",
                   command=self.fw_pick_local).pack(side="left", padx=6)
        self.fw_progress = ttk.Progressbar(f, mode="determinate", maximum=1.0)
        self.fw_progress.pack(fill="x", pady=(10, 2))
        self.fw_msg = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.fw_msg).pack(anchor="w")
        ttk.Label(f, text=RECOVERY, wraplength=760, foreground="#555", justify="left").pack(anchor="w", pady=10)
        ttk.Label(f, text="REVIVER is built on your PC: TENET Hub downloads the official "
                          "firmware from Hexcore's own server, checks it, adds REVIVER's changes and checks the "
                          "result before installing anything.",
                  wraplength=760, foreground="#555", justify="left").pack(anchor="w")
        self.refresh_firmware()

    def _fw_text(self):
        fw = self.host.fw
        if fw is None:
            return self.host.status
        return f"Your keyboard: {fw}"

    def refresh_firmware(self):
        tv = self.fw_tv
        tv.delete(*tv.get_children())
        for r in self.reg.firmware_releases():
            tv.insert("", "end", iid="fw:" + r["id"], text=r.get("name", r["id"]),
                      values=(r.get("patch_version", ""), r.get("notes", "")))
        o = firmware.OFFICIAL_307
        tv.insert("", "end", iid="official", text=f"Official Hexcore firmware {o['version']}",
                  values=("-", "Removes REVIVER (packages that need it stop working)."))
        kids = tv.get_children()
        if kids:
            tv.selection_set(kids[0])
        self._fw_selected()

    def _fw_selected(self):
        s = self.fw_tv.selection()
        if not s:
            self.fw_notes.set("")
        elif s[0] == "official":
            self.fw_notes.set("Hexcore's own firmware, exactly as Hexcore Link installs it.")
        else:
            r = next((r for r in self.reg.firmware_releases() if "fw:" + r["id"] == s[0]), {})
            self.fw_notes.set(r.get("notes", ""))

    def fw_pick_local(self):
        p = filedialog.askopenfilename(title="Official TENET 70 firmware (apx_tenet_KEY_APP.bin)",
                                       filetypes=[("Firmware", "*.bin"), ("All files", "*.*")])
        if p:
            data = open(p, "rb").read()
            if firmware.sha256(data) != firmware.OFFICIAL_307["sha256"]:
                messagebox.showerror(APP_NAME, "That file isn't the official 3.0.7 firmware.")
                return
            self.local_official = p
            self.fw_msg.set("Using the official firmware file you picked (no download needed).")

    def fw_install(self):
        s = self.fw_tv.selection()
        if not s or self.busy:
            return
        sel = s[0]
        release = None if sel == "official" else next(
            r for r in self.reg.firmware_releases() if "fw:" + r["id"] == sel)
        name = "the official firmware" if release is None else release.get("name", release["id"])
        if not messagebox.askokcancel(
                APP_NAME, f"Install {name}?\n\nKeep the keyboard plugged in for about a minute. "
                          f"Close Hexcore Link first.\n\n{RECOVERY}"):
            return
        self.busy = True
        self.b_flash.configure(state="disabled")
        self.fw_progress["value"] = 0

        def progress(frac, msg):
            self.ui(self._fw_progress, frac, msg)

        def work():
            self.host.release_keyboard()
            time.sleep(0.5)
            try:
                progress(0, "Getting the firmware...")
                if release is None:
                    data = firmware.official(local_path=self.local_official)
                    allowed = {firmware.OFFICIAL_307["sha256"]}
                else:
                    data = firmware.build(release, self.reg.index.get("_url", self.cfg["index_url"]),
                                          self.local_official)
                    allowed = {release["result_sha256"]}
                flasher.flash(data, allowed, progress)
                time.sleep(4)
            finally:
                self.host.resume()

        def done(_, err):
            self.busy = False
            self.b_flash.configure(state="normal")
            if err:
                self.fw_msg.set(f"Not installed: {err}")
                messagebox.showerror(APP_NAME, f"Firmware not installed:\n{err}\n\n{RECOVERY}")
            else:
                self.fw_msg.set("Installed. The keyboard has restarted.")
                self.host.reload()
        self._thread(work, done)

    def _fw_progress(self, frac, msg):
        self.fw_progress["value"] = frac
        self.fw_msg.set(msg)
        self.fw_status.set(msg)

    # --- settings tab ---------------------------------------------------------------------
    def _build_settings(self, nb):
        f = ttk.Frame(nb, padding=10)
        nb.add(f, text="Settings")
        self.vars = {}

        def check(key, label):
            v = tk.BooleanVar(value=bool(self.cfg[key]))
            self.vars[key] = v
            ttk.Checkbutton(f, text=label, variable=v, command=self.save_settings).pack(anchor="w", pady=2)
        check("start_at_login", "Start TENET Hub when I log in")
        check("check_updates", "Check GitHub for new packages and updates")
        check("auto_update_packages", "Update packages automatically (firmware is never updated without asking)")
        check("off_when_pc_sleeps", "Turn the keyboard screen off when the PC sleeps or shuts down")
        check("off_with_monitor", "...and when Windows turns the monitor off")
        check("burn_in_shift", "Burn-in protection: nudge package screens sideways every 2 minutes")
        row = ttk.Frame(f)
        row.pack(anchor="w", pady=6)
        ttk.Label(row, text="Package screens turn off after").pack(side="left")
        self.idle_var = tk.IntVar(value=int(self.cfg["idle_off_min"]))
        ttk.Spinbox(row, from_=0, to=120, width=5, textvariable=self.idle_var,
                    command=self.save_settings).pack(side="left", padx=4)
        ttk.Label(row, text="minutes without keyboard/mouse use (0 = never)").pack(side="left")
        row = ttk.Frame(f)
        row.pack(anchor="w", pady=6)
        ttk.Label(row, text="Package screen brightness").pack(side="left")
        self.contrast_var = tk.IntVar(value=int(self.cfg["contrast"]))
        ttk.Scale(row, from_=8, to=255, length=220, variable=self.contrast_var,
                  command=lambda v: None).pack(side="left", padx=6)
        ttk.Button(row, text="Apply", command=self.save_settings).pack(side="left")
        row = ttk.Frame(f)
        row.pack(fill="x", pady=10)
        ttk.Label(row, text="Catalogue (index.json) URL:").pack(anchor="w")
        self.index_var = tk.StringVar(value=self.cfg["index_url"])
        ttk.Entry(row, textvariable=self.index_var).pack(fill="x")
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=10)
        ttk.Button(bar, text="Save", command=self.save_settings).pack(side="left")
        ttk.Button(bar, text="Open log", command=lambda: self._open(self.log_path)).pack(side="left", padx=6)
        ttk.Button(bar, text="Quit TENET Hub", command=self.quit).pack(side="right")
        ttk.Label(f, text=f"{APP_NAME} {VERSION}", foreground="#777").pack(anchor="w", side="bottom")

    def save_settings(self):
        for k, v in self.vars.items():
            self.cfg.data[k] = bool(v.get())
        try:
            self.cfg.data["idle_off_min"] = max(0, int(self.idle_var.get()))
        except (tk.TclError, ValueError):
            pass
        self.cfg.data["contrast"] = int(self.contrast_var.get())
        url = self.index_var.get().strip()
        changed_url = url and url != self.cfg["index_url"]
        if url:
            self.cfg.data["index_url"] = url
        self.cfg.save()
        try:
            set_start_at_login(self.cfg["start_at_login"])
        except Exception as e:
            self.log("couldn't change start-at-login:", e)
        self.host.watcher.with_display = self.cfg["off_with_monitor"]
        if self.host.screen:
            self.host.screen.contrast = self.cfg["contrast"]
            try:
                with self.host.lock:
                    self.host.kb.oled(0x81, self.cfg["contrast"])
            except Exception:
                pass
        if changed_url:
            self.check_updates()

    # --- updates ----------------------------------------------------------------------------
    def check_updates(self, quiet=False):
        def done(r, err):
            self.refresh_packages()
            self.refresh_firmware()
            self._hub_banner()
            if not quiet and self.reg.index_error:
                messagebox.showwarning(APP_NAME, f"Couldn't check for updates:\n{self.reg.index_error}")
        self._thread(self._do_check, done)

    def _do_check(self):
        new, upd = self.reg.refresh()
        if self.reg.index_error:
            return
        if upd and self.cfg["auto_update_packages"]:
            for pid in upd:
                try:
                    m = self.reg.install(pid)
                    self.notify("Package updated", f"{m['name']} {m['version']}")
                except Exception as e:
                    self.log(f"auto-update of {pid} failed:", e)
            self.host.reload()
        elif upd:
            self.notify("Updates available", ", ".join(self.reg.available()[p]["name"] for p in upd))
        notified = set(self.cfg.get("notified", []))
        fresh = [p for p in new if p not in notified]
        if fresh:
            self.notify("New packages", ", ".join(self.reg.available()[p]["name"] for p in fresh))
        fw = self.reg.firmware_releases()
        cur = self.host.fw
        if fw and cur and not cur.at_least(fw[0].get("patch_version", 0)) and ("fw:" + fw[0]["id"]) not in notified:
            self.notify("Firmware update", f"{fw[0].get('name', fw[0]['id'])} is available (Firmware tab).")
            fresh.append("fw:" + fw[0]["id"])
        if fresh:
            self.cfg.set("notified", sorted(notified | set(fresh)))

    def _update_loop(self):
        time.sleep(5)
        while True:
            if self.cfg["check_updates"]:
                self.ui(self.check_updates, True)
            time.sleep(max(1, self.cfg["check_every_hours"]) * 3600)

    def _hub_banner(self):
        h = self.reg.hub_update()
        if h:
            self.banner.configure(text=f"TENET Hub {h['version']} is available - click to download")
            self.banner.pack(fill="x", padx=10, before=self.nb)
        else:
            self.banner.pack_forget()

    def _open_hub_release(self):
        h = self.reg.hub_update()
        if h and h.get("url"):
            webbrowser.open(h["url"])

    def _open(self, path):
        try:
            if sys.platform == "win32":
                os.startfile(path)
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            messagebox.showerror(APP_NAME, str(e))


class PackageSettings(tk.Toplevel):
    """Settings dialog generated from the package's manifest."""

    def __init__(self, app, m):
        super().__init__(app.root)
        self.app, self.m = app, m
        self.title(f"{m['name']} settings")
        self.resizable(False, False)
        pc = app.cfg.pkg(m["id"])
        cur = dict(m.defaults())
        cur.update(pc.get("settings") or {})
        self.vars = {}
        body = ttk.Frame(self, padding=12)
        body.pack(fill="both")
        r = 0
        if m["type"] == "screen":
            ttk.Label(body, text="Show on").grid(row=r, column=0, sticky="w", pady=3)
            names = list(api.PACKAGE_MODES.values())
            mode = pc.get("mode") or m.get("default_mode")
            self.mode_var = tk.StringVar(value=api.PACKAGE_MODES.get(mode, names[0]))
            ttk.Combobox(body, textvariable=self.mode_var, values=names, state="readonly",
                         width=24).grid(row=r, column=1, sticky="w")
            r += 1
        for s in m["settings"]:
            k, typ, label = s["key"], s.get("type", "str"), s.get("label", s["key"])
            if typ == "bool":
                v = tk.BooleanVar(value=bool(cur.get(k)))
                ttk.Checkbutton(body, text=label, variable=v).grid(row=r, column=0, columnspan=2, sticky="w", pady=3)
            else:
                ttk.Label(body, text=label).grid(row=r, column=0, sticky="w", pady=3)
                if typ == "int":
                    v = tk.IntVar(value=int(cur.get(k) or 0))
                    ttk.Spinbox(body, from_=s.get("min", 0), to=s.get("max", 1_000_000), width=8,
                                textvariable=v).grid(row=r, column=1, sticky="w")
                elif typ == "choice":
                    v = tk.StringVar(value=str(cur.get(k)))
                    ttk.Combobox(body, textvariable=v, values=s.get("choices", []), state="readonly",
                                 width=24).grid(row=r, column=1, sticky="w")
                else:
                    v = tk.StringVar(value=str(cur.get(k) or ""))
                    ttk.Entry(body, textvariable=v, width=28).grid(row=r, column=1, sticky="w")
            self.vars[k] = (v, s)
            r += 1
        bar = ttk.Frame(body)
        bar.grid(row=r, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(bar, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(bar, text="Save", command=self.save).pack(side="right", padx=6)

    def save(self):
        pc = self.app.cfg.pkg(self.m["id"])
        out = {}
        for k, (v, s) in self.vars.items():
            try:
                val = v.get()
            except tk.TclError:
                val = s.get("default")
            if s.get("type") == "int":
                val = max(s.get("min", val), min(s.get("max", val), int(val)))
            out[k] = val
        pc["settings"] = out
        if self.m["type"] == "screen":
            pc["mode"] = next(k for k, n in api.PACKAGE_MODES.items() if n == self.mode_var.get())
        self.app.cfg.save()
        self.app.host.reload()
        self.app.refresh_packages()
        self.destroy()
