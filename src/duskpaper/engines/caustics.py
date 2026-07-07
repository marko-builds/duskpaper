#!/usr/bin/env python3
"""
caustics.py — "underwater": the rippling web of light a wavy water surface casts
on the ocean floor, same interface as aurora.Sky. Reads most beautifully as the
`ice` palette (sunlit blue water); ember = warm lagoon, aurora = bioluminescent
green. Pure abstract, calming. Drop in via the scene registry (`--scene caustics`).

How the look is built (a caustic NET, no ray tracing):
  * The signature of caustics is a reticulated web of thin bright curved lines
    with dark polygonal cells, not isolated blobs. Each travelling wave gives a
    family of thin bright stripes via (1 - |sin(theta)|) ** sharp (brightest at
    the wave's zero-crossings, thinned by the power). Summing several directions
    crosses the stripe families into a net, with bright nodes where lines meet.
  * Two slow warp waves displace the sample coordinates, so the straight stripe
    grid curves and wanders -> organic, never-repeating-looking light.
  * A finer, sharper octave adds the sparkle; a vertical sun gradient + bloom
    sell "sunlight from above through water" (light, not wire).

Seamless looping (the load-bearing trick, mirrors rain.py / flow.py):
  Each wave travels a WHOLE number of cycles across the loop: its phase is
  k*(2*pi)*t/period with INTEGER k, so it returns to its t=0 state at t=period.
  Everything downstream (ridge, power, bloom, tint) is a stateless transform of
  that periodic field. Verified frame(0) == frame(period) to <=1 LSB.

Attribute names avoid `layers` / `ridge_top` (render_still.py pokes those by
hasattr for aurora-only band-shift / ridge-frac knobs).

Renderer contract: __init__(cols, rows, calm, loop_period, meteors, meteor_seed,
palette) and frame(t) -> (H, W, 3) uint8, with H = rows*2, W = cols.
"""

import numpy as np
from PIL import Image, ImageFilter

TWO_PI = 2.0 * np.pi

# Per-palette: deep water (bottom/base), the lit floor tint, and the bright
# caustic-line colour (near-white, faintly palette-tinted = sunlight in water).
WATER = {
    "ice": {    # sunlit tropical blue — the canonical look
        "deep": (6, 26, 54), "floor": (24, 90, 150), "light": (210, 245, 255),
    },
    "aurora": {  # bioluminescent green lagoon
        "deep": (6, 28, 26), "floor": (24, 120, 95), "light": (200, 255, 220),
    },
    "ember": {   # warm shallow lagoon at golden hour
        "deep": (32, 18, 20), "floor": (150, 80, 45), "light": (255, 235, 200),
    },
    "garnet": {  # light through a gemstone onto dark velvet — deep red/magenta
        "deep": (20, 6, 14), "floor": (110, 22, 48), "light": (255, 196, 212),
    },
}


def _waves(rng, n, kmin, kmax, kt_max=3):
    """n travelling sine waves: integer cycles/loop in time (seamless) and a
    random spatial direction/frequency. Returns the arrays frame() needs."""
    ang = rng.uniform(0.0, TWO_PI, n)
    sf = rng.uniform(kmin, kmax, n)                  # spatial cycles per height-unit
    return {
        "kx": (sf * np.cos(ang)).astype(np.float32),
        "ky": (sf * np.sin(ang)).astype(np.float32),
        "phase": rng.uniform(0.0, TWO_PI, n).astype(np.float32),
        # integer temporal cycles/loop -> seamless; sign = travel direction.
        "kt": (rng.integers(1, kt_max + 1, n) * rng.choice([-1, 1], n)).astype(np.float32),
        "amp": (1.0 / sf).astype(np.float32),        # big swells dominate
    }


