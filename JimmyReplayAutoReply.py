#!/usr/bin/env python3
"""
JimmyReplayAutoReply.py - replay coverage for Simple Autoreply mode.

Simple Autoreply is configured in Options, not over UDP, so each scenario needs
Jimmy restarted against a different settings file. This driver does that: for
each scenario it seeds an isolated INI (JIMMY_TEST_INI_PATH), launches Jimmy,
runs the scenario's decodes through the existing JimmyReplay machinery, then
shuts that instance down.

The operator's real settings file is only ever READ, as the seed base, so the
scenarios inherit working prerequisites (band, Advanced Call Layout, UDP) while
every write lands in the throwaway copy.

All UDP building, the Win32 verifier and the assertion helpers are imported from
JimmyReplay.py rather than duplicated -- this file only adds scenario setup and
the Simple Autoreply assertions.

BEFORE RUNNING:
  1. Close WSJT-X.
  2. Close Jimmy -- this driver starts and stops its own instances.
  3. Build Jimmy (Debug).

USAGE:
  run_autoreply_replay_tests.bat        (do not call this script directly)
"""

import ctypes
import datetime
import os
import shutil
import socket
import struct
import subprocess
import sys
import time

import JimmyReplay as JR

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
JIMMY_EXE = os.path.join(SCRIPT_DIR, "WSJTX_Controller", "bin", "Debug", "Jimmy.exe")

# Section name used by IniFile (defaults to the assembly name).
INI_SECTION = "Jimmy"

# Overridden in every scenario so the assertions never depend on how the
# operator happens to have the Receive tab configured.
BASE_OVERRIDES = {
    "ignoreWeakSnr": "True",     # normal-mode floor ON ...
    "minSnr": "-20",             # ... at -20, so a -25 decode is master-rejected
    "removeOnWeakSnr": "False",
    "advCallLayout": "True",     # accept decodes in either T/R period
    # Auto-frequency off. With it on, AnalysisNeeded is true and Alt+C puts up a
    # modal "Transmit slot has not been analyzed" dialog instead of switching
    # mode, which silently stalls the Call CQ scenario.
    "bestOffset": "False",
    # Every Call Filter enabled. Only the mode-off scenarios go through this
    # switch at all, but without pinning it their outcome depends on which
    # categories the operator happens to have ticked -- a US station tagged
    # WAS_NEEDED was silently rejected before this was pinned.
    "callingPriorities": ("TO_MYCALL,NEW_COUNTRY_ON_BAND,NEW_COUNTRY,WANTED_CQ,ALWAYS_WANTED,"
                          "DEFAULT,WAS_NEEDED,WAS_UNCONFIRMED,DXCC_UNCONFIRMED,ZONE_NEEDED,"
                          "STILL_NEEDED"),
}

# Simple Autoreply defaults, per scenario overrides applied on top.
AR_DEFAULTS = {
    "autoReplySimpleEnabled": "True",
    "autoReplySimpleMyCallers": "True",
    "autoReplySimpleCqCallers": "True",
    "autoReplySimpleMinSnrEnabled": "False",
    "autoReplySimpleMinSnr": "-24",
    "autoReplySimpleNewOnly": "False",
    "autoReplySimpleNewDxccOnly": "False",
    "autoReplySimpleNewDxccScope": "ANY_BAND",
    "autoReplySimpleExcludeDirCq": "True",
    "autoReplySimpleListMode": "ALLOW",
    "autoReplySimpleList": "",
}


def real_ini_path():
    return os.path.join(os.environ["LOCALAPPDATA"], "Jimmy", "Jimmy.ini")


