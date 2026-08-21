// Standalone selftest for Duskpaper.qml against the LIVE compositor:
//   quickshell -p selftest.qml   (exit 0 = pass, 1 = fail)
//
// It mounts the real plugin with a stub shell whose shellConfig it rewrites
// mid-run, so every config path is exercised the way the shell would drive it.
// Two of the checks are the ones worth having:
//
//   shader-compiled   — qsb compiles a shader that renders BLANK (a const
//                       array is legal to qsb and illegal in the GLSL 120 the
//                       RHI translates to). Compiling is not evidence; the
//                       ShaderEffect's own status is.
//   surface-above-background — the entire plugin rests on a second background
//                       layer surface stacking ABOVE omarchy-background. If
//                       that ever flips, the wallpaper is invisible and every
//                       other check still passes.
import Quickshell
import Quickshell.Io
import QtQuick

ShellRoot {
  id: harness

  property bool failed: false
  property int checks: 0
  property real timeAtFreeze: -1
  property real timeAtSpeedZero: -1

  function log(m) { console.log("[selftest] " + m) }

  function check(name, cond) {
    checks++
    if (cond) log("ok   " + name)
    else { log("FAIL " + name); harness.failed = true }
  }

  // Stub of the object shell.qml injects as `shell`.
  QtObject {
    id: stubShell
    property var shellConfig: ({ plugins: [] })
  }

  Duskpaper {
    id: plugin
    shell: stubShell
    manifest: ({ id: "io.github.marko-builds.duskpaper" })
  }


  // ── the pixel check ───────────────────────────────────────────────────────
  // status === Compiled is NOT evidence the shader runs. Calibrated 2026-08-21
  // by injecting the const array from Borealis's gotcha list: qsb compiled it
  // clean, ShaderEffect reported Compiled, the RHI logged C7516, and the
  // surface painted nothing. Grabbing the pixels is what tells the difference.
  property string grabPath: "/tmp/duskpaper-selftest-grab.png"

  Process {
    id: pixels
    command: ["magick", harness.grabPath, "-format",
              "%[fx:standard_deviation] %[fx:maxima]", "info:"]
    stdout: StdioCollector {
      onStreamFinished: {
        var parts = String(text).trim().split(/\s+/)
        var sd = parseFloat(parts[0])
        var mx = parseFloat(parts[1])
        harness.log("grab: sd=" + sd + " max=" + mx)
        // A blank/failed shader grabs as flat black: sd 0, max 0. A live
        // aurora has real spread and real highlights.
        harness.check("shader-paints-pixels", isFinite(sd) && sd > 0.01 && mx > 0.05)
        layers.running = true
      }
    }
  }

  function grabShader() {
    if (!plugin.shaderItem) {
      harness.check("shader-item-exposed", false)
      layers.running = true
      return
    }
    harness.check("shader-item-exposed", true)
    plugin.shaderItem.grabToImage(function(result) {
      var saved = result.saveToFile(harness.grabPath)
      harness.check("shader-grab-saved", saved === true)
      if (saved) pixels.running = true
      else layers.running = true
    })
  }

  Process {
    id: layers
    command: ["hyprctl", "layers", "-j"]
    stdout: StdioCollector {
      onStreamFinished: {
        var order = []
        try {
          var d = JSON.parse(text)
          for (var mon in d) {
            var lvl0 = d[mon].levels ? d[mon].levels["0"] : null
            if (!lvl0) continue
            for (var i = 0; i < lvl0.length; i++) order.push(lvl0[i].namespace)
          }
        } catch (e) {}
        var mine = order.indexOf("duskpaper")
        var bg = order.indexOf("omarchy-background")
        harness.check("surface-present", mine !== -1)
        // Later in the level-0 array is drawn on top. Measured 2026-08-21 with
        // an opaque probe: 91908 desktop pixels changed colour, so the second
        // surface really is the visible one.
        harness.check("surface-above-background", mine !== -1 && mine > bg)
        harness.finish()
      }
    }
  }

  Timer {
    interval: 250
    repeat: true
    running: true
    property int t: 0
    onTriggered: {
      t++

      if (t === 2) {
        harness.check("shader-effect-mounted", plugin.shaderStatus !== -1)
        harness.check("shader-compiled", plugin.shaderStatus === ShaderEffect.Compiled)
        if (plugin.shaderStatus !== ShaderEffect.Compiled)
          harness.log("shader status " + plugin.shaderStatus + " log: " + plugin.shaderLog)
        harness.check("default-palette-aurora", plugin.palette === "aurora")
        harness.check("default-speed", plugin.speed === 0.6)
        harness.check("default-fps", plugin.fps === 30)
      }

      // clock advances while unoccluded
      if (t === 3) harness.timeAtFreeze = plugin.clock
      if (t === 5) harness.check("clock-runs", plugin.clock > harness.timeAtFreeze)

      // config is read live off shellConfig, exactly as the shell reassigns it
      if (t === 6) {
        stubShell.shellConfig = { plugins: [
          { id: "io.github.marko-builds.duskpaper", palette: "ice", speed: 1.5, fps: 45 }] }
      }
      if (t === 7) {
        harness.check("config-palette-applied", plugin.palette === "ice")
        harness.check("config-speed-applied", plugin.speed === 1.5)
        harness.check("config-fps-applied", plugin.fps === 45)
      }

      // known negatives: junk config must fall back, not propagate
      if (t === 8) {
        stubShell.shellConfig = { plugins: [
          { id: "io.github.marko-builds.duskpaper", palette: "banana", speed: 99, fps: 0 }] }
      }
      if (t === 9) {
        harness.check("bad-palette-falls-back", plugin.palette === "aurora")
        harness.check("out-of-range-speed-falls-back", plugin.speed === 0.6)
        harness.check("out-of-range-fps-falls-back", plugin.fps === 30)
      }

      // an entry for a DIFFERENT plugin must not be read as ours
      if (t === 10) {
        stubShell.shellConfig = { plugins: [
          { id: "io.github.marko-builds.borealis", palette: "ember" }] }
      }
      if (t === 11) harness.check("other-plugin-entry-ignored", plugin.palette === "aurora")

      // Occlusion contract, asserted SYNCHRONOUSLY. anyFullscreen is owned by
      // live Hyprland events: a spawned process firing openwindow mid-test made
      // refreshOcclusion correctly clear a flag the test had just set, and the
      // check went red for an environment reason rather than a defect. Reading
      // the derived gate in the same tick leaves no window for that.
      if (t === 12) {
        var wasFullscreen = plugin.anyFullscreen
        plugin.anyFullscreen = true
        harness.check("occluded-stops-animating", plugin.animating === false)
        plugin.anyFullscreen = false
        harness.check("unoccluded-resumes-animating", plugin.animating === true)
        plugin.anyFullscreen = wasFullscreen
      }
      // That the gate really stops the clock is proven by speed-zero below,
      // which runs through the same Timer binding with no external owner.

      // speed 0 is a documented freeze, not a slow crawl
      if (t === 18) {
        stubShell.shellConfig = { plugins: [
          { id: "io.github.marko-builds.duskpaper", speed: 0 }] }
      }
      if (t === 19) harness.timeAtSpeedZero = plugin.clock
      if (t === 22) {
        harness.check("speed-zero-freezes",
                      plugin.clock === harness.timeAtSpeedZero)
      }

      if (t === 23) harness.grabShader()

      if (t >= 40) {
        harness.log("FAIL: selftest timed out")
        harness.failed = true
        harness.finish()
      }
    }
  }

  function finish() {
    log(harness.checks + " checks")
    log(harness.failed ? "RESULT FAIL" : "RESULT PASS")
    Qt.exit(harness.failed ? 1 : 0)
  }
}
