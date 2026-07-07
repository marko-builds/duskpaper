#!/usr/bin/env python3
"""
terminal.py — "code rain": falling columns of monospace glyphs, same interface
as aurora.Sky. A CLI-first take on the Matrix digital-rain look, tinted to the
kit's three palettes (aurora=green, ember=amber, ice=cyan) so it stays coherent
with the rest of the scenery. Drop in via the scene registry (`--scene terminal`).

How it reads as *code* and not a static texture: each grid cell shows a glyph
that flickers between a few baked variants, and a bright "head" falls down each
column trailing a fading tail. Both motions are quantized to the loop so the clip
is seamless.

Seamless looping (the load-bearing trick, mirrors rain.py / space.py):
  * Drop heads fall a whole number of cell-rows per loop: head = (h0 + v*t) % R
    with v = k*R/period, integer k  ->  head returns to h0 at t = period.
  * Tail brightness uses the *modular* distance d = (head - r) % R, so a tail
    crossing the top/bottom seam wraps instead of popping.
  * Glyph flicker advances an *integer* number of variant-cycles per loop, so
    every cell shows the same glyph at t and t+period.
  Bloom + scanlines are stateless transforms of those already-seamless buffers,
  so they don't reintroduce a seam. Verified frame(0) == frame(period).

Renderer contract: __init__(cols, rows, calm, loop_period, meteors, meteor_seed,
palette) and frame(t) -> (H, W, 3) uint8, with H = rows*2, W = cols.
"""

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .aurora import PALETTES

# ── glyph set: code/terminal characters, not anime katakana ───────────────────
CHARSET = ("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
           "{}[]()<>/\\|;:=+-*&^%$#@!?.~")

# Font candidates, best monospace first. Falls back to PIL's default if none.
FONTS = [
    "/usr/share/fonts/TTF/JetBrainsMonoNerdFontMono-Bold.ttf",
    "/usr/share/fonts/TTF/JetBrainsMonoNerdFont-ExtraBold.ttf",
    "/usr/share/fonts/liberation/LiberationMono-Bold.ttf",
]

# Per-palette head (bright, near-white) and tail (the column's settled colour).
COLORS = {
    "aurora": {"head": (205, 255, 215), "tail": (30, 200, 95)},
    "ember":  {"head": (255, 222, 185), "tail": (225, 85, 40)},
    "ice":    {"head": (210, 240, 255), "tail": (60, 170, 240)},
}

G = 5          # number of baked glyph variants a cell flickers between
CELL_W = 9.0   # target cell width in native px (sets column count)
CELL_AR = 1.9  # cell height / width


def _load_font(size):
    for path in FONTS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


