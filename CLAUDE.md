# duskpaper — dev notes

Python CLI that renders seamless procedural video loops (numpy piped into ffmpeg) and
runs them as a Wayland wallpaper via mpvpaper. Engines are vendored from the monolith's
`play/` studio; `play/` stays the look-dev home, this repo is the shipped product.
Ported engines must keep the seamless contract below or they do not belong here.

## The seamless contract (the repo's one invariant)

Every engine's motion is an **integer-frequency function of the loop phase**, so
`frame(0) == frame(loop_period)` exactly. Spatial phase is baked once at construction;
frame time only spins the harmonics. Each engine carries a `__main__` seam self-test
that asserts `max |frame(0) - frame(period)| <= 1` — run it before trusting a new or
edited engine:

```sh
python -m duskpaper.engines.tide
```

A seam is invisible in a 5-second preview and obvious after two minutes on a desktop,
so the assert is the check, not the eyeball.

## Gotchas (each cost a debugging round — don't relearn)

- **A large smooth gradient bands in 8-bit h264, and the banding *moves*.** tide's moon
  glow visibly breathes because the encoder re-quantizes the bands differently each
  frame. No `crf` fixes it; 8-bit is the ceiling. `tide` alone encodes HEVC 10-bit
  (`libx265`, `yuv420p10le`, Main10 hardware-decodes anywhere modern) — measured halo
  flicker dropped ~98%. Any new scene with a big soft gradient needs the same treatment;
  the branch is at `cli.py:110`.
- **GIF previews have the same trap through a different door.** 256 colors with a fresh
  per-frame palette makes a smooth gradient's banding shift frame to frame. Build preview
  GIFs with ONE global palette (`palettegen stats_mode=full`) plus a position-fixed bayer
  dither: a static element then measures ptp 0 instead of ptp 13.
- **The cache key is the full parameter set** (`cli.py:295`) — scene, palette, seed,
  resolution, seconds, fps, native, crf, calm. Adding a knob that changes pixels means
  adding it to that filename, or `set` silently serves a stale render. That is exactly
  what `--calm` had to do.
- **`--native` is the internal render width, not the output resolution.** Render cost
  scales with the scene and `--native`, never with the monitor. Per-scene minutes in
  `RENDER_MINUTES` are measured, not guessed; re-measure when an engine changes.
- **Omarchy 4 has no wallpaper daemon to save or restore.** The background is painted
  inside `omarchy-shell`, so `WALLPAPER_DAEMONS` matches nothing, `restore.json` is
  never written, and `off` is correct only because killing mpvpaper uncovers the shell's
  own background. `_shell_draws_background()` makes that explicit and matches on the
  process **cmdline**, not the name, so a stray `quickshell -p selftest.qml` from a
  plugin repo is not mistaken for the shell.
- **The Omarchy current-theme marker moved** to `~/.local/state/omarchy/current/` in
  Omarchy 4. A hook should read the theme slug from `$1` (which `omarchy-hook theme-set`
  passes) rather than either path, and `_omarchy_background()` tries both.

## Related

Borealis (`~/Projects/borealis`) is the same aurora math as a live GPU fragment shader
inside the Quattro shell. The two answer the same want by opposite routes: this repo
pre-renders on the CPU and plays a video, that one runs the shader every frame.
