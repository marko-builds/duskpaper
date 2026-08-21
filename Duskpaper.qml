// duskpaper — the live shader wallpaper for Omarchy 4.
//
// Same aurora as the CLI's `duskpaper set aurora`, arrived at from the other
// side: the CLI pre-renders two minutes of video on the CPU and hands it to
// mpvpaper, this runs the fragment shader every frame at the background layer
// inside omarchy-shell. No render wait, no cache, no video file.
//
// Shader ported from Borealis (~/Projects/borealis), which ported it from the
// monolith's play/aurora.py. Read that repo's CLAUDE.md before editing the
// shader: it carries the qsb/GLSL-120 constraints that make a shader compile
// clean and render blank.
import Quickshell
import Quickshell.Hyprland
import Quickshell.Wayland
import QtQuick

Item {
  id: root

  // Injected by the shell's generic service loader (shell.qml ensureService).
  property var shell: null
  property var manifest: null

  readonly property string pluginId: (manifest && manifest.id)
                                     || "io.github.marko-builds.duskpaper"

  // 6-stop ramps + sky tints from play/aurora.py PALETTES (0-255).
  readonly property var paletteTable: ({
    aurora: { p: [0.00, 0.12, 0.30, 0.55, 0.80, 1.00],
      c: [[150,45,180],[90,110,205],[35,200,200],[30,235,130],[25,180,80],[8,70,35]],
      sb: [6,8,18], sa: [8,10,22] },
    ember: { p: [0.00, 0.15, 0.35, 0.60, 0.82, 1.00],
      c: [[200,40,150],[225,60,95],[235,75,60],[240,120,40],[180,70,22],[60,20,12]],
      sb: [10,6,12], sa: [16,8,14] },
    gold: { p: [0.00, 0.15, 0.35, 0.60, 0.82, 1.00],
      c: [[120,70,20],[180,120,35],[225,165,55],[245,205,110],[235,175,90],[60,40,14]],
      sb: [10,8,6], sa: [16,12,8] },
    nord: { p: [0.00, 0.18, 0.40, 0.62, 0.82, 1.00],
      c: [[180,142,173],[129,161,193],[136,192,208],[143,188,187],[163,190,140],[90,110,75]],
      sb: [12,14,18], sa: [20,24,34] },
    ice: { p: [0.00, 0.15, 0.35, 0.60, 0.82, 1.00],
      c: [[60,90,200],[60,150,230],[90,210,235],[160,235,240],[130,185,160],[20,45,75]],
      sb: [6,9,22], sa: [8,14,26] }
  })

  // ── config ────────────────────────────────────────────────────────────────
  // The plugin's own entry in ~/.config/omarchy/shell.json, applied live on
  // save (shellConfig is reactive):
  //   "plugins": [{ "id": "io.github.marko-builds.duskpaper",
  //                 "palette": "ice", "speed": 0.6, "fps": 30 }]
  readonly property var entry: {
    var cfg = root.shell && root.shell.shellConfig
    var plugins = (cfg && cfg.plugins) || []
    for (var i = 0; i < plugins.length; i++)
      if (plugins[i] && plugins[i].id === root.pluginId) return plugins[i]
    return null
  }

  readonly property string palette: (entry && paletteTable[entry.palette] !== undefined)
                                    ? entry.palette : "aurora"
  readonly property var pal: paletteTable[palette]

  // A wallpaper is looked at all day, not summoned for a moment, so it runs
  // slower than Borealis by default. 0 freezes it on a still frame.
  readonly property real speed: {
    var v = entry ? Number(entry.speed) : NaN
    return (isFinite(v) && v >= 0 && v <= 4) ? v : 0.6
  }

  // Frame budget, not a display rate: the shader is cheap but fullscreen, and
  // nothing in an aurora needs 144 Hz. Clamped so a bad config cannot spin.
  readonly property int fps: {
    var v = entry ? Number(entry.fps) : NaN
    return (isFinite(v) && v >= 1 && v <= 60) ? Math.round(v) : 30
  }

  // ── the clock ─────────────────────────────────────────────────────────────
  // ONE clock for every screen. Per-panel timers drift apart within seconds, so
  // a two-monitor desktop would show the same aurora at two different moments.
  property real clock: 0

  // Mirrored off the ShaderEffect so a caller (and selftest.qml) can see
  // whether the shader actually came up. qsb compiling is not evidence.
  property int shaderStatus: -1
  property string shaderLog: ""
  // The live ShaderEffect, exposed so a caller can grab its pixels. status
  // alone is not enough: a const array in the shader passes qsb AND reports
  // Compiled, then fails in the RHI and paints nothing (Borealis, 2026-08-20).
  // Only the pixels distinguish "running" from "blank".
  property Item shaderItem: null

  Timer {
    // A Timer rather than a NumberAnimation: an animation repaints every vsync
    // (60-144 Hz for scenery that reads identically at 30), and a wallpaper
    // pays that bill all day.
    interval: Math.round(1000 / root.fps)
    repeat: true
    running: root.animating
    onTriggered: root.clock += (interval / 1000) * root.speed
  }

  function stopVec(i) {
    return Qt.vector4d(pal.c[i][0] / 255, pal.c[i][1] / 255, pal.c[i][2] / 255, pal.p[i])
  }

  // ── occlusion ─────────────────────────────────────────────────────────────
  // The wallpaper is invisible under a fullscreen window, so stop paying for
  // it. This is the shader twin of mpvpaper's auto-pause, and it is the whole
  // reason a live wallpaper is affordable while you work.
  property bool anyFullscreen: false

  // The single gate on whether this costs anything. Derived rather than
  // implicit in the Timer so it can be asserted synchronously: anyFullscreen
  // is owned by live Hyprland events and can change under a test mid-tick.
  readonly property bool animating: !root.anyFullscreen && root.speed > 0

  function refreshOcclusion() {
    var ws = Hyprland.workspaces ? Hyprland.workspaces.values : []
    var focused = Hyprland.focusedWorkspace
    for (var i = 0; i < ws.length; i++) {
      var w = ws[i]
      if (!w || !focused || w.id !== focused.id) continue
      var ipc = w.lastIpcObject
      root.anyFullscreen = !!(ipc && ipc.hasfullscreen)
      return
    }
    root.anyFullscreen = false
  }

  Connections {
    target: Hyprland
    function onRawEvent(event) {
      // fullscreen flips it; the rest change WHICH workspace is focused, which
      // changes the answer just as much.
      var n = event.name
      if (n === "fullscreen" || n === "workspace" || n === "workspacev2"
          || n === "focusedmon" || n === "closewindow" || n === "openwindow") {
        Hyprland.refreshWorkspaces()
        Qt.callLater(root.refreshOcclusion)
      }
    }
  }

  Component.onCompleted: {
    Hyprland.refreshWorkspaces()
    Qt.callLater(root.refreshOcclusion)
  }

  // ── the surface ───────────────────────────────────────────────────────────
  Variants {
    model: Quickshell.screens

    PanelWindow {
      id: panel
      required property var modelData

      screen: modelData
      anchors { top: true; bottom: true; left: true; right: true }
      // Opaque: this replaces the desktop background rather than tinting it.
      color: "black"
      exclusionMode: ExclusionMode.Ignore
      WlrLayershell.namespace: "duskpaper"
      WlrLayershell.layer: WlrLayer.Background
      WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
      // Never park this with updatesEnabled: false. The background layer has
      // been observed to lose its committed buffer while parked, leaving a
      // black desktop until the shell restarts (omarchy.background carries the
      // same note). Idling is done by stopping the clock below, which stops the
      // repaints without unmapping anything.
      updatesEnabled: true

      ShaderEffect {
        id: scene
        anchors.fill: parent

        property real time: root.clock
        property vector2d resolution: Qt.vector2d(width, height)
        property vector4d stop0: root.stopVec(0)
        property vector4d stop1: root.stopVec(1)
        property vector4d stop2: root.stopVec(2)
        property vector4d stop3: root.stopVec(3)
        property vector4d stop4: root.stopVec(4)
        property vector4d stop5: root.stopVec(5)
        property vector4d skyBase: Qt.vector4d(root.pal.sb[0] / 255, root.pal.sb[1] / 255,
                                               root.pal.sb[2] / 255, 0)
        property vector4d skyAmp: Qt.vector4d(root.pal.sa[0] / 255, root.pal.sa[1] / 255,
                                              root.pal.sa[2] / 255, 0)
        fragmentShader: Qt.resolvedUrl("shaders/aurora.frag.qsb")

        function publishStatus() {
          root.shaderStatus = scene.status
          root.shaderLog = scene.log
        }
        onStatusChanged: publishStatus()
        Component.onCompleted: { root.shaderItem = scene; publishStatus() }
      }
    }
  }
}
