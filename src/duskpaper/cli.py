#!/usr/bin/env python3
"""duskpaper: generative animated wallpapers for Wayland/Hyprland.

Renders seamless video loops from procedural scene engines (numpy piped into
ffmpeg) and runs them as your desktop wallpaper via mpvpaper. Every wallpaper
is generated on YOUR machine at YOUR resolution with YOUR seed. No downloads,
no video files of someone else's desktop.

    duskpaper list
    duskpaper render aurora
    duskpaper render silk --seed 3 --seconds 120
    duskpaper set fireflies          # render (cached) + set as live wallpaper
    duskpaper off                    # back to your static wallpaper
"""

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from . import __version__, engines

CACHE = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "duskpaper"
STATE = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "duskpaper"
MPV_OPTS = "no-audio loop hwdec=auto"

# static-wallpaper daemons we stop on `set` and restore on `off`
WALLPAPER_DAEMONS = ("swaybg", "hyprpaper", "swww-daemon", "wpaperd")

# rough render minutes per scene at the default --native 1280 (scene dominates
# cost, not monitor resolution; scale with your CPU's single-core speed)
RENDER_MINUTES = {"fireflies": 1, "galaxy": 3, "tide": 8, "embers": 10, "aurora": 12, "silk": 20}


# ── monitor / process helpers ────────────────────────────────────────────────

def detect_resolution():
    """Focused monitor's pixel resolution via hyprctl; falls back to 1920x1080."""
    try:
        mons = json.loads(subprocess.check_output(
            ["hyprctl", "monitors", "-j"], text=True))
        m = next((m for m in mons if m.get("focused")), mons[0])
        return int(m["width"]), int(m["height"])
    except Exception:
        return 1920, 1080


def _pids(name):
    try:
        out = subprocess.check_output(["pgrep", "-x", name], text=True)
        return [int(p) for p in out.split()]
    except subprocess.CalledProcessError:
        return []


def _kill(name):
    for pid in _pids(name):           # explicit PIDs, never pkill -f
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def _detached(cmd):
    """Launch a wallpaper daemon detached from this process. Prefers uwsm-app
    (systemd user scope — survives the shell and the session) when present."""
    if shutil.which("uwsm-app"):
        cmd = ["uwsm-app", "--"] + cmd
    subprocess.Popen(["setsid"] + cmd, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


# ── rendering ────────────────────────────────────────────────────────────────

def render(scene, out, width, height, seconds=120, fps=30, native=1280,
           crf=18, seed=7, palette=None, quiet=False):
    """Render one seamless loop to `out` (h264 yuv420p mp4)."""
    cls, default_pal, _, _ = engines.get(scene)
    palette = palette or default_pal
    W, H = width - width % 2, height - height % 2
    nw = min(native, W)
    nh = (round(nw * H / W) // 2) * 2
    n_frames = int(round(seconds * fps))
    eng = cls(cols=nw, rows=nh // 2, loop_period=n_frames / fps,
              meteors=(2 if scene == "galaxy" else 0),
              meteor_seed=seed, palette=palette)

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    # tide's large, smooth moon gradient bands in 8-bit h264, and the encoder
    # re-quantizes the banding differently each frame so the glow visibly
    # flickers. No crf fixes it (8-bit is the ceiling); HEVC 10-bit adds the
    # precision that removes the banding. Main10 is hardware-decoded on any
    # modern GPU, so the wallpaper stays light. Other scenes keep 8-bit h264.
    if scene == "tide":
        vcodec = ["-c:v", "libx265", "-pix_fmt", "yuv420p10le",
                  "-x265-params", "log-level=none", "-tag:v", "hvc1"]
    else:
        vcodec = ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    ff = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "rawvideo", "-pixel_format", "rgb24",
         "-video_size", f"{W}x{H}", "-framerate", str(fps), "-i", "-",
         "-an", *vcodec,
         "-crf", str(crf), "-preset", "medium", "-movflags", "+faststart",
         str(out)],
        stdin=subprocess.PIPE)
    if not quiet:
        print(f"rendering {scene}: {n_frames} frames, native {nw}x{nh} -> {W}x{H}")
        print(f"  (one-time render, roughly {RENDER_MINUTES.get(scene, 10)} min "
              f"on a modern CPU; cached after)")
    try:
        for i in range(n_frames):
            img = eng.frame(i / fps)
            if (nw, nh) != (W, H):
                img = np.asarray(Image.fromarray(img, "RGB")
                                 .resize((W, H), Image.LANCZOS))
            ff.stdin.write(img.tobytes())
            if not quiet and i % (fps * 5) == 0:
                print(f"\r  {100 * i / n_frames:5.1f}%", end="", flush=True)
        ff.stdin.close()
        ff.wait()
        if not quiet:
            print("\r  100.0%")
    except BrokenPipeError:
        sys.exit("ffmpeg failed")
    if ff.returncode:
        sys.exit(ff.returncode)
    return out


# ── live wallpaper management ────────────────────────────────────────────────

