"""Hardware abstraction for the chat app.

Every badge-only import is guarded here so all other modules (codec, gossip,
radio, keyboard, screens) stay pure Python and host-testable. Code runs on BOTH
MicroPython (badge) and CPython (sim/tests): use the ticks shims below, never
bare time.time() deltas.

Adapted (copied, not imported cross-package) from twin_flame/hw.py +
twin_flame/sync_ble.py per plans/chat-2026.md section 16. Do not modify
twin_flame/ or infection/.
"""

import time

# --- ticks shims (CPython lacks ticks_ms/ticks_diff) ------------------------
if hasattr(time, "ticks_ms"):
    ticks_ms = time.ticks_ms
    ticks_diff = time.ticks_diff
else:
    def ticks_ms():
        return int(time.monotonic() * 1000)

    def ticks_diff(a, b):
        return a - b


# --- LEDs -------------------------------------------------------------------
try:
    from tildagonos import tildagonos as _tildagonos
    HAS_LEDS = True
except ImportError:
    _tildagonos = None
    HAS_LEDS = False

# Firmware pattern display owns the ring unless told otherwise.
try:
    from system.eventbus import eventbus as _eventbus
    from system.patterndisplay.events import PatternDisable, PatternEnable
    _HAS_PATTERN = True
except ImportError:
    _HAS_PATTERN = False


# ON-BADGE: the LED ring path (tildagonos.leds + pattern bus). Host runs are
# no-ops via the HAS_LEDS/_HAS_PATTERN guards; colour/strobe logic is tested
# host-side, the physical write only on hardware.
def claim_leds():
    """Suppress the firmware pattern display so we own the ring."""
    if _HAS_PATTERN:
        _eventbus.emit(PatternDisable())


def release_leds():
    """Blank the ring and hand it back to the firmware pattern display."""
    if HAS_LEDS:
        try:
            for i in range(1, 20):
                _tildagonos.leds[i] = (0, 0, 0)
            _tildagonos.leds.write()
        except Exception:
            pass
    if _HAS_PATTERN:
        _eventbus.emit(PatternEnable())


def write_leds(colors):
    """colors: list of 19 (r, g, b) 0-255 tuples for ring LEDs 1..19."""
    if not HAS_LEDS:
        return
    leds = _tildagonos.leds
    for i in range(19):
        leds[i + 1] = colors[i]
    leds.write()


RING_LEDS = 12  # the visible top-ring pixels; chain indices 1..12 (1-based)


def set_ring(color):
    """Set the 12 visible ring LEDs to one (r, g, b) 0-255 tuple.

    Stays inside indices 1..12 and never raises: LED-count/indexing differs
    between firmware versions (the "No such LED" hazard), and a notification
    blink must never be able to crash the app.
    """
    if not HAS_LEDS:
        return
    try:
        leds = _tildagonos.leds
        for i in range(1, RING_LEDS + 1):
            leds[i] = color
        leds.write()
    except Exception:
        pass


# --- 2026 frontboard touch ring ---------------------------------------------
try:
    from frontboards.twentysix import TOUCH
    HAS_TOUCH = True
except ImportError:
    TOUCH = None
    HAS_TOUCH = False


# --- firmware floor for BLE radio -------------------------------------------
# ON-BADGE: bluetooth.BLE().active(True) hard-HANGS Tildagon firmware older
# than v2.0.0-alpha.3 (a hang, not an exception -- try/except cannot save us).
# Copied verbatim from twin_flame/sync_ble.py per plan section 2/16.
def fw_allows_ble(version):
    """True when ``version`` (an ``ota.get_version()`` string) is safe for BLE.

    Safe means >= v2.0.0-alpha.3. Handles the shapes the firmware has actually
    shipped ("v1.6.0", "v1.12.3", "v2.0.0-alpha.6") plus plain releases
    ("v2.0.0", "v2.1.0") and beta/rc prereleases. Pure string logic so it is
    unit-testable off-badge.
    """
    if not version:
        return False
    v = version.strip()
    if v[:1] in ("v", "V"):
        v = v[1:]
    pre = None
    if "-" in v:
        v, pre = v.split("-", 1)
    try:
        nums = [int(x) for x in v.split(".")]
        major = nums[0]
        minor = nums[1] if len(nums) > 1 else 0
        patch = nums[2] if len(nums) > 2 else 0
    except (ValueError, IndexError):
        return False
    if major < 2:
        return False
    if (major, minor, patch) != (2, 0, 0):
        return True                    # 2.0.1+ / 2.1+ / 3+ - past the floor
    if pre is None:
        return True                    # plain v2.0.0 release
    if pre.startswith("alpha."):
        try:
            return int(pre[6:]) >= 3
        except ValueError:
            return False
    # beta/rc sort after every alpha, so they are past the floor.
    return pre.startswith("beta") or pre.startswith("rc")


def _ble_fw_safe():
    """On-badge check: does the running firmware clear the BLE floor?"""
    try:
        import ota
        return fw_allows_ble(ota.get_version())
    except Exception:
        return False


# --- HTTP transport ---------------------------------------------------------
# Server-era helper; dead code under the P2P plan (no laptop server). Kept so a
# stale import doesn't crash during the M1-M6 transition; pruned in M7.
# ON-BADGE: unused by the P2P app.
try:
    import urequests as _urequests
    HAS_HTTP = True
except ImportError:
    try:
        import requests as _urequests  # sim or tests
        HAS_HTTP = True
    except ImportError:
        _urequests = None
        HAS_HTTP = False


def http_request(method, url, json_body=None, timeout=10):
    """Perform an HTTP request and return parsed JSON, or None on failure.

    Never raises: WiFi at a festival is flaky and chat is best-effort. The
    caller decides whether a None means "retry" or "show offline".
    """
    if not HAS_HTTP:
        return None
    try:
        if method == "GET":
            r = _urequests.get(url, timeout=timeout)
        else:
            r = _urequests.post(url, json=json_body, timeout=timeout)
        try:
            return r.json()
        finally:
            r.close()
    except Exception:
        return None


# --- identity ----------------------------------------------------------------
def badge_id():
    """Stable 32-bit id from machine.unique_id() on-badge, random in sim/tests.

    Randomising per run in sim is deliberate: two simulator instances must
    get different ids, and the id only needs to be stable for one session.
    """
    try:
        import machine
        uid = machine.unique_id()
        h = 5381
        for b in uid:
            h = ((h * 33) ^ b) & 0xFFFFFFFF
        return h or 1
    except Exception:
        import random
        return random.getrandbits(32) or 1


# --- persistence -------------------------------------------------------------
def read_text(path):
    """Read a small text file, or return None if missing/unreadable."""
    try:
        with open(path, "r") as f:
            return f.read()
    except Exception:
        return None


def write_text(path, text):
    """Best-effort write; never raise (flash may be read-only in sim)."""
    try:
        with open(path, "w") as f:
            f.write(text)
    except Exception:
        pass