#!/usr/bin/env python3
"""
flow.py — "silk": a curl-noise flow field, same interface as aurora.Sky.

Pure abstract, extremely calming: soft ribbons of smoke/silk drifting and
folding through each other, tinted to the kit's three palettes. No horizon, no
objects — just a divergence-free field breathing in place. Drop in via the scene
registry (`--scene flow`).

How the look is built (Inigo-Quilez domain warping over a curl field):
  * A periodic stream-function psi = sum of sinusoids. Its CURL gives a
    divergence-free velocity (v = (d psi/dy, -d psi/dx)) — the field swirls
    without compressing, which is what reads as smoke rather than a scroll.
  * Two nested curl warps displace the sample coordinates (warp the warp), so
    straight iso-bands fold into silky ribbons.
  * A final scalar field is sampled at the warped coordinate and mapped through
    a soft 4-stop palette gradient; its iso-bands are the drifting ribbons.

Seamless looping (the load-bearing trick, mirrors rain.py / terminal.py):
  Every time dependence enters ONLY as omega * tau with tau = 2*pi*t/period and
  INTEGER omega. So sin/cos of (... + omega*tau) returns to its t=0 value at
  t=period. Domain warping composes these periodic functions, so it stays
  periodic; the colour map, static grain and vignette are stateless transforms.
  There is no advected particle state to drift. Verified frame(0) == frame(period).

Attribute names deliberately avoid `layers` / `ridge_top` — render_still.py pokes
those (band-shift / ridge-frac) by hasattr, and they are aurora-only knobs.

Renderer contract: __init__(cols, rows, calm, loop_period, meteors, meteor_seed,
palette) and frame(t) -> (H, W, 3) uint8, with H = rows*2, W = cols.
"""

import numpy as np
from PIL import Image, ImageFilter

from .aurora import PALETTES

TWO_PI = 2.0 * np.pi

# Per-palette smoke gradient: dark base -> mid -> glow -> pale ribbon highlight.
# Positions are 0..1 over the (gamma-shaped) field; the bright stop is what the
# ribbons catch. Tuned low-contrast so most of the frame stays calm/dark.
GRADIENTS = {
    "aurora": {  # emerald smoke over teal-black
        "p": [0.00, 0.45, 0.78, 1.00],
        "r": [8,  18, 60, 165],
        "g": [16, 70, 165, 230],
        "b": [20, 55, 120, 200],
    },
    "ember": {  # warm coal -> red -> amber -> cream
        "p": [0.00, 0.45, 0.78, 1.00],
        "r": [18, 110, 215, 245],
        "g": [10, 32,  95, 205],
        "b": [14, 28,  45, 155],
    },
    "ice": {  # deep navy -> blue -> cyan -> pale ice
        "p": [0.00, 0.45, 0.78, 1.00],
        "r": [8,  25,  60, 200],
        "g": [12, 55, 150, 230],
        "b": [26, 120, 220, 250],
    },
    "gold": {  # magic hour sky: dusty mauve -> rose-gold -> peach -> pale cream
        "p": [0.00, 0.38, 0.70, 1.00],
        "r": [78, 200, 246, 255],
        "g": [60, 150, 200, 240],
        "b": [82, 138, 162, 224],
    },
    "nord": {  # polar night -> nord frost: charcoal -> slate -> frost blue -> cyan
        "p": [0.00, 0.45, 0.78, 1.00],
        "r": [12, 46, 94, 136],
        "g": [14, 52, 129, 192],
        "b": [18, 64, 172, 208],
    },
}


def _modes(rng, n, fmin, fmax, omegas, amp_scale):
    """A band-limited periodic field: n sinusoids with random direction/phase,
    integer time-frequency omega (seamless), amplitude ~ 1/freq so the big slow
    swells dominate and the look stays soft."""
    ang = rng.uniform(0.0, TWO_PI, n)
    r = rng.uniform(fmin, fmax, n)                  # cycles per height-unit
    return {
        "fx": (r * np.cos(ang)).astype(np.float32),
        "fy": (r * np.sin(ang)).astype(np.float32),
        "phase": rng.uniform(0.0, TWO_PI, n).astype(np.float32),
        "omega": rng.choice(np.array(omegas), n).astype(np.float32),
        "amp": (amp_scale / r).astype(np.float32),
    }


