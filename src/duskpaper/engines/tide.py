#!/usr/bin/env python3
"""
tide.py — "tide": a moonlit sea. Same interface as aurora.Sky.

The kit's first water scene. A low moon hangs over a dark horizon and lays a
shimmering glade down the water toward you; the sea heaves in slow swell, the
crests catch the moon as sparse silver glints, and away from the glade the odd
wave lights up with a faint bioluminescent glimmer. Dark, slow, quiet — built
to sit behind a working desktop. Drop in via the scene registry (`--scene tide`).

How the look is built:
  * A low-frequency SWELL field modulates the water's brightness into broad
    light/dark bands that heave — the sea breathing.
  * A high-frequency RIPPLE field, faded in toward the viewer and sharpened at
    its crests, becomes the specular glints. A static moon-glade cone concentrates
    those glints into the reflected road of light under the moon.
  * A sparse high-threshold BIOLUM field lights occasional crests outside the
    glade with a cold glimmer.
  * Sky gradient, moon disc + halo, water gradient, the glade's baked sheen and
    the star positions are computed once and never move.

Seamless looping (the load-bearing trick, mirrors flow.py / space.py):
  Every wave is a sum of sinusoids sin(2*pi*(fx*x + fy*pv) + phase + m*tau) with
  tau = 2*pi*t/period and INTEGER m. The spatial phase 2*pi*(fx*x + fy*pv)+phase
  is baked at init into a per-harmonic (sin, cos) pair; at frame time we only spin
  the m-th harmonic: value = sum_m [ S_m*cos(m*tau) + C_m*sin(m*tau) ]. At
  t=period, tau=2*pi and every harmonic returns exactly to its t=0 value. No
  advected state, no drift, no seam. Verified frame(0) == frame(period).

The vertical wave coordinate pv = k/(depth+eps) is a ground-plane perspective:
crests bunch toward the horizon and open out toward you. It only warps SPACE, so
it never touches the loop.

Renderer contract: __init__(cols, rows, calm, loop_period, meteors, meteor_seed,
palette) and frame(t) -> (H, W, 3) uint8, with H = rows*2, W = cols.
"""

import numpy as np
from PIL import Image, ImageFilter

TWO_PI = 2.0 * np.pi

# Per-palette sea. sky_top/sky_horizon = night-sky gradient; water_far/water_near
# = sea colour at the horizon fading to the deep water at your feet; moon = the
# disc + its glade; glint = the specular sparkle on the crests; biolum = the cold
# glimmer away from the glade. Tuned dark and low-contrast so the frame stays calm.
TIDES = {
    "ice": {  # silver moon on deep polar water (default)
        "sky_top": [4, 7, 20], "sky_horizon": [16, 26, 46],
        "water_far": [22, 38, 64], "water_near": [5, 11, 24],
        "moon": [222, 234, 250], "glint": [204, 226, 255], "biolum": [90, 210, 220],
    },
    "nord": {  # nord frost sea under a polar-night sky
        "sky_top": [13, 17, 26], "sky_horizon": [30, 41, 60],
        "water_far": [38, 52, 74], "water_near": [11, 17, 30],
        "moon": [216, 223, 234], "glint": [198, 214, 234], "biolum": [136, 192, 208],
    },
    "aurora": {  # green-tinted night sea
        "sky_top": [4, 10, 16], "sky_horizon": [12, 32, 36],
        "water_far": [15, 44, 44], "water_near": [3, 13, 15],
        "moon": [212, 238, 222], "glint": [186, 244, 214], "biolum": [120, 245, 180],
    },
    "gold": {  # harvest moon, warm honey road on dark water
        "sky_top": [12, 10, 18], "sky_horizon": [44, 30, 26],
        "water_far": [44, 34, 28], "water_near": [10, 8, 11],
        "moon": [255, 224, 150], "glint": [255, 236, 182], "biolum": [255, 202, 120],
    },
    "ember": {  # blood-moon sea, deep red-black
        "sky_top": [12, 6, 12], "sky_horizon": [46, 18, 20],
        "water_far": [40, 18, 20], "water_near": [10, 5, 8],
        "moon": [255, 152, 112], "glint": [255, 184, 146], "biolum": [255, 150, 112],
    },
}

# Star colours by temperature — mostly cool silver, a warm-amber minority (like a
# real sky). Palette-independent: stars are stars under any moon. (tint, weight).
STAR_TINTS = [
    ([0.78, 0.86, 1.00], 0.34),   # silver-blue
    ([0.90, 0.94, 1.00], 0.26),   # bright silver
    ([1.00, 1.00, 0.97], 0.12),   # white
    ([1.00, 0.82, 0.52], 0.18),   # warm amber
    ([1.00, 0.70, 0.40], 0.10),   # deep ember
]


