"""Scene engines — pure numpy/PIL frame generators.

Every engine shares one contract:
    __init__(cols, rows, calm, loop_period, meteors, meteor_seed, palette)
    frame(t) -> (H, W, 3) uint8, with H = rows * 2

and loops seamlessly by construction: every time-dependence is an
integer-frequency function of tau = 2*pi*t/period, so frame(0) == frame(period)
exactly. No advected state, no drift, no seam.
"""

from . import aurora, embers, fireflies, flow, space

SCENES = {
    "aurora": (aurora.Sky, "aurora", "northern lights over a dark ridge"),
    "galaxy": (space.Space, "aurora", "slow drift across a galaxy band"),
    "silk": (flow.Flow, "ice", "curl-noise silk ribbons, deep blue"),
    "embers": (embers.Embers, "ember", "warm sparks rising on true black"),
    "fireflies": (fireflies.Fireflies, "aurora", "firefly meadow under a night sky"),
}

PALETTES = list(aurora.PALETTES)


def get(name):
    if name not in SCENES:
        raise SystemExit(
            f"unknown scene '{name}' (have: {', '.join(SCENES)})")
    return SCENES[name]