def seed_ini(scenario, overrides):
    """Copy the operator's INI (read-only) and apply overrides to the copy."""
    dest = os.path.join(os.environ["TEMP"], f"JimmyAutoReplyTest_{scenario}.ini")
    src = real_ini_path()
    if os.path.exists(src):
        shutil.copyfile(src, dest)
    elif os.path.exists(dest):
        os.remove(dest)

    merged = dict(BASE_OVERRIDES)
    merged.update(AR_DEFAULTS)
    merged.update(overrides)

    lines = []
    if os.path.exists(dest):
        with open(dest, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()

    # Rewrite existing keys in place; append whatever is left under the section.
    remaining = dict(merged)
    out = []
    in_section = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_section and remaining:
                for k, v in remaining.items():
                    out.append(f"{k}={v}")
                remaining = {}
            in_section = (stripped[1:-1] == INI_SECTION)
            out.append(line)
            continue
        if in_section and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in remaining:
                out.append(f"{key}={remaining.pop(key)}")
                continue
        out.append(line)

    if not any(l.strip() == f"[{INI_SECTION}]" for l in out):
        out.insert(0, f"[{INI_SECTION}]")
        in_section = True
    if remaining:
        # Append to the end of the section (or the file, if it was the last one).
        for k, v in remaining.items():
            out.append(f"{k}={v}")

    with open(dest, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    return dest


def launch_jimmy(ini_path):
    env = dict(os.environ)
    env["JIMMY_TEST_INI_PATH"] = ini_path
    return subprocess.Popen([JIMMY_EXE], cwd=os.path.dirname(JIMMY_EXE), env=env)


def wait_for_verifier(timeout=25.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = JR.JimmyVerifier()
        if v.available:
            return v
        time.sleep(1.0)
    return JR.JimmyVerifier()


def open_socket(timeout=20.0):
    """LOCAL_PORT lingers briefly after the previous scenario closed it."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("0.0.0.0", JR.LOCAL_PORT))
            return s
        except OSError as e:
            last = e
            time.sleep(1.0)
    raise last


def run_scenario(scenario, description, overrides, body):
    print()
    print("=" * 70)
    print(f"  SCENARIO: {scenario} -- {description}")
    print("=" * 70)

    ini = seed_ini(scenario, overrides)
    print(f"  Settings: {ini}")
    proc = launch_jimmy(ini)
    sock = None
    try:
        v = wait_for_verifier()
        if not v.available:
            print("  x Jimmy window not found -- scenario skipped, assertions not run.")
            return False
        if not JR.ensure_jimmy_udp_ready():
            print("  x Jimmy's UDP port not available -- scenario skipped.")
            return False
        sock = open_socket()
        if not JR.handshake(sock, v):
            print("  x Handshake failed -- scenario skipped.")
            return False
        body(sock, v)
        return True
    finally:
        if sock:
            sock.close()
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        time.sleep(1.5)


# ═══════════════════════════════════════════════════════════════════════════════
# Driving the transmit-decision path
# ═══════════════════════════════════════════════════════════════════════════════
#
# Everything else in this suite (and in JimmyReplay.py) asserts on ADMISSION --
# what AddSelectedCall lets into the queue. Jimmy's decision about who to
# actually call lives in ProcessDecodes, which is driven by processDecodeTimer,
# which only ever starts when a StatusMessage reports decoding going true. Send
# decodes alone and ProcessDecodes never runs at all, so no assertion here could
# ever see a call being selected. These helpers close that gap.

def parse_command(data):
    """(cmd_idx, gen_msg) from an EnableTxMessage, else (None, None).

    JimmyReplay._parse_enable_tx skips the generated message because the
    handshake only needs the command index; the assertions below need to know
    *what* Jimmy asked WSJT-X to transmit, so this reads that field too.
    """
    if not data.startswith(JR.MAGIC) or len(data) < 12:
        return None, None
    if struct.unpack_from(">I", data, 8)[0] != JR.MSG_ENABLE_TX:
        return None, None
    pos = 12

    def rd_u32():
        nonlocal pos
        v = struct.unpack_from(">I", data, pos)[0]
        pos += 4
        return v

    def rd_str():
        nonlocal pos
        n = rd_u32()
        if n == 0xFFFFFFFF:
            return ""
        s = data[pos:pos + n].decode("utf-8", errors="replace")
        pos += n
        return s

    rd_str()                    # Id
    cmd_idx = rd_u32()          # NewTxMsgIdx
    return cmd_idx, rd_str()    # GenMsg


def collect_commands(sock, seconds):
    """Drain the socket for a while, returning the (cmd, genMsg) pairs seen."""
    end = time.time() + seconds
    seen = []
    while time.time() < end:
        sock.settimeout(max(0.1, end - time.time()))
        try:
            data, _ = sock.recvfrom(4096)
        except Exception:
            continue
        cmd, gen = parse_command(data)
        if cmd is not None:
            seen.append((cmd, gen))
    return seen


def check_cq_issued(v, commands, label, expect=True):
    """cmd:6 carrying a 'CQ ...' generated message is Jimmy setting WSJT-X to call CQ."""
    ok = any(cmd == 6 and gen.startswith("CQ ") for cmd, gen in commands)
    if not expect:
        ok = not ok
    v._report(ok, label, f"commands={commands}")


def now_since_midnight_ms(period_ms=15000):
    """Current UTC time-of-day in ms, snapped to the start of the T/R period.

    TrimCallQueue expires a queue entry once (now - (RxDate + SinceMidnight))
    exceeds trPeriod * maxCallQueueAgePeriods. The rest of this suite sends
    SinceMidnight=0, which dates every decode to midnight UTC -- harmless while
    nothing ran TrimCallQueue, but once a decode cycle is driven the queue is
    swept clean before the transmit decision is ever reached. Scenarios that
    drive a cycle must therefore timestamp their decodes realistically.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    ms = ((now.hour * 3600 + now.minute * 60 + now.second) * 1000) + now.microsecond // 1000
    return (ms // period_ms) * period_ms


def drive_decode_cycle(sock, settle=20.0):
    """Run one WSJT-X decode cycle so Jimmy reaches ProcessDecodes.

    processDecodeTimer is set to fire at the end of the current T/R period, so
    the wait has to cover a full FT8 period (15 s) plus slack.
    """
    print("        (driving a decode cycle so ProcessDecodes runs...)")
    sock.sendto(JR.build_status(decoding=True), (JR.JIMMY_HOST, JR.JIMMY_PORT))
    time.sleep(1.0)
    sock.sendto(JR.build_status(decoding=False), (JR.JIMMY_HOST, JR.JIMMY_PORT))
    time.sleep(settle)


# ── Win32 keystroke injection ────────────────────────────────────────────────
# txMode is deliberately not persisted in the INI ("always defaults to LISTEN"),
# so seeding a settings file cannot put Jimmy in Call CQ mode. The only way in is
# the Alt+C hotkey, which is a ProcessCmdKey override and needs real keyboard
# input to the focused window.
_WM_SYSKEYDOWN = 0x0104
_WM_SYSKEYUP = 0x0105
_VK_MENU = 0x12
_KEYEVENTF_KEYUP = 0x0002
_SCAN_CODES = {"C": 0x2E}


def force_foreground(hwnd):
    """Bring Jimmy's window to the foreground.

    A bare SetForegroundWindow from a background process is refused by Windows'
    foreground lock. Attaching our input queue to the current foreground thread
    first lifts that -- the same AttachThreadInput dance Jimmy itself uses in
    Controller.cs.
    """
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    if user32.GetForegroundWindow() == hwnd:
        return True

    # Whether a plain SetForegroundWindow is granted depends on which process
    # currently owns the foreground, so this tries progressively harder rather
    # than assuming one trick works everywhere.
    def attempt(fn):
        fg = user32.GetForegroundWindow()
        cur_tid = kernel32.GetCurrentThreadId()
        fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
        attached = False
        if fg_tid and fg_tid != cur_tid:
            attached = bool(user32.AttachThreadInput(cur_tid, fg_tid, True))
        try:
            fn()
        finally:
            if attached:
                user32.AttachThreadInput(cur_tid, fg_tid, False)
        time.sleep(0.6)
        return user32.GetForegroundWindow() == hwnd

    # Drop the foreground lock timeout first (SPI_SETFOREGROUNDLOCKTIMEOUT=0x2001,
    # SPIF_SENDCHANGE=2); harmless if it fails.
    try:
        user32.SystemParametersInfoW(0x2001, 0, ctypes.c_void_p(0), 2)
    except Exception:
        pass

    if attempt(lambda: (user32.ShowWindow(hwnd, 9), user32.SetForegroundWindow(hwnd))):
        return True
    if attempt(lambda: user32.SwitchToThisWindow(hwnd, True)):
        return True
    # Last resort: a minimize/restore round trip usually grants foreground.
    if attempt(lambda: (user32.ShowWindow(hwnd, 6), time.sleep(0.2),
                        user32.ShowWindow(hwnd, 9), user32.SetForegroundWindow(hwnd))):
        return True
    return False


def send_alt_key(hwnd, char):
    """Synthesise a real Alt+<char>.

    Posting WM_SYSKEYDOWN is not enough: WinForms builds ProcessCmdKey's keyData
    as the message's virtual key OR'd with ModifierKeys, and ModifierKeys reads
    the live keyboard state via GetKeyState. A posted message leaves Alt unset,
    so the hotkey arrives as a bare 'C' and matches nothing.
    """
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    if not force_foreground(hwnd):
        return False
    scan = _SCAN_CODES[char]
    user32.keybd_event(_VK_MENU, 0x38, 0, 0)
    time.sleep(0.05)
    user32.keybd_event(ord(char), scan, 0, 0)
    time.sleep(0.05)
    user32.keybd_event(ord(char), scan, _KEYEVENTF_KEYUP, 0)
    time.sleep(0.05)
    user32.keybd_event(_VK_MENU, 0x38, _KEYEVENTF_KEYUP, 0)
    time.sleep(1.0)
    return True


def switch_to_call_cq(v, attempts=3):
    """Alt+C. Returns True if Jimmy's status confirms Call CQ mode.

    Retried: whether the foreground grab is granted depends on what else is
    fighting for focus at that instant, and a silently missed switch would leave
    a Call CQ scenario asserting nothing.
    """
    for attempt in range(attempts):
        if not send_alt_key(v._jhwnd, "C"):
            print(f"        (foreground grab failed, attempt {attempt + 1}/{attempts})")
            time.sleep(1.0)
            continue
        for _ in range(10):
            if "cq mode" in v.status_text().lower():
                return True
            time.sleep(0.5)
        print(f"        (Alt+C did not take, attempt {attempt + 1}/{attempts})")
    return False


def require_call_cq(v, label):
    """Switch to Call CQ, or FAIL the scenario.

    Reporting a failure rather than returning quietly matters: a scenario that
    skips its assertions must not leave the suite showing all-green.
    """
    if switch_to_call_cq(v):
        return True
    v._report(False, f"{label}: could not switch Jimmy to Call CQ mode (Alt+C)")
    return False


def check_status_contains_nospace(v, fragment, label):
    """Callsigns are spelled out letter-by-letter in the status text for screen
    readers ('W 7 P I C K'), so compare with whitespace removed."""
    v.wait_for_status(fragment, timeout=4.0)
    t = v.status_text()
    ok = fragment.lower().replace(" ", "") in t.lower().replace(" ", "")
    v._report(ok, label, f"status='{t}'")


# ═══════════════════════════════════════════════════════════════════════════════
# Scenarios
# ═══════════════════════════════════════════════════════════════════════════════

def scenario_open(sock, v):
    """Mode on, no filters: the queue must admit what the normal pipeline drops."""
    JR.send(sock,
            "Ordinary CQ from a brand-new station: CQ W7OPEN EM63",
            "Baseline -- Simple Autoreply admits an ordinary CQ",
            JR.build_enqueue("CQ W7OPEN EM63"),
            verify_fn=lambda: v.check_queue_contains(
                "W7OPEN", "AR01: W7OPEN queued (mode on, no filters)"))

    JR.send(sock,
            "CQ from a station already worked on this band: CQ W7WORKD EM63",
            "KEY CASE -- master rejects this (ShouldRejectAlreadyWorked); "
            "with the mode on and 'only new stations' off it must be queued",
            JR.build_enqueue("CQ W7WORKD EM63", is_new_call=False),
            verify_fn=lambda: v.check_queue_contains(
                "W7WORKD", "AR02: already-worked station queued (repeat QSOs allowed)"))

    JR.send(sock,
            "Very weak CQ, below the normal-mode floor: CQ W7WEAK EM63 (-25)",
            "Normal floor is on at -20 in this scenario; Simple Autoreply owns "
            "the decision and has no floor set, so it must still be queued",
            JR.build_enqueue("CQ W7WEAK EM63", snr=-25),
            verify_fn=lambda: v.check_queue_contains(
                "W7WEAK", "AR03: weak station queued (mode floor off overrides Receive tab)"))

    JR.send(sock,
            "Directed CQ not meant for us: CQ JA W7DIR EM63",
            "'Skip directed CQs not meant for me' defaults on, so this must NOT queue",
            JR.build_enqueue("CQ JA W7DIR EM63"),
            verify_fn=lambda: v.check_queue_not_contains(
                "W7DIR", "AR04: unmatched directed CQ not queued"))

    # Third-party traffic. Two bugs met here on 2026-09-05, in opposite directions:
    #
    #  * isAcceptableCq is gated on isCq, so it reads false for any decode that is not
    #    a CQ. Passed straight into the directed-CQ filter, that rejected every report,
    #    sign-off and reply between other stations as "directed CQ not for us" -- 111
    #    rejections in one on-air session, including the 73/RR73 that mark a station as
    #    newly free.
    #  * Relaxing it admitted ALL of that traffic instead, so the queue filled with
    #    stations demonstrably mid-QSO with somebody else ("BG8HNC LY3BFH -16"), and
    #    Jimmy called them. None could answer.
    #
    # The rule that settles both: a station is callable when it is calling CQ, or when
    # it has just signed off. Anything else between two other stations means busy.
    JR.send(sock,
            "Sign-off between two other stations: R9WGG W7RR73 RR73",
            "The station just finished, so it is free -- must be queued",
            JR.build_enqueue("R9WGG W7RR73 RR73"),
            verify_fn=lambda: v.check_queue_contains(
                "W7RR73", "AR17: third-party RR73 queued (station now free)"))

    JR.send(sock,
            "Sign-off between two other stations: R9WGG W773 73",
            "Same for a plain 73",
            JR.build_enqueue("R9WGG W773 73"),
            verify_fn=lambda: v.check_queue_contains(
                "W773", "AR18: third-party 73 queued (station now free)"))

    JR.send(sock,
            "Report between two other stations: 3B8GL W7THIRD R-17",
            "KEY CASE -- this decode proves the station is working someone else, "
            "so it must NOT be queued no matter how open the filters are",
            JR.build_enqueue("3B8GL W7THIRD R-17"),
            verify_fn=lambda: v.check_queue_not_contains(
                "W7THIRD", "AR19: third-party report NOT queued (station busy)"))

    JR.send(sock,
            "Grid reply between two other stations: KF0YWI W7GRID LP04",
            "Also mid-QSO, must NOT be queued",
            JR.build_enqueue("KF0YWI W7GRID LP04"),
            verify_fn=lambda: v.check_queue_not_contains(
                "W7GRID", "AR35: third-party grid reply NOT queued (station busy)"))

    JR.send(sock,
            "Bare RRR between two other stations: KF0YWI W7RRR RRR",
            "RRR is 'all received', not a sign-off -- the station may still be working "
            "the other party, so it must NOT be queued",
            JR.build_enqueue("KF0YWI W7RRR RRR"),
            verify_fn=lambda: v.check_queue_not_contains(
                "W7RRR", "AR36: third-party RRR NOT queued (not a sign-off)"))


def scenario_mode_off(sock, v):
    """Mode off: the gates the mode replaces must all be back in force."""
    JR.send(sock,
            "CQ from a station already worked on this band: CQ W8WORKD EM63",
            "REGRESSION -- with the mode off, ShouldRejectAlreadyWorked must "
            "reject this exactly as it does on master",
            JR.build_enqueue("CQ W8WORKD EM63", is_new_call=False),
            verify_fn=lambda: v.check_queue_not_contains(
                "W8WORKD", "AR05: mode off -- already-worked station still rejected"))

    JR.send(sock,
            "Very weak CQ, below the Receive-tab floor: CQ W8WEAK EM63 (-25)",
            "REGRESSION -- with the mode off the Receive-tab floor (-20) applies again",
            JR.build_enqueue("CQ W8WEAK EM63", snr=-25),
            verify_fn=lambda: v.check_queue_not_contains(
                "W8WEAK", "AR06: mode off -- weak-signal floor still applies"))


def scenario_cq_callers_off(sock, v):
    """Mode on, 'reply to CQ callers' off: work only stations that called us."""
    JR.send(sock,
            "CQ from another station: CQ W7CQOFF EM63",
            "'Reply to stations calling CQ' is off, so this must NOT queue",
            JR.build_enqueue("CQ W7CQOFF EM63"),
            verify_fn=lambda: v.check_queue_not_contains(
                "W7CQOFF", "AR07: CQ caller not queued when that option is off"))

    JR.send(sock,
            f"Station answering our CQ: {JR.MY_CALL} W7MINE EM63",
            "'Reply to stations answering my CQ' is on, so this MUST queue",
            JR.build_enqueue(f"{JR.MY_CALL} W7MINE EM63"),
            verify_fn=lambda: v.check_queue_contains(
                "W7MINE", "AR08: station answering our CQ still queued"))


def scenario_min_snr(sock, v):
    """Mode on with its own weak-signal floor at -15."""
    JR.send(sock,
            "CQ comfortably above the mode's floor: CQ W7LOUD EM63 (-10)",
            "Mode floor is -15, so -10 must queue",
            JR.build_enqueue("CQ W7LOUD EM63", snr=-10),
            verify_fn=lambda: v.check_queue_contains(
                "W7LOUD", "AR09: signal above the mode's floor queued"))

    JR.send(sock,
            "CQ below the mode's floor: CQ W7QUIET EM63 (-20)",
            "Mode floor is -15, so -20 must NOT queue",
            JR.build_enqueue("CQ W7QUIET EM63", snr=-20),
            verify_fn=lambda: v.check_queue_not_contains(
                "W7QUIET", "AR10: signal below the mode's floor rejected"))


def scenario_new_only(sock, v):
    """Mode on with 'only stations not yet worked on this band'."""
    JR.send(sock,
            "CQ from a new station: CQ W7NEW EM63",
            "Never worked on this band, so it must queue",
            JR.build_enqueue("CQ W7NEW EM63", is_new_call=True),
            verify_fn=lambda: v.check_queue_contains(
                "W7NEW", "AR11: new station queued with 'only new' on"))

    JR.send(sock,
            "CQ from a station already worked: CQ W7AGAIN EM63",
            "'Only stations not yet worked on this band' is on, so this must NOT queue",
            JR.build_enqueue("CQ W7AGAIN EM63", is_new_call=False),
            verify_fn=lambda: v.check_queue_not_contains(
                "W7AGAIN", "AR12: already-worked station rejected with 'only new' on"))


def scenario_allow_list(sock, v):
    """Mode on, station list in ALLOW mode holding one continent code."""
    JR.send(sock,
            "CQ from the allowed continent: CQ EA7ALLOW IN80 (EU)",
            "Allow list is 'EU', so an EU station must queue",
            JR.build_enqueue("CQ EA7ALLOW IN80", country="Spain", continent="EU"),
            verify_fn=lambda: v.check_queue_contains(
                "EA7ALLOW", "AR13: allow-listed continent queued"))

    JR.send(sock,
            "CQ from outside the allow list: CQ W7DENY EM63 (NA)",
            "Allow list is 'EU' only, so an NA station must NOT queue",
            JR.build_enqueue("CQ W7DENY EM63", country="USA", continent="NA"),
            verify_fn=lambda: v.check_queue_not_contains(
                "W7DENY", "AR14: station outside the allow list rejected"))


def scenario_exclude_list(sock, v):
    """Mode on, station list in EXCLUDE mode holding one callsign prefix."""
    JR.send(sock,
            "CQ matching the exclude prefix: CQ EA9SKIP IN80",
            "Exclude list is 'EA9', matched as a callsign prefix, so this must NOT queue",
            JR.build_enqueue("CQ EA9SKIP IN80", country="Spain", continent="EU"),
            verify_fn=lambda: v.check_queue_not_contains(
                "EA9SKIP", "AR15: excluded callsign prefix rejected"))

    JR.send(sock,
            "CQ not matching the exclude prefix: CQ EA1KEEP IN80",
            "Same continent and country, different prefix, so this must queue",
            JR.build_enqueue("CQ EA1KEEP IN80", country="Spain", continent="EU"),
            verify_fn=lambda: v.check_queue_contains(
                "EA1KEEP", "AR16: non-excluded station queued"))


def scenario_auto_select_listen(sock, v):
    """Mode on + 'reply to CQ callers': Jimmy must work the queue unattended.

    Admission alone changes nothing -- Listen mode otherwise waits for the
    operator's Alt+N/Enter and Call CQ keeps calling CQ, so before this the queue
    just filled up and nothing was ever called.
    """
    JR.send(sock,
            "CQ from a station: CQ W7PICK EM63",
            "Must be admitted to the queue first",
            JR.build_enqueue("CQ W7PICK EM63", since_midnight_ms=now_since_midnight_ms()),
            verify_fn=lambda: v.check_queue_contains(
                "W7PICK", "AR20: station admitted to the queue"))

    drive_decode_cycle(sock)

    # ReplyTo() pulls the selected call out of the queue and makes it callInProg,
    # so a successful auto-selection shows up as the station leaving the list and
    # appearing in the status text.
    v.check_queue_not_contains(
        "W7PICK", "AR21: auto-selected station taken out of the queue (Listen mode)")
    check_status_contains_nospace(
        v, "W7PICK", "AR22: status announces the auto-selected station (Listen mode)")


def scenario_auto_select_call_cq(sock, v):
    """Same, in Call CQ mode -- where Jimmy would otherwise just re-call CQ."""
    if not require_call_cq(v, "AR23"):
        return

    JR.send(sock,
            "CQ from a station: CQ W7CQPICK EM63",
            "Must be admitted to the queue first",
            JR.build_enqueue("CQ W7CQPICK EM63", since_midnight_ms=now_since_midnight_ms()),
            verify_fn=lambda: v.check_queue_contains(
                "W7CQPICK", "AR23: station admitted to the queue (Call CQ mode)"))

    drive_decode_cycle(sock)

    v.check_queue_not_contains(
        "W7CQPICK", "AR24: auto-selected station taken out of the queue (Call CQ mode)")
    check_status_contains_nospace(
        v, "W7CQPICK", "AR25: status announces the auto-selected station (Call CQ mode)")


def scenario_auto_select_off(sock, v):
    """Mode off: the auto-selection must not fire at all.

    Negative control for the two above -- proves the new ProcessDecodes branch is
    gated on Simple Autoreply and does not change unattended behaviour on master.
    """
    JR.send(sock,
            "Ordinary CQ, mode off: CQ W8STAY EM63",
            "Admitted by the normal filters (plain CQ, cqOnly)",
            JR.build_enqueue("CQ W8STAY EM63", since_midnight_ms=now_since_midnight_ms()),
            verify_fn=lambda: v.check_queue_contains(
                "W8STAY", "AR26: station admitted by the normal pipeline"))

    drive_decode_cycle(sock)

    v.check_queue_contains(
        "W8STAY", "AR27: mode off -- station still waiting in the queue, nothing auto-called")


def scenario_resume_cq(sock, v):
    """Call CQ mode: once the queue empties again, Jimmy must go back to calling CQ.

    The auto-selection branch pre-empts the CQ-setup path for as long as there is
    someone to work, so this pins the other half: nothing left to work means CQ
    resumes rather than Jimmy sitting silent.
    """
    if not require_call_cq(v, "AR28"):
        return

    JR.send(sock,
            "CQ from a station: CQ W7ONE EM63",
            "Gets auto-selected, so Jimmy stops calling CQ while working it",
            JR.build_enqueue("CQ W7ONE EM63", since_midnight_ms=now_since_midnight_ms()),
            verify_fn=lambda: v.check_queue_contains(
                "W7ONE", "AR28: station admitted to the queue (Call CQ mode)"))

    drive_decode_cycle(sock)
    v.check_queue_not_contains(
        "W7ONE", "AR29: station auto-selected, queue now empty")

    # The station signs off; there is nothing left to work.
    sock.sendto(JR.build_enqueue(f"{JR.MY_CALL} W7ONE 73",
                                 since_midnight_ms=now_since_midnight_ms()),
                (JR.JIMMY_HOST, JR.JIMMY_PORT))
    time.sleep(2.0)
    drive_decode_cycle(sock, settle=17.0)
    cmds = collect_commands(sock, 4.0)
    check_cq_issued(v, cmds, "AR30: CQ resumed once the queue emptied")


def scenario_filtered_caller_cq(sock, v):
    """Call CQ mode: a filtered station calling US must not get answered.

    SetupCq leaves WSJT-X on "CQ, auto, call 1st", so removing the caller from
    Jimmy's own queue is not enough -- WSJT-X's auto-sequence would reply to it
    anyway. Jimmy has to re-issue the CQ. Before this fix a probe against a live
    Jimmy showed it sending no command whatsoever after rejecting a caller.
    """
    if not require_call_cq(v, "AR31"):
        return

    collect_commands(sock, 2.0)     # discard the initial CQ setup

    sock.sendto(JR.build_enqueue(f"{JR.MY_CALL} W7NOTEU EM63",
                                 country="USA", continent="NA",
                                 since_midnight_ms=now_since_midnight_ms()),
                (JR.JIMMY_HOST, JR.JIMMY_PORT))
    print("  [--] Filtered station calls us: KB0UZT W7NOTEU EM63 (NA, allow list is EU)")
    time.sleep(2.0)

    v.check_queue_not_contains(
        "W7NOTEU", "AR31: filtered caller kept out of the queue")
    cmds = collect_commands(sock, 4.0)
    check_cq_issued(
        v, cmds, "AR32: CQ re-issued so WSJT-X does not auto-answer the filtered caller")


def scenario_accepted_caller_cq(sock, v):
    """Control for AR32: a caller that PASSES the filter must not be CQ'd over."""
    if not require_call_cq(v, "AR33"):
        return

    collect_commands(sock, 2.0)     # discard the initial CQ setup

    sock.sendto(JR.build_enqueue(f"{JR.MY_CALL} EA7OK IN80",
                                 country="Spain", continent="EU",
                                 since_midnight_ms=now_since_midnight_ms()),
                (JR.JIMMY_HOST, JR.JIMMY_PORT))
    print("  [--] Accepted station calls us: KB0UZT EA7OK IN80 (EU, matches the allow list)")
    time.sleep(2.0)

    v.check_queue_contains(
        "EA7OK", "AR33: accepted caller queued")
    cmds = collect_commands(sock, 4.0)
    check_cq_issued(
        v, cmds, "AR34: no CQ issued over an accepted caller", expect=False)


def scenario_new_dxcc_only(sock, v):
    """Mode on with "Only DXCC entities I haven't worked".

    IsNewCountry / IsNewCountryOnBand come from WSJT-X in the decode itself, so the
    harness sets them directly rather than trying to reproduce any log state.
    """
    JR.send(sock,
            "CQ from a never-worked entity: CQ 3B8DX LG89",
            "Entity missing from the log, so it must be queued",
            JR.build_enqueue("CQ 3B8DX LG89", country="Mauritius", continent="AF",
                             is_new_country=True, is_new_country_on_band=True),
            verify_fn=lambda: v.check_queue_contains(
                "3B8DX", "AR37: never-worked DXCC entity queued"))

    JR.send(sock,
            "CQ from an entity already in the log: CQ EA9DUP IM76",
            "Any-band scope, so an entity already worked must NOT be queued",
            JR.build_enqueue("CQ EA9DUP IM76", country="Ceuta", continent="AF",
                             is_new_country=False, is_new_country_on_band=False),
            verify_fn=lambda: v.check_queue_not_contains(
                "EA9DUP", "AR38: already-worked DXCC entity NOT queued"))

    JR.send(sock,
            "CQ from an entity worked elsewhere but new on this band: CQ VK9SLOT QF22",
            "Any-band scope is the strict one, so this must NOT be queued either",
            JR.build_enqueue("CQ VK9SLOT QF22", country="Australia", continent="OC",
                             is_new_country=False, is_new_country_on_band=True),
            verify_fn=lambda: v.check_queue_not_contains(
                "VK9SLOT", "AR39: entity worked on another band NOT queued (any-band scope)"))

    # The filter governs who Jimmy goes out and calls, never who it answers.
    JR.send(sock,
            f"Already-worked entity calls US: {JR.MY_CALL} EA9CALLS IM76",
            "A station answering our CQ is worked whatever its entity",
            JR.build_enqueue(f"{JR.MY_CALL} EA9CALLS IM76", country="Ceuta", continent="AF",
                             is_new_country=False, is_new_country_on_band=False),
            verify_fn=lambda: v.check_queue_contains(
                "EA9CALLS", "AR40: station answering our CQ queued despite a worked entity"))


def scenario_new_dxcc_this_band(sock, v):
    """Same filter, scoped to the band in use -- the looser of the two."""
    JR.send(sock,
            "CQ from an entity worked elsewhere but new on this band: CQ VK9SLOT QF22",
            "This-band scope, so a fresh band slot counts and it must be queued",
            JR.build_enqueue("CQ VK9SLOT QF22", country="Australia", continent="OC",
                             is_new_country=False, is_new_country_on_band=True),
            verify_fn=lambda: v.check_queue_contains(
                "VK9SLOT", "AR41: entity new on this band queued (this-band scope)"))

    JR.send(sock,
            "CQ from an entity already worked on this band: CQ EA9DUP IM76",
            "Nothing left to gain here, so it must NOT be queued",
            JR.build_enqueue("CQ EA9DUP IM76", country="Ceuta", continent="AF",
                             is_new_country=False, is_new_country_on_band=False),
            verify_fn=lambda: v.check_queue_not_contains(
                "EA9DUP", "AR42: entity already worked on this band NOT queued"))


def scenario_new_dxcc_unconfirmed(sock, v):
    """Mode on, DXCC scope = "Not confirmed yet".

    Coverage limit, stated plainly: the worked-but-unconfirmed half of this scope is
    read from Jimmy's own logbook (LogbookDb.LoadHrcCache -> worked minus LoTW/QRZ
    confirmed) and resolved through a callsign lookup. The replay suite runs against
    an empty throwaway database with network lookups blocked, so nothing can ever BE
    unconfirmed here -- hrcUnconfirmedDxcc is empty and IsHrcDxccUnconfirmed short
    circuits to false. That half is covered by AutoReplyFilterNewDxccTests instead,
    which feeds the flag in directly.

    What this scenario does pin down is the rest of the scope end to end: a
    never-worked entity is admitted, and an entity that is neither new nor
    unconfirmed is rejected with the scope's own reason.
    """
    JR.send(sock,
            "CQ from a never-worked entity: CQ 3B8NEW LG89",
            "Never worked, so it still needs confirming -- must be queued",
            JR.build_enqueue("CQ 3B8NEW LG89", country="Mauritius", continent="AF",
                             is_new_country=True, is_new_country_on_band=True),
            verify_fn=lambda: v.check_queue_contains(
                "3B8NEW", "AR43: never-worked entity queued (not-confirmed scope)"))

    JR.send(sock,
            "CQ from an entity with nothing left to gain: CQ EA9DONE IM76",
            "Not new, and not in the unconfirmed set, so it must NOT be queued",
            JR.build_enqueue("CQ EA9DONE IM76", country="Ceuta", continent="AF",
                             is_new_country=False, is_new_country_on_band=False),
            verify_fn=lambda: v.check_queue_not_contains(
                "EA9DONE", "AR44: fully-confirmed entity NOT queued (not-confirmed scope)"))


SCENARIOS = [
    ("open", "mode on, no filters -- replies to everyone",
     {}, scenario_open),
    ("mode-off", "mode off -- master gates back in force",
     {"autoReplySimpleEnabled": "False"}, scenario_mode_off),
    ("cq-callers-off", "mode on, only work stations that called us",
     {"autoReplySimpleCqCallers": "False"}, scenario_cq_callers_off),
    ("min-snr", "mode on, own weak-signal floor at -15",
     {"autoReplySimpleMinSnrEnabled": "True", "autoReplySimpleMinSnr": "-15"},
     scenario_min_snr),
    ("new-only", "mode on, only stations not yet worked on this band",
     {"autoReplySimpleNewOnly": "True"}, scenario_new_only),
    ("allow-list", "mode on, station list = allow EU",
     {"autoReplySimpleListMode": "ALLOW", "autoReplySimpleList": "EU"},
     scenario_allow_list),
    ("exclude-list", "mode on, station list = exclude prefix EA9",
     {"autoReplySimpleListMode": "EXCLUDE", "autoReplySimpleList": "EA9"},
     scenario_exclude_list),
    ("auto-select-listen", "mode on -- Jimmy works the queue unattended (Listen mode)",
     {}, scenario_auto_select_listen),
    ("auto-select-call-cq", "mode on -- Jimmy works the queue unattended (Call CQ mode)",
     {}, scenario_auto_select_call_cq),
    ("auto-select-off", "mode off -- nothing is auto-called",
     {"autoReplySimpleEnabled": "False"}, scenario_auto_select_off),
    ("resume-cq", "mode on -- CQ resumes once the queue empties (Call CQ mode)",
     {}, scenario_resume_cq),
    ("filtered-caller-cq", "mode on -- filtered caller must not get answered (Call CQ mode)",
     {"autoReplySimpleListMode": "ALLOW", "autoReplySimpleList": "EU"},
     scenario_filtered_caller_cq),
    ("accepted-caller-cq", "mode on -- accepted caller must not be CQ'd over (Call CQ mode)",
     {"autoReplySimpleListMode": "ALLOW", "autoReplySimpleList": "EU"},
     scenario_accepted_caller_cq),
    ("new-dxcc-any-band", "mode on -- only DXCC entities never worked",
     {"autoReplySimpleNewDxccOnly": "True", "autoReplySimpleNewDxccScope": "ANY_BAND"},
     scenario_new_dxcc_only),
    ("new-dxcc-this-band", "mode on -- only DXCC entities not yet worked on this band",
     {"autoReplySimpleNewDxccOnly": "True", "autoReplySimpleNewDxccScope": "CURRENT_BAND"},
     scenario_new_dxcc_this_band),
    ("new-dxcc-unconfirmed", "mode on -- only DXCC entities still lacking a confirmation",
     {"autoReplySimpleNewDxccOnly": "True",
      "autoReplySimpleNewDxccScope": "NEW_OR_UNCONFIRMED"},
     scenario_new_dxcc_unconfirmed),
]


def main():
    # Same production-safety refusal as JimmyReplay.main(): this driver sends
    # simulated decodes that Jimmy will log for real unless it is isolated.
    if not os.environ.get("JIMMY_TEST_DB_PATH"):
        print("ERROR: JIMMY_TEST_DB_PATH is not set in this shell.")
        print("Refusing to run -- launch via run_autoreply_replay_tests.bat.")
        sys.exit(1)

    if not os.path.exists(JIMMY_EXE):
        print(f"ERROR: {JIMMY_EXE} not found. Build Jimmy first (build.bat).")
        sys.exit(1)

    print("=" * 70)
    print("  JimmyReplayAutoReply.py -- Simple Autoreply replay coverage")
    print("=" * 70)
    print(f"  Jimmy:    {JIMMY_EXE}")
    print(f"  Test DB:  {os.environ['JIMMY_TEST_DB_PATH']}")
    print(f"  Seed INI: {real_ini_path()} (read only, never written)")
    print(f"  Scenarios: {len(SCENARIOS)}")

    total_passed = 0
    total_failed = 0
    skipped = []

    for name, desc, overrides, body in SCENARIOS:
        # Each scenario gets a fresh verifier/counter pair via a fresh Jimmy.
        before = (JR._test_num,)
        holder = {}

        def wrapped(sock, v, _body=body, _holder=holder):
            _body(sock, v)
            _holder["v"] = v

        ok = run_scenario(name, desc, overrides, wrapped)
        v = holder.get("v")
        if not ok or v is None:
            skipped.append(name)
            continue
        total_passed += v.passed
        total_failed += v.failed
        print(f"  -- {name}: {v.passed} passed, {v.failed} failed")
        del before

    print()
    print("=" * 70)
    total = total_passed + total_failed
    print(f"  Simple Autoreply summary: {total_passed}/{total} assertions passed")
    if skipped:
        print(f"  Scenarios skipped (setup failed): {', '.join(skipped)}")
    if total_failed:
        print(f"  x {total_failed} assertion(s) FAILED")
        sys.exit(1)
    if skipped:
        sys.exit(1)
    print("  All Simple Autoreply assertions passed.")


if __name__ == "__main__":
    main()
