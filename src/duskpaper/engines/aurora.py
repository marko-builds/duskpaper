#!/usr/bin/env python3
"""
Aurora Borealis — a terminal screensaver.

Renders shifting curtains of northern light to your terminal using 24-bit
truecolor and the half-block trick (▀) for double vertical resolution.
No game, no goal. Leave it running in a pane and watch the sky breathe.

    python3 aurora.py            # fill the terminal, ~24 fps
    python3 aurora.py --fps 30   # chase a smoother sky
    python3 aurora.py --calm     # slower drift, fewer ripples

Quit with q or Ctrl-C.

Dependency-light: just numpy. Works in any truecolor terminal (kitty,
alacritty, foot, ghostty, wezterm, modern xterm). If colors look blocky,
make sure COLORTERM=truecolor.
"""

import argparse
import os
import select
import signal
import sys
import termios
import tty

import numpy as np

# ── ANSI plumbing ────────────────────────────────────────────────────────────
ESC = "\x1b"
HIDE_CURSOR = f"{ESC}[?25l"
SHOW_CURSOR = f"{ESC}[?25h"
ALT_SCREEN = f"{ESC}[?1049h"
MAIN_SCREEN = f"{ESC}[?1049l"
HOME = f"{ESC}[H"
UPPER_HALF = "▀"  # ▀  fg paints the top pixel, bg paints the bottom

# ── colour palettes ──────────────────────────────────────────────────────────
# Colour follows altitude within a hanging curtain (0 = top, 1 = bottom). Each
# palette also tints the night sky so the whole frame stays coherent (an ember
# aurora over a cold navy sky would look wrong). p/r/g/b = the curtain ramp;
# sky_base/sky_amp = sky colour at the horizon plus what fades in toward zenith.
PALETTES = {
    # Classic green/violet: oxygen green low, nitrogen violet high, teal seam.
    "aurora": {
        "p": [0.00, 0.12, 0.30, 0.55, 0.80, 1.00],
        "r": [150, 90, 35, 30, 25, 8],
        "g": [45, 110, 200, 235, 180, 70],
        "b": [180, 205, 200, 130, 80, 35],
        "sky_base": [6, 8, 18], "sky_amp": [8, 10, 22],
    },
    # Rare red/magenta aurora: magenta crown, fiery red and orange below.
    "ember": {
        "p": [0.00, 0.15, 0.35, 0.60, 0.82, 1.00],
        "r": [200, 225, 235, 240, 180, 60],
        "g": [40, 60, 75, 120, 70, 20],
        "b": [150, 95, 60, 40, 22, 12],
        "sky_base": [10, 6, 12], "sky_amp": [16, 8, 14],
    },
    # Warm gold: deep amber low into bright honey-gold and pale white-gold.
    # (The sparkle/glitter scene's canonical tint; also a valid aurora sky.)
    "gold": {
        "p": [0.00, 0.15, 0.35, 0.60, 0.82, 1.00],
        "r": [120, 180, 225, 245, 235, 60],
        "g": [70, 120, 165, 205, 175, 40],
        "b": [20, 35, 55, 110, 90, 14],
        "sky_base": [10, 8, 6], "sky_amp": [16, 12, 8],
    },
    # Nord: Frost blues + Aurora-group accents over a Polar Night slate sky.
    "nord": {
        "p": [0.00, 0.18, 0.40, 0.62, 0.82, 1.00],
        "r": [180, 129, 136, 143, 163, 90],
        "g": [142, 161, 192, 188, 190, 110],
        "b": [173, 193, 208, 187, 140, 75],
        "sky_base": [12, 14, 18], "sky_amp": [20, 24, 34],
    },
    # Cold polar blue: deep blue crown into cyan and pale ice at the hem.
    "ice": {
        "p": [0.00, 0.15, 0.35, 0.60, 0.82, 1.00],
        "r": [60, 60, 90, 160, 130, 20],
        "g": [90, 150, 210, 235, 185, 45],
        "b": [200, 230, 235, 240, 160, 75],
        "sky_base": [6, 9, 22], "sky_amp": [8, 14, 26],
    },
}

# Module-level default ramp, kept for any external caller.
RAMP_P = np.array(PALETTES["aurora"]["p"])
RAMP_R = np.array(PALETTES["aurora"]["r"])
RAMP_G = np.array(PALETTES["aurora"]["g"])
RAMP_B = np.array(PALETTES["aurora"]["b"])


