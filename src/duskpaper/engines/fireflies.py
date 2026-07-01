#!/usr/bin/env python3
"""
fireflies.py — "firefly night meadow": dark ridge silhouettes under a deep
night sky, with warm fireflies drifting and pulsing above the grass. Same
interface as aurora.Sky; drop in via the scene registry (`--scene fireflies`).

Built as a wallpaper scene: almost everything is near-black and still; the
only motion is the fireflies' slow wander + blink and a faint star twinkle,
so it stays calm behind a working desktop.

Seamless looping (mirrors flow.py / space.py):
  * Firefly paths are closed Lissajous curves — every positional term is
    sin/cos of (integer_freq * tau + phase) with tau = 2*pi*t/period, so
    position(period) == position(0) exactly.
  * Blinks are max(0, sin(k*tau + ph))**4 with integer k — periodic pulses
    with true dark gaps between them.
  * Star twinkle rides integer-frequency phases (same trick as space.py).
  * Sky gradient + ridges are baked once at init and never move.

Renderer contract: __init__(cols, rows, calm, loop_period, meteors,
meteor_seed, palette) and frame(t) -> (H, W, 3) uint8, with H = rows*2.
"""

import numpy as np

TWO_PI = 2.0 * np.pi

# night colours
SKY_TOP = np.array([2, 3, 8.0], np.float32)
SKY_HORIZON = np.array([10, 20, 24.0], np.float32)
RIDGE_FILLS = [  # far -> near, silhouettes barely lighter than black
    np.array([9, 16, 14.0], np.float32),
    np.array([5, 10, 9.0], np.float32),
    np.array([2, 5, 4.0], np.float32),
]
FIREFLY_TINTS = [  # mostly green-gold, a few amber
    (196, 255, 110), (210, 255, 130), (185, 245, 100), (255, 214, 110),
]
STAR_COL = np.array([190, 205, 220.0], np.float32)


def _ridge(rng, W, y_center, amplitude, harmonics=(1, 2, 3, 5, 8)):
    """Periodic ridgeline (integer harmonics tile exactly at width W)."""
    x = np.arange(W, dtype=np.float32) * (TWO_PI / W)
    r = np.zeros(W)
    for k in harmonics:
        r += rng.uniform(0.5, 1.0) / k * np.sin(k * x + rng.uniform(0, TWO_PI))
    r /= np.abs(r).max() or 1.0
    return np.clip(y_center + r * amplitude, 0, None)