class Flow:
    """Curl-noise silk. Mirrors aurora.Sky's constructor + frame(t) contract."""

    def __init__(self, cols, rows, calm=False, loop_period=None,
                 meteors=0, meteor_seed=7, palette="aurora"):
        # meteors / meteor_seed accepted for signature-compatibility, unused.
        self.W = cols
        self.H = rows * 2
        W, H = self.W, self.H
        self.period = loop_period or 16.0

        g = GRADIENTS[palette]
        self.gp = np.array(g["p"], np.float32)
        self.gr = np.array(g["r"], np.float32)
        self.gg = np.array(g["g"], np.float32)
        self.gb = np.array(g["b"], np.float32)

        # square-pixel coords in units of height (u spans the aspect ratio).
        x = np.arange(W, dtype=np.float32)
        y = np.arange(H, dtype=np.float32)
        self.u = (x / H)[None, :].repeat(H, 0)          # (H, W)
        self.v = (y / H)[:, None].repeat(W, 1)          # (H, W)

        rng = np.random.default_rng(meteor_seed * 131 + 7)

        # Default is already calm; --calm slows drift further (omega -> +/-1) and
        # eases the warp so ribbons fold more gently.
        om_slow = [-1, 1] if calm else [-2, -1, 1, 2]
        om_field = [-1, 1] if calm else [-2, -1, 1, 2]
        warp = 0.8 if calm else 1.0

        # warp stage A: big, slow swirls.  stage B: finer folds of the warped p.
        self.warpA = _modes(rng, 5, 0.35, 1.05, [-1, 1], amp_scale=1.0)
        self.warpB = _modes(rng, 5, 0.85, 2.10, om_slow, amp_scale=1.0)
        self.field = _modes(rng, 6, 0.50, 1.80, om_field, amp_scale=1.0)
        self.ampA = 0.33 * warp
        self.ampB = 0.13 * warp
        self.norm = float(np.abs(self.field["amp"]).sum())   # scalar range

        # static filmic grain + vignette (baked once -> stay seamless).
        grain = rng.normal(0.0, 1.0, (H, W)).astype(np.float32)
        self.grain = (grain * 3.5)[..., None]
        vx, vy = (x / W - 0.5)[None, :], (y / H - 0.5)[:, None]
        self.vig = np.clip(1.0 - 0.85 * (vx ** 2 * 1.15 + vy ** 2 * 1.5),
                           0.30, 1.0).astype(np.float32)[..., None]
        self.blur = max(0.6, H / 360.0)                 # tiny smooth, kills aliasing

    def _curl(self, u, v, m, tau):
        """Divergence-free velocity = curl of the stream-function m at (u,v,tau).
        v = (d psi/dv, -d psi/du); the freq factors fall out of the derivative."""
        theta = (TWO_PI * (m["fx"] * u[..., None] + m["fy"] * v[..., None])
                 + m["phase"] + m["omega"] * tau)       # (H, W, n)
        c = m["amp"] * np.cos(theta)
        wx = (c * m["fy"]).sum(-1)
        wy = -(c * m["fx"]).sum(-1)
        return wx, wy

    def _scalar(self, u, v, m, tau):
        theta = (TWO_PI * (m["fx"] * u[..., None] + m["fy"] * v[..., None])
                 + m["phase"] + m["omega"] * tau)
        return (m["amp"] * np.sin(theta)).sum(-1)

    def frame(self, t):
        tau = TWO_PI * (t / self.period)

        # domain warp: curl-warp the coords, then curl-warp the warped coords.
        ax, ay = self._curl(self.u, self.v, self.warpA, tau)
        u1 = self.u + self.ampA * ax
        v1 = self.v + self.ampA * ay
        bx, by = self._curl(u1, v1, self.warpB, tau)
        u2 = u1 + self.ampB * bx
        v2 = v1 + self.ampB * by

        # final field at the doubly-warped coord -> 0..1 with a dark-weighted gamma.
        s = self._scalar(u2, v2, self.field, tau)
        p = np.clip(0.5 + 0.5 * (s / self.norm) * 0.92, 0.0, 1.0)
        p = p ** 1.35                                   # push toward dark; sparse ribbons

        img = np.empty((self.H, self.W, 3), np.float32)
        img[..., 0] = np.interp(p, self.gp, self.gr)
        img[..., 1] = np.interp(p, self.gp, self.gg)
        img[..., 2] = np.interp(p, self.gp, self.gb)

        img += self.grain
        img *= self.vig
        np.clip(img, 0, 255, out=img)

        # a hair of blur to read as smoke, not banded gradient.
        im = Image.fromarray(img.astype(np.uint8), "RGB").filter(
            ImageFilter.GaussianBlur(self.blur))
        return np.asarray(im, np.uint8)
