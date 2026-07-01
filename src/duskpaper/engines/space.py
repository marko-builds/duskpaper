#!/usr/bin/env python3
"""
space.py — a slow majestic drift across a galaxy, same interface as aurora.Sky.

Not a warp: a near-black sky with a warm Milky-Way band (bright cores + a dark
dust lane + cloudy mottling) cutting diagonally across, a small companion galaxy
off in one corner, and a field of varied-size stars that drift gently and
twinkle. Tuned to a reference night-sky clip — slow star speed, varied star
sizes, a colorful background galaxy. Drop into the renderers via `--scene space`.

Seamless looping: star drift rides a sinusoid and twinkle rides an
integer-frequency phase keyed to the loop period, so frame(0) == frame(period)
exactly. The galaxy band is baked once at init.
"""

import numpy as np
from PIL import Image

from .aurora import PALETTES

# warm galactic light, core -> rim (like the Milky Way's golden core)
GALAXY_CORE = np.array([255, 226, 170.0])
GALAXY_MID = np.array([210, 150, 90.0])
GALAXY_RIM = np.array([90, 62, 52.0])

# star colours: mostly warm/cool white, a few faintly tinted (the "colorful" ask)
STAR_TINTS = [
    (170, 200, 255), (255, 232, 205), (255, 198, 168),
    (205, 222, 255), (255, 250, 242),
]


def _value_noise(H, W, rng, octaves=5, base=5):
    """Fractal value noise in [0,1] — low-res random grids bilinearly upsampled."""
    out = np.zeros((H, W))
    amp, tot = 1.0, 0.0
    for o in range(octaves):
        gh = base * (2 ** o)
        gw = max(2, round(gh * W / H))
        grid = (rng.random((gh, gw)) * 255).astype(np.uint8)
        up = np.asarray(Image.fromarray(grid).resize((W, H), Image.BILINEAR), float) / 255.0
        out += up * amp
        tot += amp
        amp *= 0.55
    return out / tot


