// Renders the marketplace preview frame offscreen, at full monitor resolution
// and with no windows in the way:
//
//   quickshell -p preview-render.qml     # writes preview.png, then exits
//
// Offscreen rather than a screen capture because the wallpaper is, by
// definition, the thing every window is covering.
import Quickshell
import Quickshell.Io
import QtQuick

ShellRoot {
  id: root

  readonly property int outWidth: 2560
  readonly property int outHeight: 1440
  // The moment the aurora looks like itself. Change it, re-run, look.
  readonly property real atTime: 42.0
  readonly property string outPath: Qt.resolvedUrl("preview.png").toString().replace("file://", "")

  QtObject {
    id: stubShell
    property var shellConfig: ({ plugins: [
      { id: "io.github.marko-builds.duskpaper", palette: "aurora", speed: 0 }] })
  }

  Duskpaper {
    id: plugin
    shell: stubShell
    manifest: ({ id: "io.github.marko-builds.duskpaper" })
  }

  Process {
    id: fit
    command: ["magick", root.outPath, "-resize",
              root.outWidth + "x" + root.outHeight + "!", "-strip", root.outPath]
    onExited: function(code) {
      console.log("preview: " + (code === 0 ? "wrote " : "resize failed for ") + root.outPath)
      Qt.exit(code)
    }
  }

  Timer {
    interval: 400
    repeat: true
    running: true
    property int t: 0
    onTriggered: {
      t++
      if (t === 1) {
        plugin.clock = root.atTime
        return
      }
      if (!plugin.shaderItem) {
        if (t > 12) { console.log("preview: shader never mounted"); Qt.exit(1) }
        return
      }
      running = false
      plugin.shaderItem.grabToImage(function(result) {
        var ok = result.saveToFile(root.outPath)
        if (!ok) { console.log("preview: FAILED to write " + root.outPath); Qt.exit(1) }
        // grabToImage scales the request by the monitor's scale factor, and the
        // reported devicePixelRatio (2) did not match the factor actually
        // applied (1.6), so a request for an exact size is not one. Resize
        // afterwards, where the number is the number.
        fit.running = true
      }, Qt.size(root.outWidth, root.outHeight))
    }
  }
}
