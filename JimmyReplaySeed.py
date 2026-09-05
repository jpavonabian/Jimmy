#!/usr/bin/env python3
"""JimmyReplaySeed.py - build an isolated settings file for a replay run.

Both replay suites point Jimmy at a throwaway INI (JIMMY_TEST_INI_PATH) built
from the operator's own settings file. The operator's file is only ever READ:
the run inherits working prerequisites (band, UDP, callsign) while every write
lands in the copy.

Why the copy also PINS keys
---------------------------
Left unpinned, a suite's results describe whoever happens to be running it. The
main suite showed eight "pre-existing failures" for exactly that reason -- none
were bugs, they were one operator's Options. Worse, they moved: switching
Optimize throughput off changed maxTxRepeat, which changes maxAutoGenEnqueue,
which changes how many stations fit in a period, which changes which
queue-capacity assertions fail.

Pinning the keys the assertions actually depend on makes a run reproducible and
comparable between builds. It does NOT make a failing assertion pass; it only
stops the answer drifting with the operator's configuration.
"""

import io
import os
import sys

# Section name used by IniFile (defaults to the assembly name).
INI_SECTION = "Jimmy"

# Keys every replay run pins, whatever the suite. These are the ones the shipped
# assertions in JimmyReplay.py depend on, with the value each assertion assumes.
MAIN_SUITE_PINS = {
    # Accept decodes in either T/R period, so period-dependent tests do not turn
    # on which half of the cycle the run happens to start in (T19, T20).
    "advCallLayout": "True",
    # Fixed transmit repeat count. With Optimize on, maxTxRepeat tracks queue
    # depth, and through UpdateMaxAutoGenEnqueue that silently resizes the queue
    # mid-run -- which is what made the queue-capacity assertions wander.
    "optimizeTx": "False",
    "timeout": "3",
    "maxQueuedCalls": "5",
    # Every Call Filter enabled, so a decode is never rejected merely because a
    # category is unticked in the operator's own setup.
    "callingPriorities": ("TO_MYCALL,NEW_COUNTRY_ON_BAND,NEW_COUNTRY,WANTED_CQ,ALWAYS_WANTED,"
                          "DEFAULT,WAS_NEEDED,WAS_UNCONFIRMED,DXCC_UNCONFIRMED,ZONE_NEEDED,"
                          "STILL_NEEDED"),
    # Message-type and origin filters wide open (T07, T10, T24, T25-T28).
    "cqOnly": "False",
    "cqGrid": "False",
    "anyMsg": "True",
    "enableReplyDx": "True",
    "enableReplyLocal": "True",
    "newOnBand": "False",
    # Weak-signal floor on at -22, which is what Group 18 and 19 assume.
    "ignoreWeakSnr": "True",
    "minSnr": "-22",
    "removeOnWeakSnr": "True",
    # Directed CQ alerts the suite expects to be recognised (T08 POTA, T17 SOTA).
    "useAlertDirected": "True",
    # Space separated, not comma: Controller.ReplyDirCqEntries splits on ' ', so
    # "POTA,SOTA" is one token that matches neither and the alerts never fire.
    "alertDirecteds": "POTA SOTA",
    # Auto-frequency off: with it on, Jimmy asks about transmit-slot analysis in
    # a modal dialog instead of switching mode.
    "bestOffset": "False",
    # Simple Autoreply off -- the main suite measures the normal pipeline.
    "autoReplySimpleEnabled": "False",
}


def real_ini_path():
    return os.path.join(os.environ["LOCALAPPDATA"], "Jimmy", "Jimmy.ini")


def seed_ini(dest, overrides, base=None):
    """Copy the operator's INI to dest and apply overrides to the copy.

    Written without a BOM on purpose. PowerShell's Set-Content -Encoding UTF8
    adds one, and Windows' private-profile API then stops recognising the
    leading [Jimmy] section header -- Jimmy starts with every setting at its
    default and the run silently measures nothing it was meant to.
    """
    src = real_ini_path()
    data = io.open(src, "rb").read() if os.path.exists(src) else b""
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]

    merged = dict(base or {})
    merged.update(overrides or {})

    lines = data.decode("utf-8", errors="replace").splitlines()
    remaining = dict(merged)
    out = []
    in_section = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_section and remaining:
                for k, v in remaining.items():
                    out.append("{0}={1}".format(k, v))
                remaining = {}
            in_section = (stripped[1:-1] == INI_SECTION)
            out.append(line)
            continue
        if in_section and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in remaining:
                out.append("{0}={1}".format(key, remaining.pop(key)))
                continue
        out.append(line)

    if not any(l.strip() == "[{0}]".format(INI_SECTION) for l in out):
        out.insert(0, "[{0}]".format(INI_SECTION))
    for k, v in remaining.items():
        out.append("{0}={1}".format(k, v))

    io.open(dest, "wb").write(("\n".join(out) + "\n").encode("utf-8"))
    return dest


if __name__ == "__main__":
    # Invoked by run_replay_tests.bat with the destination path.
    if len(sys.argv) != 2:
        print("usage: JimmyReplaySeed.py <destination .ini>")
        sys.exit(2)
    path = seed_ini(sys.argv[1], MAIN_SUITE_PINS)
    print("Seeded pinned settings: {0}".format(path))