class Space:
    """Slow drift across a galaxy band + starfield. Mirrors Sky's contract."""

    def __init__(self, cols, rows, calm=False, loop_period=None,
                 meteors=0, meteor_seed=7, palette="aurora"):
        self.W = cols
        self.H = rows * 2
        W, H = self.W, self.H
        self.period = loop_period or 14.0
        self.calm = calm
        rng = np.random.default_rng(11)
        pal = PALETTES[palette]
        yy, xx = np.mgrid[0:H, 0:W].astype(float)

        # ── near-black deep space (static base) ──
        bg = np.zeros((H, W, 3), np.float32) + np.array([3, 4, 8.0], np.float32)

        # palette-tinted nebula wisps, each baked as its OWN layer so it can
        # "breathe" — brightness rides an integer-frequency sinusoid (seamless).
        pcol = [np.array([np.interp(p, pal["p"], pal["r"]),
                          np.interp(p, pal["p"], pal["g"]),
                          np.interp(p, pal["p"], pal["b"])]) for p in (0.25, 0.5, 0.75)]
        self.wisps = []                       # (layer HxWx3, int freq, phase)
        for i in range(3):
            bx, by = rng.uniform(0.35, 1.0) * W, rng.uniform(0, H)
            br = rng.uniform(0.20, 0.40) * max(W, H)
            d2 = (xx - bx) ** 2 + (yy - by) ** 2
            layer = (np.exp(-d2 / (2 * br ** 2))[..., None]
                     * pcol[i % 3] * rng.uniform(0.045, 0.085)).astype(np.float32)
            self.wisps.append((layer, int(rng.integers(1, 3)),
                               rng.uniform(0, 2 * np.pi)))

        # ── the galaxy band: diagonal, cloudy mottle, dark dust lane, bright cores ──
        # Baked wider than the frame so it can pan (parallax drift) at frame time.
        self.band_amp = 0.012 * W             # drift amplitude in px ("far" layer)
        pad = int(np.ceil(self.band_amp)) + 2
        self.band_pad = pad
        WB = W + 2 * pad
        yyb, xxb = np.mgrid[0:H, 0:WB].astype(float)
        xxb -= pad                            # same coordinate frame as the sky
        ang = np.deg2rad(-24.0)
        ca, sa = np.cos(ang), np.sin(ang)
        bx0, by0 = 0.70 * W, 0.52 * H          # bulge sits center-right; left sky stays dark
        u = (xxb - bx0) * ca + (yyb - by0) * sa   # along the band
        v = -(xxb - bx0) * sa + (yyb - by0) * ca  # across the band
        bandw = 0.135 * H
        along = np.exp(-(u / (0.44 * W)) ** 2)   # concentrated, fades before the left edge
        across = np.exp(-(v / bandw) ** 2)
        noise = _value_noise(H, WB, rng, octaves=5, base=5)
        glow = across * along * (0.20 + 1.06 * noise)   # higher contrast -> cloudier
        # dark dust lane, slightly offset across the band, broken up by its own noise
        dust = np.exp(-((v - 0.18 * bandw) / (0.38 * bandw)) ** 2)
        dustn = _value_noise(H, WB, rng, octaves=4, base=4)
        glow *= (1.0 - 0.76 * dust * (0.45 + 0.55 * dustn))
        glow = np.clip(glow, 0, None)

        g = glow[..., None]
        band_rgb = (GALAXY_RIM
                    + (GALAXY_MID - GALAXY_RIM) * np.clip(g / 0.5, 0, 1)
                    + (GALAXY_CORE - GALAXY_MID) * np.clip((g - 0.5) / 0.5, 0, 1))
        band = band_rgb * g * 1.5

        # bright cores glowing through the band (pan with it)
        for frac, br, amp in [(-0.04, 0.10, 1.0), (0.20, 0.12, 0.65), (-0.28, 0.07, 0.4)]:
            ccx, ccy = bx0 + frac * W * ca, by0 + frac * W * sa
            r = br * H
            d2 = (xxb - ccx) ** 2 + (yyb - ccy) ** 2
            band += np.exp(-d2 / (2 * r ** 2))[..., None] * GALAXY_CORE * amp * 0.85
        self.band = band.astype(np.float32)    # (H, W+2*pad, 3)

        # ── small companion galaxy off in a corner (static base) ──
        gx, gy = 0.10 * W, 0.17 * H
        ga = np.deg2rad(35)
        gu = (xx - gx) * np.cos(ga) + (yy - gy) * np.sin(ga)
        gv = -(xx - gx) * np.sin(ga) + (yy - gy) * np.cos(ga)
        a, b = 0.058 * W, 0.020 * W
        ell = np.exp(-((gu / a) ** 2 + (gv / b) ** 2))
        halo = np.exp(-((gu / (a * 1.9)) ** 2 + (gv / (b * 2.6)) ** 2))
        bg += (halo[..., None] * np.array([200, 215, 255.0]) * 0.16).astype(np.float32)
        bg += (ell[..., None] * np.array([255, 246, 232.0]) * 0.55).astype(np.float32)

        self.sky = bg

        # ── starfield: varied sizes, gentle parallax drift + twinkle ──
        scale = 0.7 if calm else 1.0
        n = int(W * H / 620 * scale)
        self.n = n
        self.sx = rng.uniform(0, W, n)
        self.sy = rng.uniform(0, H, n)
        self.bright = rng.uniform(0.22, 1.0, n) ** 1.7
        roll = rng.random(n)
        self.cls = np.where(roll < 0.84, 0, np.where(roll < 0.97, 1, 2))  # small/med/large
        self.tw_f = rng.integers(1, 4, n).astype(float)     # twinkle cycles per loop
        self.tw_ph = rng.uniform(0, 2 * np.pi, n)
        depth = np.where(self.cls == 2, 1.0, np.where(self.cls == 1, 0.6, 0.32))
        amp_px = (0.008 if calm else 0.014) * W             # slow drift
        ddir = rng.uniform(0, 2 * np.pi)
        self.dvx = np.cos(ddir) * amp_px * depth
        self.dvy = np.sin(ddir) * amp_px * depth
        col = np.tile(np.array([255, 252, 246.0]), (n, 1))
        tint = rng.random(n) < 0.28
        col[tint] = np.array(STAR_TINTS, float)[rng.integers(0, len(STAR_TINTS), tint.sum())]
        self.scol = col

        # ── meteors (shooting stars) — off by default ──
        self.meteors = meteors
        mrng = np.random.default_rng(meteor_seed)
        self.met = [(mrng.uniform(0, 1),
                     (mrng.uniform(0.05, 0.5) * W, mrng.uniform(0.05, 0.5) * H),
                     mrng.uniform(2.4, 3.6))                # travel angle (rad)
                    for _ in range(meteors)]

    def _splat(self, img, xs, ys, rgb, offsets):
        for dx, dy, w in offsets:
            xi = np.round(xs + dx).astype(int)
            yi = np.round(ys + dy).astype(int)
            ok = (xi >= 0) & (xi < self.W) & (yi >= 0) & (yi < self.H)
            np.add.at(img, (yi[ok], xi[ok]), rgb[ok] * w)

    def _stars(self, img, t):
        s = np.sin(2 * np.pi * t / self.period)
        x = self.sx + self.dvx * s
        y = self.sy + self.dvy * s
        tw = 0.60 + 0.40 * np.sin(2 * np.pi * self.tw_f * t / self.period + self.tw_ph)
        val = (self.bright * tw)[:, None] * self.scol

        small = [(0, 0, 1.0)]
        med = [(0, 0, 1.0), (1, 0, .4), (-1, 0, .4), (0, 1, .4), (0, -1, .4)]
        large = [(0, 0, 1.0), (1, 0, .6), (-1, 0, .6), (0, 1, .6), (0, -1, .6),
                 (2, 0, .25), (-2, 0, .25), (0, 2, .25), (0, -2, .25),
                 (1, 1, .3), (-1, 1, .3), (1, -1, .3), (-1, -1, .3)]
        for c, offs, boost in ((0, small, 1.0), (1, med, 1.1), (2, large, 1.5)):
            m = self.cls == c
            self._splat(img, x[m], y[m], val[m] * boost, offs)

    def _meteors(self, img, t):
        for p0, (sx, sy), ang in self.met:
            ph = (p0 + t / self.period) % 1.0
            env = np.clip(ph / 0.05, 0, 1) * np.clip((1 - ph) / 0.30, 0, 1)
            if env <= 0:
                continue
            dist = 0.5 * self.diag if hasattr(self, "diag") else 0.5 * np.hypot(self.W, self.H)
            hx = sx + np.cos(ang) * dist * ph
            hy = sy + np.sin(ang) * dist * ph
            k = np.linspace(0, 1, 14)
            L = 0.06 * self.W
            px = hx - np.cos(ang) * L * k
            py = hy - np.sin(ang) * L * k
            rgb = np.tile(np.array([255, 250, 240.0]), (len(k), 1)) * (env * (1 - k))[:, None]
            self._splat(img, px, py, rgb, [(0, 0, 1.0)])

    def frame(self, t):
        tau = 2 * np.pi * t / self.period
        img = self.sky.copy()

        # nebula breathing: each wisp's brightness rides its own integer-freq
        # sinusoid, so it returns to its t=0 value at t=period.
        for layer, freq, ph in self.wisps:
            img += layer * (1.0 + 0.30 * np.sin(freq * tau + ph))

        # galaxy-band parallax drift: sub-pixel pan of the padded bake, same
        # sinusoid phase as the star drift but smaller amplitude (band = "far").
        off = self.band_pad + self.band_amp * np.sin(tau)
        i0 = int(np.floor(off))
        fr = np.float32(off - i0)
        img += self.band[:, i0:i0 + self.W] * (1.0 - fr)
        img += self.band[:, i0 + 1:i0 + 1 + self.W] * fr

        self._stars(img, t)
        if self.meteors:
            self._meteors(img, t)
        np.clip(img, 0, 255, out=img)
        return img.astype(np.uint8)


if __name__ == "__main__":
    eng = Space(cols=320, rows=100, loop_period=30.0, meteors=2)
    a = eng.frame(0.0).astype(int)
    b = eng.frame(30.0).astype(int)
    delta = int(np.abs(a - b).max())
    print(f"space seamless check: max |frame(0)-frame(period)| = {delta} (<=1 ok)")
    assert delta <= 1, f"space loop seam too large: {delta}"
    c = eng.frame(7.0).astype(int)
    assert not (a == c).all(), "frame(0) == frame(7): scene is not moving"
    print("space motion check: ok")