class Fireflies:
    """Firefly night meadow. Mirrors aurora.Sky's constructor + frame(t)."""

    def __init__(self, cols, rows, calm=False, loop_period=None,
                 meteors=0, meteor_seed=7, palette="aurora"):
        # meteors accepted for signature-compatibility, unused. palette too:
        # a meadow at night has one true colour scheme.
        self.W = cols
        self.H = rows * 2
        W, H = self.W, self.H
        self.period = loop_period or 30.0
        rng = np.random.default_rng(meteor_seed * 977 + 3)

        # ── baked backdrop: sky gradient + faint horizon glow + ridges ──
        frac = (np.linspace(0, 1, H, dtype=np.float32) ** 1.4)[:, None, None]
        img = SKY_TOP * (1 - frac) + SKY_HORIZON * frac
        img = np.broadcast_to(img, (H, W, 3)).copy()

        yy = np.arange(H, dtype=np.float32)[:, None]
        ridge_ys = [0.62 * H, 0.74 * H, 0.88 * H]
        ridge_amp = [0.075 * H, 0.06 * H, 0.05 * H]
        far_ridge = None
        for ry, ra, fill in zip(ridge_ys, ridge_amp, RIDGE_FILLS):
            hm = _ridge(rng, W, ry, ra)
            if far_ridge is None:
                far_ridge = hm
                # soft mist glow hugging the far ridgeline (baked, static)
                d = (yy - hm[None, :]) / (0.10 * H)
                glow = np.exp(-d * d) * 0.5 + np.exp(-np.abs(d) * 3) * 0.5
                img += glow[..., None] * np.array([14, 26, 24.0], np.float32) * 0.9
            img[yy >= hm[None, :]] = fill
        self.backdrop = img

        # star mask above the far ridge only
        # ── sparse dim stars (positions baked; twinkle at frame time) ──
        n_stars = int(W * H / 2800)
        sx = rng.uniform(0, W, n_stars)
        sy = rng.uniform(0, 0.9, n_stars) ** 1.6 * (far_ridge[
            np.clip(sx.astype(int), 0, W - 1)] - 4)
        keep = sy > 0
        self.sx, self.sy = sx[keep], sy[keep]
        ns = self.sx.size
        self.s_bright = rng.uniform(0.10, 0.5, ns) ** 1.4
        self.s_freq = rng.integers(1, 4, ns).astype(np.float32)
        self.s_ph = rng.uniform(0, TWO_PI, ns)

        # ── fireflies: closed Lissajous wander + integer-freq blink ──
        n = max(10, int(W * H / (11000 if calm else 6500)))
        self.n = n
        # home positions: meadow band, denser toward the grass
        self.fx0 = rng.uniform(0.02 * W, 0.98 * W, n)
        band = rng.uniform(0.0, 1.0, n) ** 0.7      # 0 = high, 1 = low
        self.fy0 = (0.42 + 0.52 * band) * H
        # wander: two sinusoids per axis, integer freqs, small slow amplitudes
        amp = 0.035 * W * (0.7 if calm else 1.0)
        self.ax1 = rng.uniform(0.3, 1.0, n) * amp
        self.ax2 = rng.uniform(0.1, 0.4, n) * amp
        self.ay1 = rng.uniform(0.2, 0.7, n) * amp * 0.6
        self.ay2 = rng.uniform(0.1, 0.3, n) * amp * 0.6
        self.fx1 = rng.integers(1, 3, n).astype(np.float32)
        self.fx2 = rng.integers(2, 5, n).astype(np.float32)
        self.fy1 = rng.integers(1, 3, n).astype(np.float32)
        self.fy2 = rng.integers(2, 5, n).astype(np.float32)
        self.px1, self.px2, self.py1, self.py2 = (
            rng.uniform(0, TWO_PI, n) for _ in range(4))
        # blink: k pulses per loop, staggered; brightness varies per fly
        self.bk = rng.integers(2, 6, n).astype(np.float32)
        self.bph = rng.uniform(0, TWO_PI, n)
        self.bmax = rng.uniform(0.70, 1.15, n)
        col = np.array(FIREFLY_TINTS, np.float32)
        self.fcol = col[rng.integers(0, len(FIREFLY_TINTS), n)]

        # precomputed glow kernel: hot gaussian core + wide soft halo. Sized in
        # native pixels so the glow scales with the render grid.
        rad = max(4, int(round(H / 130)))
        s_core = rad * 0.22
        s_halo = rad * 0.62
        kern = []
        for dy in range(-rad, rad + 1):
            for dx in range(-rad, rad + 1):
                r2 = dx * dx + dy * dy
                w = (np.exp(-r2 / (2 * s_core ** 2))
                     + 0.30 * np.exp(-r2 / (2 * s_halo ** 2)))
                if w > 0.01:
                    kern.append((dx, dy, float(w)))
        self.glow_kernel = kern

    def _splat(self, img, xs, ys, rgb, offsets):
        for dx, dy, w in offsets:
            xi = np.round(xs + dx).astype(int)
            yi = np.round(ys + dy).astype(int)
            ok = (xi >= 0) & (xi < self.W) & (yi >= 0) & (yi < self.H)
            np.add.at(img, (yi[ok], xi[ok]), rgb[ok] * w)

    def frame(self, t):
        tau = TWO_PI * t / self.period
        img = self.backdrop.copy()

        # star twinkle
        tw = 0.55 + 0.45 * np.sin(self.s_freq * tau + self.s_ph)
        sval = (self.s_bright * tw)[:, None] * STAR_COL
        self._splat(img, self.sx, self.sy, sval, [(0, 0, 1.0)])

        # fireflies: closed wander + pulsing glow
        x = (self.fx0 + self.ax1 * np.sin(self.fx1 * tau + self.px1)
             + self.ax2 * np.sin(self.fx2 * tau + self.px2))
        y = (self.fy0 + self.ay1 * np.sin(self.fy1 * tau + self.py1)
             + self.ay2 * np.sin(self.fy2 * tau + self.py2))
        blink = np.maximum(0.0, np.sin(self.bk * tau + self.bph)) ** 3
        b = (blink * self.bmax)[:, None] * self.fcol
        self._splat(img, x, y, b, self.glow_kernel)

        np.clip(img, 0, 255, out=img)
        return img.astype(np.uint8)


if __name__ == "__main__":
    eng = Fireflies(cols=320, rows=100, loop_period=30.0)
    a = eng.frame(0.0).astype(int)
    b = eng.frame(30.0).astype(int)
    delta = int(np.abs(a - b).max())
    print(f"fireflies seamless check: max |frame(0)-frame(period)| = {delta} (<=1 ok)")
    assert delta <= 1, f"fireflies loop seam too large: {delta}"
    c = eng.frame(7.0).astype(int)
    assert not (a == c).all(), "frame(0) == frame(7): scene is not moving"
    print("fireflies motion check: ok")