class _Field:
    """A band-limited periodic wave field. n sinusoids with random direction,
    phase and INTEGER time-harmonic m; the spatial phase is baked once into a
    per-m (sin, cos) pair so frame time only spins the harmonics. value(tau) is
    normalised to roughly [-1, 1]."""

    def __init__(self, rng, n, hx, pv, fx_lo, fx_hi, fy_lo, fy_hi, m_choices):
        fx = rng.uniform(fx_lo, fx_hi, n)
        fy = rng.uniform(fy_lo, fy_hi, n)
        ph = rng.uniform(0.0, TWO_PI, n)
        m_arr = rng.choice(np.array(m_choices), n)
        amp = (rng.uniform(0.6, 1.0, n) / n).astype(np.float32)
        self.buckets = {}                        # m -> (S, C), both (H, W) f32
        for i in range(n):
            theta = TWO_PI * (fx[i] * hx + fy[i] * pv) + ph[i]
            s = (amp[i] * np.sin(theta)).astype(np.float32)
            c = (amp[i] * np.cos(theta)).astype(np.float32)
            m = int(m_arr[i])
            if m in self.buckets:
                S, C = self.buckets[m]
                self.buckets[m] = (S + s, C + c)
            else:
                self.buckets[m] = (s, c)
        self.norm = float(amp.sum()) or 1.0

    def value(self, tau):
        out = None
        for m, (S, C) in self.buckets.items():
            v = S * np.cos(m * tau) + C * np.sin(m * tau)
            out = v if out is None else out + v
        return out / self.norm


