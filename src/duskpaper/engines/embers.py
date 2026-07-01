#!/usr/bin/env python3
"""
embers.py — "embers": warm sparks drifting UPWARD with a gentle sway and flicker,
an additive fire-side / magical-atmosphere overlay. Same interface as aurora.Sky.

Glowing coal-sparks rise, sway as if on a draught, and flicker on their own
rhythms, fading in near the bottom and burning out near the top. Cozy fire-side
or enchanted air. Tinted to the kit's palettes (ember = hot coals). `--scene
embers`. Built on the same splat→blur idiom as `sparkle`, but with continuous
upward drift instead of fixed twinkling.

How the look is built (additive light on true black):
  * N particles splatted into a buffer and Gaussian-blurred into soft glowing
    dots, tinted warm and summed over black.
  * Each particle drifts upward an INTEGER number of frame-heights per loop, so it
    re-enters the bottom exactly as it exits the top (seamless). A small
    integer-omega horizontal sway and a flicker give life.
  * A vertical edge-fade window (0 at the very top/bottom rows, flat in the middle)
    does double duty: it keeps the top/bottom edges genuinely black (additive-
    transparent background) AND hides the wrap — a particle is invisible exactly
    where it teleports from top back to bottom, so there is no pop.

Seamless looping: upward drift wraps an integer number of times across the period;
sway and flicker enter only as integer-omega*tau. So frame(0) == frame(period).
Verified by the __main__ self-test (seam + true-black top/bottom edges).

Attribute names deliberately avoid `layers` / `ridge_top` — render_still.py pokes
those by hasattr (aurora-only knobs).

Renderer contract: __init__(cols, rows, calm, loop_period, meteors, meteor_seed,
palette) and frame(t) -> (H, W, 3) uint8, with H = rows*2, W = cols.
"""

import numpy as np
from scipy.ndimage import gaussian_filter

TWO_PI = 2.0 * np.pi

# Per-palette ember colours: warm body + hot core. ember is the canonical look.
EMBERS = {
    "ember":  {"body": (210, 84, 28), "core": (255, 196, 120)},
    "gold":   {"body": (220, 150, 50), "core": (255, 236, 180)},
    "ice":    {"body": (90, 170, 230), "core": (210, 240, 255)},
    "aurora": {"body": (70, 200, 120), "core": (210, 255, 220)},
}


class Embers:
    """Warm sparks drifting upward on black. Mirrors aurora.Sky's API."""

    def __init__(self, cols, rows, calm=False, loop_period=None,
                 meteors=0, meteor_seed=7, palette="ember"):
        self.W = cols
        self.H = rows * 2
        W, H = self.W, self.H
        self.period = loop_period or 14.0
        c = EMBERS.get(palette, EMBERS["ember"])
        self.body = np.array(c["body"], np.float32)
        self.core = np.array(c["core"], np.float32)

        rng = np.random.default_rng(meteor_seed * 661 + 29)
        # ~150 sparks at 1080p: dense enough to read as embers, sparse enough to
        # stay an elegant drifting overlay, not a wall of fire. (The per-spark gain
        # below makes each visible regardless of count, so this need not be high.)
        n = max(40, int(W * H // 6000))
        self.x0 = rng.uniform(0, W, n)
        self.y0 = rng.uniform(0, H, n)
        # Each spark rises an INTEGER number of frame-heights per loop → seamless.
        self.wraps = rng.integers(1, 3, n).astype(np.float64)
        self.sway_amp = rng.uniform(0.01, 0.05, n) * W
        self.sway_om = rng.choice(np.array([1.0, 2.0] if not calm else [1.0]), n)
        self.sway_ph = rng.uniform(0.0, TWO_PI, n)
        self.flick_om = rng.choice(np.array([1.0, 2.0, 3.0] if not calm else [1.0, 2.0]), n)
        self.flick_ph = rng.uniform(0.0, TWO_PI, n)
        self.mag = rng.uniform(0.6, 1.0, n)
        self.gamma = rng.uniform(1.2, 2.2, n)    # gentle pulse, not a hard blink

        self.sigma = max(1.2, H / 130.0)         # soft-dot core radius
        self.sigma_halo = max(3.0, H / 42.0)     # wider warm halo for glow visibility
        # Vertical edge-fade: 0 at top/bottom rows, flat 1 in the interior.
        y = np.arange(H, dtype=np.float32)
        edge = max(2.0, 0.12 * H)
        fade = np.clip(np.minimum(y / edge, (H - 1 - y) / edge), 0.0, 1.0)
        self.fade = (fade * fade * (3 - 2 * fade)).astype(np.float32)   # smoothstep

    def frame(self, t):
        tau = TWO_PI * (t / self.period)
        frac = t / self.period
        x = self.x0 + self.sway_amp * np.sin(self.sway_om * tau + self.sway_ph)
        y = (self.y0 - self.wraps * self.H * frac) % self.H
        # Embers glow steadily and pulse — a raised floor keeps them lit, not blinking.
        pulse = (0.5 + 0.5 * np.sin(self.flick_om * tau + self.flick_ph)) ** self.gamma
        flick = self.mag * (0.4 + 0.6 * pulse)

        xi = np.mod(x.astype(np.int64), self.W)
        yi = np.mod(y.astype(np.int64), self.H)
        buf = np.zeros((self.H, self.W), np.float32)
        np.add.at(buf, (yi, xi), flick.astype(np.float32))
        # Blur conserves energy, so a 1-px splat dilutes to a tiny peak; the gains
        # (~2*pi*sigma^2) undo that so an ember core lands near full brightness.
        core = gaussian_filter(buf, self.sigma, mode="constant") * (2 * np.pi * self.sigma ** 2)
        halo = gaussian_filter(buf, self.sigma_halo, mode="constant") \
            * (2 * np.pi * self.sigma_halo ** 2)

        b = 0.85 * core + 0.30 * halo            # brightness field, ~1 at ember cores
        img = b[..., None] * self.body[None, None]
        img += np.clip(b - 0.7, 0.0, None)[..., None] * self.core[None, None] * 1.3
        img *= self.fade[:, None, None]          # black top/bottom edges + hide wrap
        np.clip(img, 0, 255, out=img)
        return img.astype(np.uint8)


if __name__ == "__main__":
    s = Embers(cols=320, rows=90, loop_period=14.0, palette="ember")
    a = s.frame(0.0).astype(int)
    b = s.frame(s.period).astype(int)
    diff = int(np.abs(a - b).max())
    print(f"embers seamless check: max |frame(0)-frame(period)| = {diff} (<=1 ok)")
    assert diff <= 1, "embers loop seam too large!"
    # additive-cleanliness: top/bottom edges (and corners) black at any sampled t.
    for tt in (0.0, 3.3, 6.6, 9.9):
        f = s.frame(tt)
        edge = int(max(f[0].max(), f[-1].max()))
        assert edge == 0, f"embers top/bottom edge not black at t={tt} (max={edge})"
    # particles must actually rise: mean brightness-weighted y decreases a little
    print("embers edges black at all sampled t: ok")
    print("ok")
