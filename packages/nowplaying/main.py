"""Now Playing: what's playing on the PC, with live progress and an audio spectrum."""
import asyncio, datetime, threading, time

from PIL import Image, ImageDraw

from hub import api, render

W, H = api.WIDTH, api.HEIGHT
X0 = 20                       # text starts right of the music note
BAND_ROWS = 16                # title rows go through the tear-free buffer
TICK = 0.15                   # scroll step interval
SCROLL_STEP, SCROLL_PAUSE = 2, 2.0
SPEC_BARS = 12
SPEC_BAR_W, SPEC_GAP = 3, 1
SPEC_W = SPEC_BARS * (SPEC_BAR_W + SPEC_GAP) - SPEC_GAP
SPEC_TOP, SPEC_BOTTOM = 1, 14


# --- what's playing (Windows media controls) --------------------------------------------------
def _media_manager():
    try:
        from winrt.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as M)
    except ImportError:
        from winsdk.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as M)
    return M


async def _get_track():
    mgr = await _media_manager().request_async()
    session = mgr.get_current_session()
    if session is None:
        return None
    props = await session.try_get_media_properties_async()
    title = (props.title or "").strip()
    artist = (props.artist or "").strip()
    if not title:
        return None
    info = dict(track=(title, artist), pos=None, dur=None, playing=False)
    try:
        playing = int(session.get_playback_info().playback_status) == 4
        tl = session.get_timeline_properties()
        pos = tl.position.total_seconds()
        dur = (tl.end_time - tl.start_time).total_seconds()
        if playing and tl.last_updated_time:      # players report the position only now and then
            now = datetime.datetime.now(datetime.timezone.utc)
            pos += max(0.0, (now - tl.last_updated_time).total_seconds())
        if dur > 0:
            info.update(pos=min(pos, dur), dur=dur, playing=playing)
    except Exception:
        pass
    return info