def _save_daemon_cmdlines():
    """Remember how the running static-wallpaper daemon(s) were launched, so
    `off` can restore the desktop exactly as it was."""
    saved = []
    for name in WALLPAPER_DAEMONS:
        for pid in _pids(name):
            try:
                args = Path(f"/proc/{pid}/cmdline").read_bytes()
                saved.append([a for a in args.decode().split("\0") if a])
            except OSError:
                pass
    if saved:
        STATE.mkdir(parents=True, exist_ok=True)
        (STATE / "restore.json").write_text(json.dumps(saved))
    return saved


def start_live(video):
    video = Path(video).resolve()
    if not video.is_file():
        sys.exit(f"no such video: {video}")
    if not shutil.which("mpvpaper"):
        sys.exit("mpvpaper not found. Install it first (AUR: mpvpaper)")
    if not (STATE / "restore.json").exists():
        _save_daemon_cmdlines()
    _kill("mpvpaper")
    for name in WALLPAPER_DAEMONS:
        _kill(name)
    _detached(["mpvpaper", "-p", "-o", MPV_OPTS, "*", str(video)])
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / "video").write_text(str(video))
    (STATE / "enabled").touch()


def stop_live():
    (STATE / "enabled").unlink(missing_ok=True)
    _kill("mpvpaper")
    # restore the static wallpaper exactly as it ran before `set`
    restore = STATE / "restore.json"
    if restore.exists():
        for cmd in json.loads(restore.read_text()):
            if shutil.which(cmd[0]):
                _detached(cmd)
        restore.unlink()
        return
    # fallback: Omarchy's current-theme wallpaper, if that system is present
    bg = Path.home() / ".config/omarchy/current/background"
    if bg.exists() and shutil.which("swaybg"):
        _detached(["swaybg", "-i", str(bg), "-m", "fill"])


# ── CLI ──────────────────────────────────────────────────────────────────────

def _parse_res(res):
    if res == "auto":
        return detect_resolution()
    try:
        w, h = res.lower().replace("×", "x").split("x")
        return int(w), int(h)
    except ValueError:
        sys.exit(f"bad --res '{res}' (expected WxH, e.g. 2560x1440)")


def main():
    ap = argparse.ArgumentParser(prog="duskpaper", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"duskpaper {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="list scenes and palettes")

    for name in ("render", "set"):
        p = sub.add_parser(name, help="render a loop" if name == "render"
                           else "render (cached) + run as live wallpaper")
        p.add_argument("scene", choices=list(engines.SCENES) + (
            [] if name == "render" else ["off"]))
        p.add_argument("--res", default="auto",
                       help="WxH (default: detect via hyprctl; on other "
                            "compositors pass it explicitly)")
        p.add_argument("--seconds", type=float, default=120)
        p.add_argument("--fps", type=int, default=30)
        p.add_argument("--native", type=int, default=1280,
                       help="internal render width (detail level)")
        p.add_argument("--crf", type=int, default=18)
        p.add_argument("--seed", type=int, default=7)
        p.add_argument("--palette", default=None)
        if name == "render":
            p.add_argument("--out", default=None)

    sub.add_parser("on", help="(re)start the saved live wallpaper")
    sub.add_parser("off", help="stop; restore static wallpaper")
    sub.add_parser("status", help="show what's running")

    a = ap.parse_args()

    if a.cmd == "list":
        for name, (_, pal, pals, desc) in engines.SCENES.items():
            extra = f"palettes: {', '.join(pals)}" if pals else "fixed palette"
            print(f"  {name:10s} {desc}\n  {'':10s} default: {pal}; {extra}")

    elif a.cmd in ("render", "set"):
        if a.cmd == "set" and a.scene == "off":
            stop_live()
            return
        _, default_pal, allowed, _ = engines.get(a.scene)
        if a.palette and not allowed:
            print(f"note: {a.scene} has a fixed palette; --palette ignored")
            a.palette = None
        elif a.palette and a.palette not in allowed:
            sys.exit(f"{a.scene} supports palettes: {', '.join(allowed)}")
        W, H = _parse_res(a.res)
        pal = a.palette or default_pal
        default_out = (CACHE / f"{a.scene}_{pal}_s{a.seed}_{W}x{H}"
                               f"_{a.seconds:g}s_{a.fps}f_n{a.native}_c{a.crf}.mp4")
        out = Path(getattr(a, "out", None) or default_out)
        if a.cmd == "render" or not out.exists():
            render(a.scene, out, W, H, a.seconds, a.fps, a.native,
                   a.crf, a.seed, pal)
            print(f"wrote {out}")
        if a.cmd == "set":
            start_live(out)
            print(f"live wallpaper: {a.scene} ({out.name})")

    elif a.cmd == "on":
        vf = STATE / "video"
        if (STATE / "enabled").exists() and vf.exists():
            start_live(vf.read_text().strip())

    elif a.cmd == "off":
        stop_live()
        print("static wallpaper restored")

    elif a.cmd == "status":
        pids = _pids("mpvpaper")
        print(f"mpvpaper: {'running (pid ' + str(pids[0]) + ')' if pids else 'not running'}")
        if (STATE / "enabled").exists():
            print(f"enabled: yes ({(STATE / 'video').read_text().strip()})")
        else:
            print("enabled: no")


if __name__ == "__main__":
    main()