class Terminal:
    """Falling code rain. Mirrors aurora.Sky's constructor + frame(t) contract."""

    def __init__(self, cols, rows, calm=False, loop_period=None,
                 meteors=0, meteor_seed=7, palette="aurora"):
        # meteors / meteor_seed accepted for signature-compatibility, unused.
        self.W = cols
        self.H = rows * 2
        W, H = self.W, self.H
        self.period = loop_period or 12.0
        pal = PALETTES[palette]
        col = COLORS[palette]
        self.head = np.array(col["head"], float)
        self.tail = np.array(col["tail"], float)
        # near-black background, faintly tinted by the palette sky.
        self.bg = (np.array(pal["sky_base"], float) * 0.5)[None, None, :]

        # ── grid ──
        self.nc = max(8, round(W / CELL_W))                 # columns of text
        self.nr = max(8, round(H / (CELL_W * CELL_AR)))     # rows of text
        nr, nc = self.nr, self.nc
        cw, ch = W / nc, H / nr                             # float cell size

        rng = np.random.default_rng(20)

        # ── bake G full-screen glyph layers at NATIVE res (antialiased) ──
        # Each layer assigns every cell an independent random glyph; the cell
        # flickers between layers over time. Drawing at native res (not block
        # upscaling tiny bitmaps) keeps the characters crisp.
        font = _load_font(max(6, int(ch * 0.92)))
        layers = np.empty((G, H, W), np.float32)
        for g in range(G):
            im = Image.new("L", (W, H), 0)
            d = ImageDraw.Draw(im)
            for r in range(nr):
                for c in range(nc):
                    glyph = CHARSET[rng.integers(len(CHARSET))]
                    d.text((c * cw + cw / 2, r * ch + ch / 2), glyph,
                           font=font, fill=255, anchor="mm")
            layers[g] = np.asarray(im, np.float32) / 255.0
        self.glyph_layers = layers

        # ── per-cell flicker: integer variant-cycles per loop → seamless ──
        self.g_phase = rng.random((nr, nc)).astype(np.float32)
        self.g_freq = rng.integers(0, 4, (nr, nc)).astype(np.float32)  # 0=static

        # ── drops: every column gets one head, ~40% get a second (parallax +
        #    dense enough that any single frozen frame looks full, for banners) ──
        kset = np.array([1]) if calm else np.array([1, 2])
        cols_a = np.arange(nc)
        extra = cols_a[rng.random(nc) < (0.25 if calm else 0.42)]
        dcol = np.concatenate([cols_a, extra])
        n = dcol.size
        k = kset[rng.integers(len(kset), size=n)].astype(float)
        self.d_col = dcol
        self.d_h0 = rng.random(n) * nr
        self.d_v = k * nr / self.period                     # cell-rows per second
        self.d_L = rng.uniform(0.28, 0.55, n) * nr          # tail length (cells)
        self.d_decay = rng.uniform(3.5, 7.0, n)             # brightness falloff
        self.d_amp = rng.uniform(0.5, 1.15, n)              # wide range = depth

        # static scanline mask (every other native row dimmed) — CRT feel.
        scan = np.ones(H, np.float32)
        scan[::2] = 0.82
        self.scan = scan[:, None, None]
        self.bloom_radius = max(1.0, ch * 0.35)

        self._R = np.arange(nr)[:, None].astype(float)      # (nr,1) row indices

    def _grids(self, t):
        """Per-cell brightness + headness at time t (both (nr, nc))."""
        h = (self.d_h0 + self.d_v * t) % self.nr            # (n,) head row/drop
        dist = (h[None, :] - self._R) % self.nr             # (nr, n) modular dist
        in_tail = dist < self.d_L[None, :]
        # tail falloff + a white-hot spike on the leading cell (the Matrix tell).
        envelope = np.exp(-dist / self.d_decay[None, :]) + 0.9 * np.exp(-dist / 0.7)
        contrib = self.d_amp[None, :] * envelope * in_tail
        headness = np.clip(1.0 - dist / 1.1, 0.0, 1.0) * in_tail

        bright = np.zeros((self.nr, self.nc))
        head = np.zeros((self.nr, self.nc))
        rr = np.broadcast_to(self._R.astype(int), (self.nr, contrib.shape[1])).ravel()
        cc = np.broadcast_to(self.d_col[None, :], (self.nr, contrib.shape[1])).ravel()
        np.maximum.at(bright, (rr, cc), contrib.ravel())
        np.maximum.at(head, (rr, cc), headness.ravel())
        return bright, head

    def _up(self, grid):
        """Nearest-neighbour upsample a cell grid to native (H, W) — works at any
        resolution, so the banner/still path never hits a divisibility crash."""
        im = Image.fromarray(grid.astype(np.float32), "F").resize(
            (self.W, self.H), Image.NEAREST)
        return np.asarray(im, np.float32)

    def frame(self, t):
        bright, head = self._grids(t)

        # which baked glyph layer each cell shows now (integer cycles/loop).
        sel = np.floor((self.g_phase + (t / self.period) * self.g_freq) * G) % G
        sel_pix = np.asarray(
            Image.fromarray(sel.astype(np.uint8), "L").resize(
                (self.W, self.H), Image.NEAREST))
        glyph = np.take_along_axis(self.glyph_layers, sel_pix[None], axis=0)[0]  # (H,W)

        bright_pix = self._up(bright)[..., None]
        head_pix = self._up(head)[..., None]
        color = self.tail[None, None, :] + (self.head - self.tail)[None, None, :] * head_pix
        lit = glyph[..., None] * color * bright_pix                       # (H,W,3)

        # bloom: blur the lit glyphs and add back so dark cells get a soft glow.
        blur = np.asarray(
            Image.fromarray(np.clip(lit, 0, 255).astype(np.uint8), "RGB")
            .filter(ImageFilter.GaussianBlur(self.bloom_radius)), np.float32)

        img = self.bg + lit + blur * 0.55
        img *= self.scan
        np.clip(img, 0, 255, out=img)
        return img.astype(np.uint8)


if __name__ == "__main__":
    s = Terminal(cols=320, rows=90, loop_period=12.0, palette="aurora")
    a = s.frame(0.0).astype(int)
    b = s.frame(s.period).astype(int)
    diff = int(np.abs(a - b).max())
    print(f"terminal seamless check: max |frame(0)-frame(period)| = {diff} (<=1 ok)")
    assert diff <= 1, "terminal loop seam too large!"
    print("ok")