def ramp(p):
    """Map curtain-position p (any shape) → (..., 3) float RGB (default palette)."""
    r = np.interp(p, RAMP_P, RAMP_R)
    g = np.interp(p, RAMP_P, RAMP_G)
    b = np.interp(p, RAMP_P, RAMP_B)
    return np.stack([r, g, b], axis=-1)


class Sky:
    """Holds everything that depends on terminal size, and renders one frame."""

    def __init__(self, cols, rows, calm=False, loop_period=None,
                 meteors=0, meteor_seed=7, palette="aurora"):
        pal = PALETTES[palette]
        self._p = np.array(pal["p"])
        self._r = np.array(pal["r"])
        self._g = np.array(pal["g"])
        self._b = np.array(pal["b"])
        self.cols = cols
        self.rows = rows
        self.W = cols          # pixel width  = terminal columns
        self.H = rows * 2       # pixel height = terminal rows * 2 (half-blocks)
        self.calm = calm
        # For a seamless video loop we quantize every time-varying speed to an
        # integer multiple of the loop's fundamental frequency (2π / period), so
        # frame t and frame t+period are identical. Live mode leaves it None for
        # organic, never-repeating drift. Use a period ≥ ~57s so even the slowest
        # curtain wave still gets at least one whole cycle.
        self.loop_period = loop_period
        self._omega = (2 * np.pi / loop_period) if loop_period else None

        x = np.arange(self.W)
        y = np.arange(self.H)
        self.X, self.Y = np.meshgrid(x, y)        # (H, W)
        self.fx = (x / self.W).astype(np.float64)  # 0..1 across the sky

        # Curtain layers: each is (top-edge waves, drape length, drift speeds).
        # Stacking a few at different scales gives parallax and depth.
        speed = 0.45 if calm else 1.0
        self.layers = [
            # base_y, length, [(freq, amp, spd), ...] edge waves, ray_freq, ray_speed, alpha
            (0.18, 0.55, [(1.3, 0.05, 0.21), (2.7, 0.03, -0.13), (5.1, 0.015, 0.31)], 23, 0.7, 1.00),
            (0.30, 0.42, [(0.9, 0.06, -0.15), (2.1, 0.035, 0.19), (4.3, 0.02, -0.27)], 17, -0.5, 0.75),
            (0.42, 0.32, [(1.7, 0.04, 0.11), (3.3, 0.025, -0.23), (6.5, 0.012, 0.17)], 31, 0.9, 0.55),
        ]
        # In loop mode t must hit harmonic phases exactly, so speed stays 1.0;
        # a calmer video comes from a longer loop_period, not a time rescale.
        self.speed = 1.0 if loop_period else speed

        # Static sky gradient (dark navy at the zenith → near-black horizon).
        vert = (y / self.H)[:, None]
        sky = np.empty((self.H, self.W, 3))
        base, amp = pal["sky_base"], pal["sky_amp"]
        for c in range(3):
            sky[..., c] = base[c] + amp[c] * (1 - vert[..., 0])[:, None]
        self.sky = sky

        # Starfield: sparse, deterministic, gently twinkling.
        rng = np.random.default_rng(7)
        n_stars = max(20, (self.W * self.H) // 220)
        sy = rng.integers(0, int(self.H * 0.7), n_stars)   # keep stars up high
        sx = rng.integers(0, self.W, n_stars)
        self.star_yx = (sy, sx)
        self.star_mag = rng.uniform(0.3, 1.0, n_stars)
        self.star_phase = rng.uniform(0, 2 * np.pi, n_stars)
        self.star_twk = rng.uniform(1.5, 4.0, n_stars)

        # Mountain silhouette along the bottom — a horizon to hang the light over.
        ridge = (
            0.5
            + 0.30 * np.sin(self.fx * 3.1 + 0.6)
            + 0.12 * np.sin(self.fx * 7.7 + 2.0)
            + 0.06 * np.sin(self.fx * 15.3 + 4.1)
        )
        ridge = ridge / ridge.max()
        self.ridge_top = (self.H - 1) - (ridge * self.H * 0.16).astype(int)

        # Shooting stars: rare, scripted streaks scattered across the timeline.
        # Each is born and dies well inside the span, so none straddle the loop
        # seam — the wrap stays seamless even with meteors on.
        self.meteors = []
        if meteors > 0:
            span = loop_period or 60.0
            mrng = np.random.default_rng(meteor_seed)
            times = np.sort(mrng.uniform(3.0, span - 3.0, meteors))
            for t0 in times:
                dur = mrng.uniform(0.8, 1.4)
                theta = mrng.uniform(0.20, 0.55) * np.pi   # angle below horizon
                sx = 1.0 if mrng.random() < 0.5 else -1.0
                dist = mrng.uniform(0.25, 0.50) * self.W
                speed = dist / dur
                self.meteors.append({
                    "t0": float(t0), "dur": float(dur),
                    "x0": float(mrng.uniform(0.05, 0.95) * self.W),
                    "y0": float(mrng.uniform(0.02, 0.42) * self.H),
                    "vx": float(speed * np.cos(theta) * sx),
                    "vy": float(speed * np.sin(theta)),
                    "tail": float(mrng.uniform(0.12, 0.20) * self.W),
                    "bright": float(mrng.uniform(0.8, 1.3)),
                })

    def _draw_meteors(self, img, t):
        warm = np.array([255.0, 247.0, 228.0])
        for m in self.meteors:
            p = (t - m["t0"]) / m["dur"]
            if p < 0.0 or p > 1.0:
                continue
            env = np.sin(np.pi * p) ** 0.5            # fade in and out, no pop
            hx = m["x0"] + m["vx"] * (t - m["t0"])
            hy = m["y0"] + m["vy"] * (t - m["t0"])
            speed = (m["vx"] ** 2 + m["vy"] ** 2) ** 0.5 + 1e-9
            dx, dy = m["vx"] / speed, m["vy"] / speed

            # ~1 sample per pixel so the tail doesn't stack into a solid bar.
            k = max(2, int(round(m["tail"])))
            s = np.linspace(0.0, 1.0, k)
            px = hx - dx * m["tail"] * s              # trail behind the head
            py = hy - dy * m["tail"] * s
            taper = (1.0 - s) ** 2.0 * env * m["bright"]   # wispy comet fade
            for ox, oy, w in ((0, 0, 1.0), (1, 0, 0.22), (-1, 0, 0.22),
                              (0, 1, 0.22), (0, -1, 0.22)):
                xi = np.round(px + ox).astype(int)
                yi = np.round(py + oy).astype(int)
                ok = (xi >= 0) & (xi < self.W) & (yi >= 0) & (yi < self.H)
                np.add.at(img, (yi[ok], xi[ok]),
                          warm[None, :] * (taper[ok] * w * 210.0)[:, None])

            # Bright bloom at the head — the spark itself.
            hxi, hyi = int(round(hx)), int(round(hy))
            for ox in range(-2, 3):
                for oy in range(-2, 3):
                    xi, yi = hxi + ox, hyi + oy
                    if 0 <= xi < self.W and 0 <= yi < self.H:
                        g = np.exp(-(ox * ox + oy * oy) / 2.2)
                        img[yi, xi] += warm * (g * env * m["bright"] * 255.0)

    def _ramp(self, p):
        """Map curtain-position p → (..., 3) float RGB using this sky's palette."""
        r = np.interp(p, self._p, self._r)
        g = np.interp(p, self._p, self._g)
        b = np.interp(p, self._p, self._b)
        return np.stack([r, g, b], axis=-1)

    def q(self, speed):
        """Quantize an angular speed to the loop's harmonic grid (no-op live)."""
        if self._omega is None:
            return speed
        sp = np.asarray(speed, dtype=np.float64)
        k = np.round(sp / self._omega)
        k = np.where((k == 0) & (sp != 0), np.sign(sp), k)   # never freeze a moving term
        out = k * self._omega
        return out if np.ndim(speed) else float(out)

    def frame(self, t):
        t *= self.speed
        img = self.sky.copy()

        # Twinkling stars.
        sy, sx = self.star_yx
        twk = 0.55 + 0.45 * np.sin(t * self.q(self.star_twk) + self.star_phase)
        bright = (self.star_mag * twk * 200.0)[:, None]
        img[sy, sx, :] += bright * np.array([0.85, 0.9, 1.0])

        H = self.H
        for base_y, length, waves, ray_f, ray_s, alpha in self.layers:
            top = np.full(self.W, base_y)
            for freq, amp, spd in waves:
                top = top + amp * np.sin(self.fx * freq * 2 * np.pi + t * self.q(spd))
            top_px = top * H                                   # (W,)
            L = length * H

            rel = (self.Y - top_px[None, :]) / L               # 0 at edge → 1 at hem
            inside = (rel >= 0) & (rel <= 1)

            # Brightness: fades toward the hem, with a soft lip at the top edge.
            b = np.clip(1.0 - rel, 0.0, 1.0)
            b *= np.clip(rel / 0.04, 0.0, 1.0)                 # soften the hard top line
            b *= inside

            # Vertical filaments — the rays that make an aurora shimmer.
            rays = (
                0.55
                + 0.30 * np.sin(self.fx * ray_f + t * self.q(ray_s))
                + 0.15 * np.sin(self.fx * ray_f * 2.3 - t * self.q(ray_s * 1.7))
            )
            rays = np.clip(rays, 0.0, 1.0) ** 1.6              # sharpen into streaks
            b *= rays[None, :]

            # Slow breathing of the whole curtain.
            b *= 0.75 + 0.25 * np.sin(t * self.q(0.6) + base_y * 9)

            col = self._ramp(rel) * (b * alpha)[..., None]
            img += col

        # Shooting stars streak over the curtains.
        if self.meteors:
            self._draw_meteors(img, t)

        # Mountains occlude the light (drawn last, near-black).
        below = self.Y >= self.ridge_top[None, :]
        img[below] = np.array([5, 6, 11])

        np.clip(img, 0, 255, out=img)
        return img.astype(np.uint8)


def render(img, rows, cols):
    """Pack the pixel buffer into ▀ half-block lines with run-length colour."""
    # Quantize a touch: longer colour runs → fewer escape codes → faster.
    q = (img >> 2) << 2
    top = q[0::2]   # (rows, cols, 3) → upper pixel of each cell  (foreground)
    bot = q[1::2]   # lower pixel of each cell                    (background)

    out = [HOME]
    last_fg = last_bg = None
    for r in range(rows):
        tr = top[r]
        br = bot[r]
        for c in range(cols):
            fr, fg_, fb = tr[c]
            br_, bg_, bb = br[c]
            fg = (fr, fg_, fb)
            bg = (br_, bg_, bb)
            if fg != last_fg:
                out.append(f"{ESC}[38;2;{fr};{fg_};{fb}m")
                last_fg = fg
            if bg != last_bg:
                out.append(f"{ESC}[48;2;{br_};{bg_};{bb}m")
                last_bg = bg
            out.append(UPPER_HALF)
        out.append(f"{ESC}[0m")        # reset at line end avoids bg bleed on wrap
        last_fg = last_bg = None
    return "".join(out)


def main():
    ap = argparse.ArgumentParser(description="Aurora borealis terminal screensaver.")
    ap.add_argument("--fps", type=float, default=24.0, help="target frames per second")
    ap.add_argument("--calm", action="store_true", help="slower, gentler drift")
    ap.add_argument("--palette", default="aurora", choices=list(PALETTES),
                    help="colour theme (try 'nord')")
    args = ap.parse_args()

    fd = sys.stdin.fileno()
    isatty = sys.stdin.isatty()
    old_term = termios.tcgetattr(fd) if isatty else None

    stop = {"now": False}
    signal.signal(signal.SIGINT, lambda *_: stop.__setitem__("now", True))

    w = sys.stdout.write
    w(ALT_SCREEN + HIDE_CURSOR)
    w(f"{ESC}[2J")
    sys.stdout.flush()

    try:
        if isatty:
            tty.setcbreak(fd)
        cols, rows = os.get_terminal_size()
        sky = Sky(cols, rows, calm=args.calm, palette=args.palette)

        dt = 1.0 / max(1.0, args.fps)
        t = 0.0
        # Drive time off a wall clock would be smoother, but Date/time helpers
        # are flaky here; a fixed step gives a steady, reproducible drift.
        while not stop["now"]:
            # Handle resize on the fly.
            nc, nr = os.get_terminal_size()
            if (nc, nr) != (cols, rows):
                cols, rows = nc, nr
                sky = Sky(cols, rows, calm=args.calm, palette=args.palette)
                w(f"{ESC}[2J")

            img = sky.frame(t)
            w(render(img, rows, cols))
            sys.stdout.flush()
            t += dt

            # Sleep via select so a keypress wakes us instantly.
            if isatty:
                ready, _, _ = select.select([fd], [], [], dt)
                if ready and sys.stdin.read(1).lower() == "q":
                    break
            else:
                import time
                time.sleep(dt)
    finally:
        if isatty and old_term is not None:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_term)
        w(SHOW_CURSOR + MAIN_SCREEN)
        sys.stdout.flush()


if __name__ == "__main__":
    main()