class Spectrum:  # noqa: copied from nowplaying_live.py
    """Captures what Windows is playing (loopback of the default output device) and
    keeps SPEC_BARS bar heights (0..1) up to date in a background thread."""

    def __init__(self):
        self.bars = [0.0] * SPEC_BARS
        self.ok = False
        self.error = None
        import threading
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            import warnings
            import numpy as np
            import soundcard as sc
            warnings.filterwarnings("ignore")      # soundcard warns about discontinuities
            rate, n = 48000, 2048
            freqs = np.fft.rfftfreq(n, 1 / rate)
            edges = np.geomspace(50, 16000, SPEC_BARS + 1)          # log-spaced bands
            bins = [(freqs >= lo) & (freqs < hi) for lo, hi in zip(edges[:-1], edges[1:])]
            window = np.hanning(n)
            peak = 1e-3
            heights = np.zeros(SPEC_BARS)
            while True:                                  # re-open if the device changes
                spk = sc.default_speaker()
                mic = sc.get_microphone(id=str(spk.name), include_loopback=True)
                with mic.recorder(samplerate=rate, blocksize=n // 2) as rec:
                    self.ok = True
                    while sc.default_speaker().name == spk.name:
                        data = rec.record(numframes=n)
                        mono = data.mean(axis=1) if data.ndim > 1 else data
                        if len(mono) < n:
                            continue
                        mag = np.abs(np.fft.rfft(mono[-n:] * window))
                        level = np.array([mag[b].mean() if b.any() else 0.0 for b in bins])
                        level = np.log10(1 + 9 * level)                 # softer, roughly dB-like
                        peak = max(peak * 0.995, level.max(), 1e-3)     # slow auto-gain
                        target = np.clip(level / peak, 0, 1)
                        heights = np.maximum(target, heights - 0.08)    # quick rise, smooth fall
                        self.bars = heights.tolist()
        except Exception as e:
            self.ok = False
            self.error = e




def mmss(s, hours=False):
    """m:ss, or h:mm:ss once it's an hour or more (or when `hours`, so both sides of
    "pos / dur" match on a long track)."""
    s = int(s)
    h, m, sec = s // 3600, s // 60 % 60, s % 60
    if h or hours:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{s // 60}:{sec:02d}"


class Package(api.ScreenPackage):
    crisp_rows = BAND_ROWS
    nothing_off_sec = 1           # frame() returns None once nothing has played for a while

    def start(self, ctx):
        self.ctx = ctx
        self.info, self.polled_at = None, 0.0
        self.track, self.track_since = None, time.time()
        self.last_playing = time.time()
        self.title_font, self.small_font = render.load_font(14), render.load_font(10)
        self.spec = Spectrum() if ctx.settings.get("spectrum", True) else None
        self.spec_reported = False
        self.stopping = threading.Event()
        threading.Thread(target=self._poll, name="nowplaying-media", daemon=True).start()

    def stop(self):
        self.stopping.set()

    def _poll(self):
        while not self.stopping.is_set():
            try:
                info = asyncio.run(_get_track())
                self._last_err = None
            except Exception as e:
                info = None
                if str(e) != getattr(self, "_last_err", None):     # log each new problem once
                    self.ctx.log("couldn't read media info:", e)
                    self._last_err = str(e)
            self.info, self.polled_at = info, time.time()
            self.stopping.wait(1.0)

    def _crisp(self):
        f = self.ctx.firmware
        return bool(f and f.at_least(4))

    def frame(self, now):
        s = self.ctx.settings
        info = self.info
        new_track = info["track"] if info else None
        if new_track != self.track:
            if new_track and not self.track and s.get("auto_switch") and self.ctx.screen_mode:
                self.ctx.request_mode(self.ctx.screen_mode)
            self.track, self.track_since = new_track, now
            if new_track:
                self.ctx.log("now playing:", " - ".join(x for x in new_track if x))
        if info:
            self.last_playing = now
        limit = s.get("nothing_off_sec", 60)
        if not info and limit and now - self.last_playing > limit:
            return None
        pos = None
        if info and info["pos"] is not None:
            pos = info["pos"] + (now - self.polled_at if info["playing"] else 0)
            pos = min(pos, info["dur"])
        held = now - self.track_since - SCROLL_PAUSE
        offset = int(max(0.0, held) / TICK) * SCROLL_STEP
        spec_on = bool(self.spec and self.spec.ok and self._crisp())
        if self.spec and self.spec.error and not self.spec_reported:
            self.ctx.log("spectrum off:", self.spec.error)
            self.spec_reported = True
        img, scrolling = self.render(info, pos, offset, self.spec.bars if spec_on else None, spec_on)
        playing = bool(info and info.get("playing"))
        self.fps = s.get("spectrum_fps", 12) if (spec_on and playing) else \
            (1 / TICK if scrolling else 2)
        return img

    # --- drawing ----------------------------------------------------------------------------
    def draw_title(self, img, title, offset, spec_on):
        max_w = W - X0 - 2 - ((SPEC_W + 5) if spec_on else 0)
        strip = Image.new("L", (max_w, BAND_ROWS), 0)
        d = ImageDraw.Draw(strip)
        if self._crisp():
            d.fontmode = "1"          # the tear-free buffer is 1 bit per pixel
        tw = render.text_width(d, title, self.title_font)
        if tw <= max_w:
            render.draw_text(d, (0, -2), title, self.title_font, 255)
        else:
            period = int(tw) + 40
            x = -(offset % period)
            render.draw_text(d, (x, -2), title, self.title_font, 255)
            render.draw_text(d, (x + period, -2), title, self.title_font, 255)
        img.paste(strip, (X0, 0))
        return tw > max_w

    def render(self, info, pos, offset, bars, spec_on):
        img = Image.new("L", (W, H), 0)
        d = ImageDraw.Draw(img)
        d.ellipse((2, 19, 9, 26), fill=255)          # music note
        d.line((9, 22, 9, 4), fill=255, width=2)
        d.line((9, 4, 15, 7), fill=255, width=2)
        if not info:
            d.text((X0, 8), "Nothing playing", font=self.title_font, fill=90)
            return img, False
        title, artist = info["track"]
        scrolling = self.draw_title(img, title, offset, spec_on)
        if bars and info.get("playing"):
            x = W - 2 - SPEC_W
            full = SPEC_BOTTOM - SPEC_TOP + 1
            for h in bars:
                n = round(h * full)
                if n:
                    d.rectangle((x, SPEC_BOTTOM - n + 1, x + SPEC_BAR_W - 1, SPEC_BOTTOM), fill=255)
                x += SPEC_BAR_W + SPEC_GAP
        dur = info["dur"]
        when = ""
        if pos is not None and dur:
            long = dur >= 3600
            when = f"{mmss(pos, long)} / {mmss(dur, long)}"
            if not info["playing"]:
                when = "paused  " + when
        tw = d.textlength(when, font=self.small_font)
        if when:
            d.text((W - 2 - tw, 15), when, font=self.small_font, fill=200)
        if artist:
            render.draw_text(d, (X0, 15), render.fit_text(d, artist, self.small_font, W - X0 - tw - 10),
                             self.small_font, 130)
        if pos is not None and dur:
            d.rectangle((X0, 29, W - 2, 30), fill=40)
            fill_to = X0 + int((W - 2 - X0) * min(pos / dur, 1.0))
            if fill_to > X0:
                d.rectangle((X0, 28, fill_to, 31), fill=255)
        return img, scrolling
