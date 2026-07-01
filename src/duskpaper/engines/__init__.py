"""Scene engines — pure numpy/PIL frame generators.

Every engine shares one contract:
    __init__(cols, rows, calm, loop_period, meteors, meteor_seed, palette)
    frame(t) -> (H, W, 3) uint8, with H = rows * 2

and loops seamlessly by construction: every time-dependence is an
integer-frequency function of tau = 2*pi*t/period, so frame(0) == frame(period)
exactly. No advected state, no drift, no seam.
"""

from . import aurora, embers, fireflies, flow, space

ALL_PALETTES = list(aurora.PALETTES)

# name -> (engine class, default palette, supported palettes, description)
SCENES = {
    "aurora": (aurora.Sky, "aurora", ALL_PALETTES,
               "northern lights over a dark ridge"),
    "galaxy": (space.Space, "aurora", ALL_PALETTES,
               "slow drift across a galaxy band"),
    "silk": (flow.Flow, "ice", list(flow.GRADIENTS),
             "curl-noise silk ribbons, deep blue"),
    "embers": (embers.Embers, "ember", list(embers.EMBERS),
               "warm sparks rising on true black"),
    "fireflies": (fireflies.Fireflies, "aurora", [],
                  "firefly meadow under a night sky (fixed palette)"),
}


def get(name):
    if name not in SCENES:
        raise SystemExit(
            f"unknown scene '{name}' (have: {', '.join(SCENES)})")
    return SCENES[name]