class Tide:
    """Moonlit sea. Mirrors aurora.Sky's constructor + frame(t) contract."""

    def __init__(self, cols, rows, calm=False, loop_period=None,
                 meteors=0, meteor_seed=7, palette="ice"):
        # meteors / meteor_seed accepted for signature-compatibility; seed varies
        # the sea. A low moon over water has one coherent scheme, tinted by palette.
        self.W = cols
        self.H = rows * 2
        W, H = self.W, self.H
        self.period = loop_period or 90.0
        pal = TIDES[palette]
        moon_col = np.array(pal["moon"], np.float32)
        self.glint_col = np.array(pal["glint"], np.float32)
        self.biolum_col = np.array(pal["biolum"], np.float32)

        rng = np.random.default_rng(meteor_seed * 719 + 5)

        xs = np.arange(W, dtype=np.float32)
        ys = np.arange(H, dtype=np.float32)
        Xg, Yg = np.meshgrid(xs, ys)                       # (H, W)
        hor = int(round(0.44 * H))                          # horizon row
        mx = 0.5 * W                                        # moon x (glade runs down centre)

        # depth 0 at the horizon -> 1 at your feet; ground-plane perspective coord.
        depth = np.clip((Yg - hor) / max(1, H - hor), 1e-3, 1.0)
        pv = 5.0 / (depth + 0.12)                           # crests bunch toward horizon
        hx = Xg / H                                         # square-pixel x, in height units
        self.water = (Yg >= hor).astype(np.float32)         # (H, W) sea mask

        # ── wave fields (spatial phase baked; only harmonics spin at frame time) ──
        speed = 0.6 if calm else 1.0
        self.swell = _Field(rng, 3, hx, pv, 0.0, 0.25, 0.10, 0.22, [1, 2])
        self.ripple = _Field(rng, 6, hx, pv, 0.0, 0.6, 0.7, 1.6,
                             [2, 3] if calm else [2, 3, 4, 5])
        self.biolum = _Field(rng, 3, hx, pv, 0.0, 0.8, 1.4, 2.6, [1, 2, 3])
        self.swell_amp = 0.16 * speed
        self.glint_thresh = 0.4
        self.bio_thresh = 0.78
        self.bio_amp = (0.5 if calm else 0.8)
        self.ripple_fade = (depth ** 0.7) * self.water      # ease dense near-horizon ripple

        # moon-glade cone: a soft column under the moon that widens with depth.
        sig = (0.02 + 0.13 * depth) * W
        glade = np.exp(-((Xg - mx) ** 2) / (2.0 * sig ** 2)) * (0.55 + 0.45 * (1 - depth))
        self.glade = glade * self.water
        self.ambient_glint = 0.09                           # faint sparkle off the road
        self.glade_gain = 1.6                               # bright sparkle on the road

        # ── baked backdrop: sky gradient, moon, water gradient, glade sheen ──
        img = np.empty((H, W, 3), np.float32)
        vfrac = (ys / max(1, hor - 1))[:, None, None]       # 0 at zenith -> 1 at horizon
        sky = (np.array(pal["sky_top"], np.float32) * (1 - np.clip(vfrac, 0, 1))
               + np.array(pal["sky_horizon"], np.float32) * np.clip(vfrac, 0, 1))
        img[:] = np.broadcast_to(sky, (H, W, 3))
        # water gradient: catch the moonlit sky at the horizon, deepen toward you.
        wfar = np.array(pal["water_far"], np.float32)
        wnear = np.array(pal["water_near"], np.float32)
        wcol = wfar * (1 - depth[..., None]) + wnear * depth[..., None]
        img = np.where(self.water[..., None] > 0, wcol, img).astype(np.float32)

        # moon disc + halo (in the sky), and its reflected sheen baked into the glade.
        my = 0.24 * H
        rad = 0.05 * H
        dist2 = (Xg - mx) ** 2 + (Yg - my) ** 2
        disc = np.exp(-dist2 / (2 * (rad * 0.55) ** 2))
        halo = 0.35 * np.exp(-np.sqrt(dist2) / (rad * 1.6))
        img += (disc + halo)[..., None] * moon_col
        img += (self.glade * 0.14)[..., None] * moon_col    # the luminous road, even when still

        # soft mist band hugging the horizon to melt the sky/sea seam.
        mist = np.exp(-((Yg - hor) / (0.03 * H)) ** 2)
        img += mist[..., None] * np.array(pal["sky_horizon"], np.float32) * 0.25
        self.backdrop = img

        # ── stars: sky only, away from the moon; silver + warm-amber, and a
        # two-harmonic twinkle (two integer freqs beat -> real scintillation) ──
        n_stars = max(24, int(W * H / 2300))
        sx = rng.uniform(0, W, n_stars)
        sy = rng.uniform(0, hor * 0.92, n_stars)
        far = ((sx - mx) ** 2 + (sy - my) ** 2) > (rad * 4.0) ** 2   # keep stars out of the moon's glow
        self.sx, self.sy = sx[far], sy[far]
        ns = self.sx.size
        self.s_mag = rng.uniform(0.2, 0.7, ns) ** 1.3
        self.s_m1 = rng.integers(1, 4, ns).astype(np.float32)     # slow pulse
        self.s_m2 = rng.integers(3, 7, ns).astype(np.float32)     # fast shimmer
        self.s_p1 = rng.uniform(0, TWO_PI, ns)
        self.s_p2 = rng.uniform(0, TWO_PI, ns)
        tints = np.array([t for t, _ in STAR_TINTS], np.float32)
        w = np.array([wt for _, wt in STAR_TINTS])
        idx = rng.choice(len(tints), ns, p=w / w.sum())
        self.s_col = tints[idx]                                    # (ns, 3)
        self.s_mag *= np.where(idx >= 3, 1.15, 1.0)               # warm ones a touch brighter
        # a small bright core + soft arms so a star survives the final blur and
        # keeps its colour (a lone pixel would just wash out grey).
        self.star_kernel = [(0, 0, 1.0), (1, 0, 0.5), (-1, 0, 0.5),
                            (0, 1, 0.5), (0, -1, 0.5), (1, 1, 0.28),
                            (-1, 1, 0.28), (1, -1, 0.28), (-1, -1, 0.28)]

        self.blur = max(0.6, H / 520.0)                     # a hair of smooth kills moire

    def frame(self, t):
        tau = TWO_PI * t / self.period
        img = self.backdrop.copy()

        # star flicker (sky): two integer harmonics beat into a lively twinkle
        tw = (0.5 + 0.34 * np.sin(self.s_m1 * tau + self.s_p1)
              + 0.20 * np.sin(self.s_m2 * tau + self.s_p2))
        np.clip(tw, 0.0, None, out=tw)
        sxi = np.round(self.sx).astype(int)
        syi = np.round(self.sy).astype(int)
        val = (self.s_mag * tw * 300.0)[:, None] * self.s_col
        for dx, dy, w in self.star_kernel:
            xi, yi = sxi + dx, syi + dy
            ok = (xi >= 0) & (xi < self.W) & (yi >= 0) & (yi < self.H)
            np.add.at(img, (yi[ok], xi[ok]), val[ok] * w)

        # slow swell heaves the water's brightness into broad bands
        swell = self.swell.value(tau)
        mod = 1.0 + self.swell_amp * swell
        img *= np.where(self.water[..., None] > 0, mod[..., None], 1.0)

        # specular glints: sharpen the ripple crests, concentrate them in the glade
        ripple = self.ripple.value(tau) * self.ripple_fade
        crest = np.clip(ripple - self.glint_thresh, 0.0, None) ** 2
        glint = crest * (self.ambient_glint + self.glade_gain * self.glade)
        img += glint[..., None] * self.glint_col

        # bioluminescence: sparse cold glimmers on crests, away from the glade
        g = self.biolum.value(tau)
        spark = np.clip(g - self.bio_thresh, 0.0, None) ** 3
        spark *= self.water * (1.0 - self.glade) * np.clip(swell, 0.0, None)
        img += spark[..., None] * self.biolum_col * self.bio_amp

        np.clip(img, 0, 255, out=img)
        im = Image.fromarray(img.astype(np.uint8), "RGB").filter(
            ImageFilter.GaussianBlur(self.blur))
        return np.asarray(im, np.uint8)


if __name__ == "__main__":
    eng = Tide(cols=320, rows=100, loop_period=90.0)
    a = eng.frame(0.0).astype(int)
    b = eng.frame(90.0).astype(int)
    delta = int(np.abs(a - b).max())
    print(f"tide seamless check: max |frame(0)-frame(period)| = {delta} (<=1 ok)")
    assert delta <= 1, f"tide loop seam too large: {delta}"
    c = eng.frame(11.0).astype(int)
    assert not (a == c).all(), "frame(0) == frame(11): scene is not moving"
    print("tide motion check: ok")
