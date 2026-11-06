"""Text helpers for packages: fonts with Japanese coverage, emoji, and fitting text to a width.

    from hub import render
    font = render.load_font(14)
    render.draw_text(draw, (x, y), "Title 🎵", font, fill=255)
    text = render.fit_text(draw, "A very long title", font, max_w=180)
"""
import os

from PIL import ImageFont

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\meiryo.ttc",
    r"C:\Windows\Fonts\YuGothM.ttc",
    r"C:\Windows\Fonts\msgothic.ttc",
    r"C:\Windows\Fonts\segoeui.ttf",
    r"C:\Windows\Fonts\arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]
EMOJI_FONT_PATHS = [r"C:\Windows\Fonts\seguiemj.ttf", r"C:\Windows\Fonts\seguisym.ttf"]
INVISIBLE = {0xFE0E, 0xFE0F, 0x200D, 0x20E3} | set(range(0x1F3FB, 0x1F400))  # joiners, skin tones
_fonts, _emoji_fonts = {}, {}


def load_font(size):
    if size not in _fonts:
        _fonts[size] = ImageFont.load_default()
        for path in FONT_CANDIDATES:
            if os.path.exists(path):
                try:
                    _fonts[size] = ImageFont.truetype(path, size)
                    break
                except OSError:
                    pass
    return _fonts[size]


def is_emoji(ch):
    cp = ord(ch)
    return (0x1F000 <= cp <= 0x1FAFF or 0x2600 <= cp <= 0x27BF or 0x2300 <= cp <= 0x23FF
            or 0x2B00 <= cp <= 0x2BFF or 0x2190 <= cp <= 0x21FF or cp in (0x00A9, 0x00AE, 0x2122))


def _load_emoji(size):
    for p in EMOJI_FONT_PATHS:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                pass
    return None


def emoji_font(font):
    """Emoji font sized so an emoji is about as tall as the text's capital letters."""
    key = font.size
    if key not in _emoji_fonts:
        _emoji_fonts[key] = None
        probe = _load_emoji(font.size)
        if probe:
            cap = font.getbbox("H")
            ink = probe.getbbox("\U0001F600")          # a typical full-size emoji
            cap_h, ink_h = cap[3] - cap[1], max(1, ink[3] - ink[1])
            size = max(6, round(font.size * cap_h * 1.15 / ink_h))
            _emoji_fonts[key] = _load_emoji(size)
    return _emoji_fonts[key]


# Japanese fonts (Meiryo etc.) draw these mid-line; use plain dots instead.
DOTS = str.maketrans({"\u2026": "...", "\u2025": "..", "\u22ef": "..."})


def runs(text, font):
    """Split text into (piece, font) runs; emoji use the emoji font, if there is one."""
    text = text.translate(DOTS)
    efont = emoji_font(font) if hasattr(font, "size") else None
    out = []
    for ch in text:
        if ord(ch) in INVISIBLE:
            continue
        f = efont if (efont and is_emoji(ch)) else font
        if out and out[-1][1] is f:
            out[-1][0] += ch
        else:
            out.append([ch, f])
    return out


def tlen(d, text, font):
    return sum(d.textlength(piece, font=f) for piece, f in runs(text, font))


def draw_fb(d, xy, text, font, fill):
    x, y = xy
    cap = font.getbbox("H")
    for piece, f in runs(text, font):
        if f is font:
            d.text((x, y), piece, font=f, fill=fill)
            x += d.textlength(piece, font=f)
            continue
        for ch in piece:                    # emoji: centre each on the capital letters
            ink = f.getbbox(ch)
            dy = ((cap[1] + cap[3]) - (ink[1] + ink[3])) / 2
            d.text((x, y + dy), ch, font=f, fill=fill)
            x += d.textlength(ch, font=f)


def fit_fb(d, text, font, max_w):
    if tlen(d, text, font) <= max_w:
        return text
    while text and tlen(d, text + "...", font) > max_w:
        text = text[:-1]
    return text.rstrip() + "..."


# friendlier names for package authors
text_width = tlen
draw_text = draw_fb
fit_text = fit_fb
