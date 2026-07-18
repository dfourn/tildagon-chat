"""LED new-message notifier.

A single ``LedNotifier`` owns the ring while a pulse is active: claim on
pulse start (suppresses the firmware pattern display), a slow double-blink
for ``LED_PULSE_MS``, release (blank + hand back) at the end. Pulses are
coalesced to at most one per ``LED_NOTIFY_COALESCE_MS`` so a burst of gossip
can never strobe the ring.

Timing logic is pure and takes an explicit ``now`` (ms) like the gossip
engine, so it is host-testable; the physical writes go through injected
callables that default to the hw helpers (``hw.set_ring`` stays inside the
known-good LED indices 1..12 and swallows the "No such LED" hazard).
"""

from . import config, hw


class LedNotifier:
    """Blink the visible LED ring when another badge's message arrives."""

    def __init__(self, claim=None, write=None, release=None):
        self._claim = claim if claim is not None else hw.claim_leds
        self._write = write if write is not None else hw.set_ring
        self._release = release if release is not None else hw.release_leds
        self._pulse_start = None   # ms the active pulse began, or None=idle
        self._last_pulse = None    # ms the last pulse began (coalescing)
        self._lit = False          # current physical on/off state

    @property
    def active(self):
        return self._pulse_start is not None

    def notify(self, now):
        """Request a pulse. Returns True if one started (else coalesced)."""
        if (self._last_pulse is not None and
                now - self._last_pulse < config.LED_NOTIFY_COALESCE_MS):
            return False
        self._last_pulse = now
        if self._pulse_start is None:
            self._claim()
        self._pulse_start = now
        return True

    def update(self, now):
        """Drive the blink. Writes are edge-triggered: zero cost while idle,
        one write per on/off phase change while pulsing (~3 Hz, no strobe)."""
        if self._pulse_start is None:
            return
        if now - self._pulse_start >= config.LED_PULSE_MS:
            self.stop()
            return
        phase = (now - self._pulse_start) // (2 * config.LED_FRAME_MS)
        want = phase % 2 == 0
        if want != self._lit:
            self._lit = want
            self._write(config.LED_NOTIFY_COLOR if want else (0, 0, 0))

    def stop(self):
        """End any active pulse: blank the ring and hand it back. Idempotent;
        safe to call from _exit()/background_update when idle."""
        if self._pulse_start is None:
            return
        self._pulse_start = None
        self._lit = False
        self._release()
