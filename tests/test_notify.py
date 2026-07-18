#!/usr/bin/env python3
"""LedNotifier: claim/blink/release cycle, coalescing, idempotent stop.

The notifier is a pure timing state machine that drives injected
claim/write/release callables (production wires them to hw.claim_leds /
hw.set_ring / hw.release_leds). Own sends never reach the notifier (the
engine's take_new_messages skips origin_id == self_id), so this test covers
only the pulse behaviour itself.

SPEC-NOTIFY-001  notify() claims + starts a pulse; update() double-blinks
SPEC-NOTIFY-002  pulse ends at LED_PULSE_MS and releases the ring
SPEC-NOTIFY-003  coalescing: a second notify within the window is a no-op
SPEC-NOTIFY-004  stop() is idempotent and safe when idle
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import badge_stubs
badge_stubs.install()

from chat import config, notify


class _FakeRing:
    """Records claim/release + every colour written, in order."""

    def __init__(self):
        self.claimed = 0
        self.released = 0
        self.writes = []  # list of (r, g, b)

    def claim(self):
        self.claimed += 1

    def write(self, color):
        self.writes.append(color)

    def release(self):
        self.released += 1


def _notifier():
    ring = _FakeRing()
    nf = notify.LedNotifier(
        claim=ring.claim, write=ring.write, release=ring.release)
    return nf, ring


def test_notify_pulse_claims_and_blinks():
    """SPEC-NOTIFY-001: a pulse claims the ring and writes blink colours."""
    print("Testing pulse claim + blink...")
    nf, ring = _notifier()
    assert not nf.active, "fresh notifier should be idle"

    started = nf.notify(now=0)
    assert started, "first notify() should start a pulse"
    assert ring.claimed == 1, "claim must fire exactly once on pulse start"
    assert nf.active, "notifier should be active after notify()"

    # Drive the blink across one full on/off phase change.
    nf.update(now=config.LED_FRAME_MS)           # phase 0 -> want on
    assert ring.writes == [config.LED_NOTIFY_COLOR], \
        "first write should be the notify colour: %r" % ring.writes
    nf.update(now=3 * config.LED_FRAME_MS)       # phase 1 -> want off
    assert ring.writes == [config.LED_NOTIFY_COLOR, (0, 0, 0)], \
        "second write should blank the ring: %r" % ring.writes
    print("  pulse + blink OK")


def test_notify_pulse_ends_and_releases():
    """SPEC-NOTIFY-002: at LED_PULSE_MS the pulse ends and releases the ring."""
    print("Testing pulse end + release...")
    nf, ring = _notifier()
    nf.notify(now=0)
    # one tick before the end: still active
    nf.update(now=config.LED_PULSE_MS - 1)
    assert nf.active, "should still be active just before LED_PULSE_MS"
    assert ring.released == 0, "ring should not be released mid-pulse"
    # at/after the end: pulse terminates and the ring is handed back
    nf.update(now=config.LED_PULSE_MS)
    assert not nf.active, "pulse must end at LED_PULSE_MS"
    assert ring.released == 1, "ring must be released exactly once at end"
    print("  end + release OK")


def test_notify_coalescing():
    """SPEC-NOTIFY-003: a second notify inside the coalesce window is a no-op."""
    print("Testing coalescing...")
    nf, ring = _notifier()
    assert nf.notify(now=0) is True, "first pulse starts"
    first_claimed = ring.claimed
    # Second notify inside the coalesce window: must not re-claim or restart.
    within = config.LED_NOTIFY_COALESCE_MS - 1
    assert nf.notify(now=within) is False, \
        "notify inside coalesce window should return False"
    assert ring.claimed == first_claimed, \
        "coalesced notify must not re-claim the ring"
    # After the window a new pulse is allowed, but the notifier must still
    # own the ring from the active pulse (no second claim).
    nf.update(now=config.LED_PULSE_MS)  # end the first pulse -> release
    assert nf.notify(now=config.LED_NOTIFY_COALESCE_MS + 1) is True, \
        "notify after window should start a fresh pulse"
    print("  coalescing OK")


def test_notify_stop_idempotent():
    """SPEC-NOTIFY-004: stop() ends a pulse and is safe when idle."""
    print("Testing idempotent stop()...")
    nf, ring = _notifier()
    # stop() while idle: no-op, must not raise
    nf.stop()
    assert ring.released == 0, "idle stop() must not release"
    # stop() during a pulse: ends it and releases once
    nf.notify(now=0)
    nf.stop()
    assert not nf.active, "stop() must clear the active pulse"
    assert ring.released == 1, "stop() must release the ring once"
    # stop() again: still safe (background_update/_exit call it unconditionally)
    nf.stop()
    assert ring.released == 1, "repeat stop() must be a no-op"
    print("  idempotent stop OK")


if __name__ == "__main__":
    test_notify_pulse_claims_and_blinks()
    test_notify_pulse_ends_and_releases()
    test_notify_coalescing()
    test_notify_stop_idempotent()
    print("\nALL NOTIFY TESTS PASSED")