class Caustics:
    """Rippling underwater light. Mirrors aurora.Sky's constructor + frame(t)."""

    def __init__(self, cols, rows, calm=False, loop_period=None,
                 meteors=0, meteor_seed=7, palette="ice"):
        # meteors / meteor_seed accepted for signature-compatibility, unused.
        self.W = cols
        self.H = rows * 2
        W, H = self.W, self.H
        self.period = loop_period or 14.0

        col = WATER[palette]
        self.deep = np.array(col["deep"], np.float32)
        self.floor = np.array(col["floor"], np.float32)
        self.light = np.array(col["light"], np.float32)

        # square-pixel coords in units of height.
        x = np.arange(W, dtype=np.float32)
        y = np.arange(H, dtype=np.float32)
        self.u = (x / H)[None, :].repeat(H, 0)
        self.v = (y / H)[:, None].repeat(W, 1)

        rng = np.random.default_rng(meteor_seed * 911 + 3)

        # warp waves bend the stripe grid into curved, wandering lines (gentle:
        # too much and the net swirls into marbled hair instead of a seabed web).
        self.warp = _waves(rng, 2, 0.5, 1.1)
        self.warp_amp = 0.07 if calm else 0.10
        # two octaves of line families: a coarse net of big cells + a finer
        # sparkle. Low spatial freq = big dark cells; high powers = thin lines;
        # low kt = lazy drift. Together: a sparse bright net over dark water.
        n_coarse = 3 if calm else 3
        n_fine = 2 if calm else 2
        self.coarse = _waves(rng, n_coarse, 0.8, 1.5, kt_max=2)
        self.fine = _waves(rng, n_fine, 2.2, 3.4, kt_max=2)
        self.sharp_c = 9.0 if calm else 8.0          # line thinness (coarse)
        self.sharp_f = 13.0                          # finer lines = sharper sparkle

        # sunlight gradient: brighter toward the top (light comes from above).
        grad = 0.78 + 0.34 * (1.0 - (y / H))
        self.sun = grad[:, None, None].astype(np.float32)

        # static vignette + a hair of bloom radius.
        vx, vy = (x / W - 0.5)[None, :], (y / H - 0.5)[:, None]
        self.vig = np.clip(1.0 - 0.7 * (vx ** 2 * 1.1 + vy ** 2 * 1.4),
                           0.42, 1.0).astype(np.float32)[..., None]
        self.bloom = max(1.0, H / 220.0)

    def _warped(self, tau):
        """Displace the sample coords by the slow warp waves -> curved lines."""
        w = self.warp
        theta = (TWO_PI * (w["kx"] * self.u[..., None] + w["ky"] * self.v[..., None])
                 + w["phase"] + w["kt"] * tau)
        du = (w["amp"] * np.sin(theta)).sum(-1)
        dv = (w["amp"] * np.cos(theta)).sum(-1)
        return self.u + self.warp_amp * du, self.v + self.warp_amp * dv

    def _net(self, waves, sharp, u, v, tau):
        """A caustic net from a wave set: each wave is a family of thin bright
        stripes (1 - |sin|) ** sharp; summed directions cross into a web with
        bright nodes where lines meet."""
        theta = (TWO_PI * (waves["kx"] * u[..., None] + waves["ky"] * v[..., None])
                 + waves["phase"] + waves["kt"] * tau)        # (H, W, n)
        lines = (1.0 - np.abs(np.sin(theta))) ** sharp        # thin bright stripes
        return lines.sum(-1)

    def frame(self, t):
        tau = TWO_PI * (t / self.period)
        u, v = self._warped(tau)
        net = self._net(self.coarse, self.sharp_c, u, v, tau)
        net = net + 0.4 * self._net(self.fine, self.sharp_f, u, v, tau)
        net = np.clip(net, 0.0, 1.0)[..., None]               # (H, W, 1)

        # floor lit by the sun gradient, then the bright caustic net laid over.
        base = self.deep[None, None] + (self.floor - self.deep)[None, None] * 0.55
        img = base * self.sun
        img = img + (self.light[None, None] - img) * net   # net pushes toward light
        img *= self.vig
        np.clip(img, 0, 255, out=img)

        # bloom: blur the bright net and add back so lines glow like light.
        im = Image.fromarray(img.astype(np.uint8), "RGB")
        glow = np.asarray(im.filter(ImageFilter.GaussianBlur(self.bloom)), np.float32)
        out = np.clip(img + glow * 0.25, 0, 255).astype(np.uint8)
        return out


if __name__ == "__main__":
    s = Caustics(cols=320, rows=90, loop_period=14.0, palette="ember")
    a = s.frame(0.0).astype(int)
    b = s.frame(s.period).astype(int)
    diff = int(np.abs(a - b).max())
    print(f"caustics seamless check: max |frame(0)-frame(period)| = {diff} (<=1 ok)")
    assert diff <= 1, "caustics loop seam too large!"
    print("ok")
