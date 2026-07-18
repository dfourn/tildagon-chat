"""T9-style text entry over the badge's six buttons.

Pure state machine (no hw/ctx imports) so every interaction is host-testable;
hold timing lives in HoldTracker, driven by synthetic now_ms in tests and by
real ticks in the app. Adapted from infection/keyboard.py but trimmed to one
mode (the 2024 badge's six-button fallback) since chat doesn't need the
2026 touch ring.

UP/DOWN move between letter clusters, LEFT/RIGHT cycle letters within one,
CONFIRM tap commits a char, CONFIRM hold submits, CANCEL tap backspaces,
CANCEL hold clears everything.
"""

from . import config

# 12 clusters mapping roughly to a phone keypad, ordered so UP/DOWN traverse
# them sensibly. ASCII only: the server sanitises the same way.
CLUSTERS = [
    "ABC", "DEF", "GH", "IJK", "LM", "NOP",
    "QR", "STU", "VW", "XYZ", " ", ".,'!?",
]


class KeyboardState:
    """Six-button T9 entry. tap/hold outcomes come from HoldTracker."""

    def __init__(self, max_len=None):
        self.max_len = config.MAX_TEXT_LEN if max_len is None else max_len
        self.text = ""
        self.cluster_idx = 5    # start on the space cluster (mid-list)
        self.letter_idx = 0
        self.submitted = False
        self.cancelled = False

    def _open(self):
        return not (self.submitted or self.cancelled)

    def current_char(self):
        cluster = CLUSTERS[self.cluster_idx]
        return cluster[self.letter_idx % len(cluster)]

    def up(self):
        if self._open():
            self.cluster_idx = (self.cluster_idx - 1) % len(CLUSTERS)
            self.letter_idx = 0

    def down(self):
        if self._open():
            self.cluster_idx = (self.cluster_idx + 1) % len(CLUSTERS)
            self.letter_idx = 0

    def left(self):
        if self._open():
            self.letter_idx = (self.letter_idx - 1) % len(CLUSTERS[self.cluster_idx])

    def right(self):
        if self._open():
            self.letter_idx = (self.letter_idx + 1) % len(CLUSTERS[self.cluster_idx])

    def confirm_tap(self):
        if self._open() and len(self.text) < self.max_len:
            self.text += self.current_char()

    def add_text(self, s):
        """Append literal chars (hexpansion keyboard path), capped at max_len."""
        if self._open():
            room = self.max_len - len(self.text)
            if room > 0:
                self.text += s[:room]

    def confirm_hold(self):
        # Submit WITHOUT committing the preview char. The preview is only
        # committed on an explicit CONFIRM tap; a hold means "send what's
        # already typed". (Matches infection T9 semantics.)
        if self._open():
            self.submitted = True

    def cancel_tap(self):
        if self._open() and self.text:
            self.text = self.text[:-1]

    def cancel_hold(self):
        if self._open():
            self.text = ""       # clear-all on hold

    def reset(self, text=""):
        """Begin a fresh compose (called after a submit/send or a clear).

        Also resets the cursor to the space cluster (neutral start) so a
        previous compose's letter_idx doesn't bleed into the next one.
        """
        self.text = text
        self.cluster_idx = 5
        self.letter_idx = 0
        self.submitted = False
        self.cancelled = False


# --- hexpansion keyboard translation -----------------------------------------
# The keebdexpansion driver emits firmware ButtonDownEvents whose buttons are in
# group "Keyboard" (events/keyboard.py KEYBOARD_BUTTONS): letters as single
# uppercase names, digits/symbols as themselves, plus SPACE and modifier names.
# Kept here (pure) so the mapping is host-testable without firmware modules.

# The keebdex icon keys, mapped to common emotes. ASCII only: the wire codec
# and the badge font both stop at 0x7E, so these are the classic text forms.
HEX_EMOTES = {
    "CIRCLE": ":)",
    "CROSS": ":(",
    "TRIANGLE": ":D",
    "SQUARE": ":|",
    "CLOUD": ":o",
    "DIAMOND": "<3",
}


def hex_key_text(name, shift=False):
    """Translate a Keyboard-group button name to text to insert, or None.

    Letters are lowercase unless ``shift``; digits/symbols pass through
    (the driver sends already-shifted symbol names itself); icon keys
    become their HEX_EMOTES string.
    """
    if name in HEX_EMOTES:
        return HEX_EMOTES[name]
    if name == "SPACE":
        return " "
    if len(name) != 1:
        return None
    if "A" <= name <= "Z":
        return name if shift else name.lower()
    o = ord(name)
    if 0x20 < o <= 0x7E:
        return name
    return None


class HoldTracker:
    """Distinguishes a tap from a >=hold_ms hold on one button.

    Feed press/release edges with now_ms; release() returns "tap" or "hold".
    A hold can also fire while still down via held(now_ms) for snappy UIs.
    """

    def __init__(self, hold_ms=None):
        self.hold_ms = config.SUBMIT_HOLD_MS if hold_ms is None else hold_ms
        self._down_ms = None
        self._fired = False

    def press(self, now_ms):
        self._down_ms = now_ms
        self._fired = False

    def held(self, now_ms):
        """True once, as soon as the press has lasted hold_ms."""
        if self._down_ms is None or self._fired:
            return False
        if now_ms - self._down_ms >= self.hold_ms:
            self._fired = True
            return True
        return False

    def release(self, now_ms):
        """Returns "tap", "hold", or None (hold already fired via held())."""
        if self._down_ms is None:
            return None
        duration = now_ms - self._down_ms
        fired = self._fired
        self._down_ms = None
        self._fired = False
        if fired:
            return None
        return "hold" if duration >= self.hold_ms else "tap"