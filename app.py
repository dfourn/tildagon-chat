"""Chat — P2P serverless chat for the Tildagon badge.

Three screens, driven by a tiny state machine:
  SETUP    pick a nickname, CONFIRM-hold (or keyboard ENTER) to continue
  FEED     scroll the gossip message log; LEFT/RIGHT pick a preset, CONFIRM
           hold sends it; CONFIRM tap (or just typing) opens compose
  COMPOSE  type a message, CONFIRM-hold / ENTER to send, CANCEL tap backspace,
           CANCEL-hold back to feed

Text entry works two ways, both feeding the same KeyboardState:
  * six-button T9 (UP/DOWN cluster, LEFT/RIGHT letter, CONFIRM tap commit);
  * the keebdexpansion keyboard hexpansion, whose driver emits firmware
    ``ButtonDownEvent``s with ``events.keyboard`` Keyboard-group buttons
    (letters/digits/symbols by name, SPACE/BACKSPACE/ENTER, modifiers, and
    icon keys we map to ASCII emotes -- see keyboard.HEX_EMOTES).
    ENTER and ESCAPE also carry System-button parents (CONFIRM/CANCEL), so
    when the event handler consumes one it arms a suppress flag that swallows
    the duplicate edge the poll path would otherwise see.

The UI is laid out for the ROUND 240px display: safe half-width at height y
is sqrt(120^2 - y^2), so everything is centred and kept inside the circle
(status bar at y=-92 has ~±77px; the old left-anchored x=-116 layout was
invisible outside ~±60px).

No server, no WiFi. Messages gossip over connectionless BLE adverts via the
``RadioBridge`` (radio.py) driving a ``GossipEngine`` (gossip.py). Everything
is best-effort: out of range = you just hear fewer messages, never a crash.
If BLE is unavailable (old firmware), a red "no radio" banner shows and the
UI stays usable as a local scratchpad.

Plan reference: plans/chat-2026.md (P2P pivot, screens, controls).
"""

import app

from events.input import Buttons, BUTTON_TYPES

try:
    from events.input import ButtonDownEvent
    from system.eventbus import eventbus
except ImportError:  # bare host tests without the event stubs
    ButtonDownEvent = None
    eventbus = None

try:
    from events.keyboard import KEYBOARD_BUTTONS
except ImportError:
    KEYBOARD_BUTTONS = None

from . import config
from . import hw
from . import keyboard as kbmod
from .gossip import GossipEngine
from .radio import RadioBridge, make_sync

_BTN_NAMES = ("UP", "DOWN", "LEFT", "RIGHT", "CONFIRM", "CANCEL")

# Keyboard-group names the composer never consumes as text. Arrow keys keep
# their System parents so they drive the T9 cursor through the poll path.
_KB_PASS = (
    "UP", "DOWN", "LEFT", "RIGHT", "SHIFT", "CTRL", "LCTRL", "ALT", "TAB",
    "NOTHING", "CAP", "FN", "FNED",
)

SETUP, FEED, COMPOSE = "setup", "feed", "compose"


class ChatApp(app.App):
    """The launcher entry point. metadata.json names ChatApp."""

    def __init__(self):
        super().__init__()
        self.button_states = Buttons(self)
        self._btn_prev = {n: False for n in _BTN_NAMES}

        # identity
        self.bid = hw.badge_id()
        nick = (hw.read_text(config.NICK_PATH) or "").strip()
        # channel tag persisted as a small int string (0 = proximity room)
        try:
            channel = int(hw.read_text(config.CHANNEL_PATH) or "0")
        except (TypeError, ValueError):
            channel = 0
        channel &= 0x07

        # P2P transport: engine + radio bridge. make_sync() picks SimSync in
        # the simulator, BLESync on hardware (guarded by the firmware floor).
        self.engine = GossipEngine(self_id=self.bid, nick=nick or "anon",
                                   channels_bitmap=(1 << channel))
        self.bridge = RadioBridge(self.engine, make_sync())
        self._radio_ok = bool(self.bridge.start(now=hw.ticks_ms()))
        # NOTE: chat does not use the LED ring -- do not claim/release it.

        # input helpers
        self.kb = kbmod.KeyboardState()
        self.hold_confirm = kbmod.HoldTracker()
        self.hold_cancel = kbmod.HoldTracker()
        # Set when the keyboard event handler consumed an ENTER/ESCAPE whose
        # System parent (CONFIRM/CANCEL) the poll path will also see; cleared
        # once that button level drops.
        self._suppress_confirm = False
        self._suppress_cancel = False

        # Hexpansion keyboard: the driver broadcasts ButtonDownEvents with
        # Keyboard-group buttons; input events are focus-gated by the bus.
        if eventbus is not None and ButtonDownEvent is not None:
            eventbus.on(ButtonDownEvent, self._on_key_event, self)

        # screen state
        self.screen = SETUP if not nick or nick == "anon" else FEED
        self.preset_idx = 0
        self.kb.reset(nick if self.screen == SETUP else "")
        self.channel = channel

    # --- input edges ---------------------------------------------------------
    def _poll_edges(self):
        """Return {name: rising_edge_bool} and update the level tracker."""
        pressed = {}
        for n in _BTN_NAMES:
            now = bool(self.button_states.get(BUTTON_TYPES[n]))
            pressed[n] = now and not self._btn_prev[n]
            self._btn_prev[n] = now
        return pressed

    def _level(self, name):
        return bool(self.button_states.get(BUTTON_TYPES[name]))

    # --- hexpansion keyboard -------------------------------------------------
    def _kb_shift_down(self):
        if KEYBOARD_BUTTONS is None:
            return False
        return bool(self.button_states.get(KEYBOARD_BUTTONS["SHIFT"]))

    def _on_key_event(self, event):
        """Consume Keyboard-group buttons (keebdexpansion et al)."""
        btn = event.button
        find = getattr(btn, "find_parent_in_group", None)
        kbd = find("Keyboard") if find is not None else None
        if kbd is None:
            return  # System/Touch button: the poll path owns it
        name = kbd.name
        if name in _KB_PASS:
            return  # arrows fall through to T9 via their System parents
        if name == "ENTER":
            self._suppress_confirm = True
            self.hold_confirm = kbmod.HoldTracker()
            if self.screen == SETUP:
                self._finish_setup()
            elif self.screen == COMPOSE:
                self._send_current()
            else:
                self.screen = COMPOSE
                self.kb.reset("")
            return
        if name == "ESCAPE":
            self._suppress_cancel = True
            self.hold_cancel = kbmod.HoldTracker()
            if self.screen == COMPOSE:
                self.screen = FEED
            elif self.screen == FEED:
                self._exit()
            return
        if name == "BACKSPACE":
            if self.screen in (SETUP, COMPOSE):
                self.kb.cancel_tap()
            return
        text = kbmod.hex_key_text(name, self._kb_shift_down())
        if text is None:
            return
        if self.screen == FEED:
            # Just start typing: fall straight into compose.
            self.screen = COMPOSE
            self.kb.reset("")
            self.hold_confirm = kbmod.HoldTracker()
            self.hold_cancel = kbmod.HoldTracker()
        self.kb.add_text(text)

    # --- main loop -----------------------------------------------------------
    def update(self, delta):
        now = hw.ticks_ms()
        pressed = self._poll_edges()

        # Pump the radio every frame: advertise, scan/drain, ingest, tx.
        self.bridge.update(now)

        result = self._dispatch(now, pressed)

        # Retire suppress flags once the parented button is released, so a
        # consumed keyboard ENTER/ESCAPE can't eat a later real press.
        if self._suppress_confirm and not self._level("CONFIRM"):
            self._suppress_confirm = False
        if self._suppress_cancel and not self._level("CANCEL"):
            self._suppress_cancel = False
        return result

    def _dispatch(self, now, pressed):
        # CANCEL exits the app from the feed only (setup/compose use it for
        # backspace; keyboard ESCAPE mirrors this via _on_key_event).
        if (self.screen == FEED and pressed["CANCEL"]
                and not self._suppress_cancel):
            self._exit()
            return True

        if self.screen == SETUP:
            return self._update_setup(now, pressed)
        if self.screen == FEED:
            return self._update_feed(now, pressed)
        if self.screen == COMPOSE:
            return self._update_compose(now, pressed)
        return False

    # --- per-screen update ---------------------------------------------------
    def _update_setup(self, now, pressed):
        self._drive_kb(now, pressed, on_submit=self._finish_setup,
                       on_cancel_hold=self.kb.cancel_hold)
        return True  # keep redrawing the keyboard

    def _finish_setup(self):
        nick = self.kb.text.strip()
        if nick:
            self.engine.set_nick(nick)
            hw.write_text(config.NICK_PATH, nick)
        self.screen = FEED

    def _update_feed(self, now, pressed):
        # CONFIRM tap -> compose; CONFIRM hold -> send the shown preset.
        if pressed["CONFIRM"]:
            self.hold_confirm.press(now)
        if self.hold_confirm.held(now):
            if self._suppress_confirm:
                self.hold_confirm = kbmod.HoldTracker()
            else:
                self._send_preset()
            return True
        if not self._level("CONFIRM"):
            outcome = self.hold_confirm.release(now)
            if outcome == "tap" and not self._suppress_confirm:
                self.screen = COMPOSE
                self.kb.reset("")
                self.hold_confirm = kbmod.HoldTracker()
                self.hold_cancel = kbmod.HoldTracker()
                return True
        # LEFT/RIGHT cycle presets
        if pressed["LEFT"]:
            self.preset_idx = (self.preset_idx - 1) % len(config.PRESETS)
        if pressed["RIGHT"]:
            self.preset_idx = (self.preset_idx + 1) % len(config.PRESETS)
        # UP/DOWN change channel tag (cheap proximity-room switch)
        if pressed["UP"]:
            self._set_channel((self.channel + 1) % config.NUM_CHANNELS)
        if pressed["DOWN"]:
            self._set_channel((self.channel - 1) % config.NUM_CHANNELS)
        return True

    def _set_channel(self, channel):
        self.channel = channel & 0x07
        self.engine.set_channels(1 << self.channel)
        hw.write_text(config.CHANNEL_PATH, str(self.channel))

    def _update_compose(self, now, pressed):
        # CANCEL tap backspaces (in _drive_kb); CANCEL hold backs out to feed.
        self._drive_kb(now, pressed, on_submit=self._send_current,
                       on_cancel_hold=self._compose_back)
        return True

    def _compose_back(self):
        self.screen = FEED

    def _send_current(self):
        text = self.kb.text
        if text.strip():
            # send() chunks + seeds the store + arms the own-burst; the bridge
            # pumps the adverts onto the radio on subsequent update() calls.
            # Returns None when rate-limited -- we drop quietly.
            self.engine.send(text, self.channel, now=hw.ticks_ms())
        self.kb.reset("")
        self.screen = FEED

    def _send_preset(self):
        text = config.PRESETS[self.preset_idx % len(config.PRESETS)]
        self.engine.send(text, self.channel, now=hw.ticks_ms())

    # --- shared keyboard driving --------------------------------------------
    def _drive_kb(self, now, pressed, on_submit, on_cancel_hold):
        """Wire six buttons to the KeyboardState + HoldTrackers.

        on_submit fires on a CONFIRM hold (send/done).
        on_cancel_hold fires on a CANCEL hold (clear in setup, back in compose).
        Taps: CONFIRM commits the highlighted T9 char, CANCEL backspaces.
        """
        # track press/release into the hold detectors
        if pressed["CONFIRM"]:
            self.hold_confirm.press(now)
        if pressed["CANCEL"]:
            self.hold_cancel.press(now)

        # movement edges
        if pressed["UP"]:
            self.kb.up()
        if pressed["DOWN"]:
            self.kb.down()
        if pressed["LEFT"]:
            self.kb.left()
        if pressed["RIGHT"]:
            self.kb.right()

        # hold-while-down detection (fires once)
        if self.hold_confirm.held(now):
            if not self._suppress_confirm:
                self.kb.confirm_hold()
                on_submit()
            return
        if self.hold_cancel.held(now):
            if not self._suppress_cancel:
                on_cancel_hold()
            self.hold_cancel = kbmod.HoldTracker()  # reset to avoid re-fire
            return

        # CONFIRM tap (release before hold threshold) commits one char
        if not self._level("CONFIRM"):
            outcome = self.hold_confirm.release(now)
            if outcome == "tap" and not self._suppress_confirm:
                self.kb.confirm_tap()
        # CANCEL tap backspaces one char
        if not self._level("CANCEL"):
            outcome = self.hold_cancel.release(now)
            if outcome == "tap" and not self._suppress_cancel:
                self.kb.cancel_tap()

    # --- draw ----------------------------------------------------------------
    # Round-display layout: keep text inside |x| < sqrt(120^2 - y^2).

    def draw(self, ctx):
        ctx.save()
        ctx.rgb(*config.COL_BG).rectangle(-120, -120, 240, 240).fill()
        if self.screen == SETUP:
            self._draw_keyboard(ctx, "pick a nickname", "done", "clear")
        elif self.screen == FEED:
            self._draw_feed(ctx)
        elif self.screen == COMPOSE:
            self._draw_keyboard(ctx, "say something", "send", "back")
        ctx.text_align = ctx.LEFT
        ctx.restore()
        self.draw_overlays(ctx)

    def _draw_status_bar(self, ctx):
        """Top arc: nearby count + nick + channel, centred at y=-92."""
        now = hw.ticks_ms()
        ctx.font_size = config.FONT_SIZE_META
        ctx.text_align = ctx.CENTER
        if self._radio_ok:
            ctx.rgb(*config.COL_MUTED)
            line = "%d nearby   @%s  ch%d" % (
                len(self.bridge.peers(now)),
                self.engine.nick[:10], self.channel)
        else:
            ctx.rgb(0.9, 0.4, 0.4)
            line = "no radio   @%s  ch%d" % (
                self.engine.nick[:10], self.channel)
        ctx.move_to(0, -92).text(line)

    def _draw_feed(self, ctx):
        self._draw_status_bar(ctx)
        ctx.text_align = ctx.CENTER
        ctx.rgb(*config.COL_TEXT)
        ctx.font_size = config.FONT_SIZE_TITLE
        ctx.move_to(0, -72).text("Chat")

        # messages: last N, newest at the bottom, one line each
        msgs = self.engine.messages(channel=self.channel)[-config.SCROLL_LINES:]
        ctx.font_size = config.FONT_SIZE_MSG
        if not msgs:
            ctx.rgb(*config.COL_MUTED)
            ctx.move_to(0, -6).text("no messages yet -- say hi!")
        else:
            ctx.text_align = ctx.LEFT
            y = -50
            for m in msgs:
                mine = m.origin_id == self.bid
                who = "me" if mine else (m.nick or "anon")[:8]
                ctx.rgb(*(config.COL_MINE if mine else config.COL_OTHER))
                ctx.move_to(-88, y).text((who + ": " + m.text)[:26])
                y += 16
            ctx.text_align = ctx.CENTER

        # preset row + hint
        idx = self.preset_idx % len(config.PRESETS)
        ctx.font_size = config.INPUT_FONT_SIZE
        ctx.rgb(*config.COL_ACCENT)
        ctx.move_to(0, 72).text("< " + config.PRESETS[idx] + " >")

        ctx.font_size = config.FONT_SIZE_META
        cooldown = self.engine.cooldown_remaining_ms(hw.ticks_ms())
        if cooldown > 0:
            ctx.rgb(*config.COL_ACCENT)
            ctx.move_to(0, 90).text(
                "wait %ds to send" % ((cooldown + 999) // 1000))
        else:
            ctx.rgb(*config.COL_MUTED)
            ctx.move_to(0, 90).text("OK=write  hold=preset  U/D=ch")

    def _draw_keyboard(self, ctx, prompt, submit_word, cancel_word):
        self._draw_status_bar(ctx)
        ctx.text_align = ctx.CENTER

        ctx.rgb(*config.COL_ACCENT)
        ctx.font_size = 12
        ctx.move_to(0, -66).text(prompt)

        # composed text, tail-clipped, with a cursor
        ctx.rgb(*config.COL_TEXT)
        ctx.font_size = 18
        ctx.move_to(0, -22).text(self.kb.text[-18:] + "_")

        # T9 cursor: big preview char + its cluster with the pick bracketed
        chv = self.kb.current_char()
        ctx.rgb(*config.COL_ACCENT)
        ctx.font_size = 26
        ctx.move_to(0, 26).text("_" if chv == " " else chv)

        cluster = kbmod.CLUSTERS[self.kb.cluster_idx]
        li = self.kb.letter_idx % len(cluster)
        parts = []
        for i, c in enumerate(cluster):
            shown = "_" if c == " " else c
            parts.append("[" + shown + "]" if i == li else shown)
        ctx.rgb(*config.COL_MUTED)
        ctx.font_size = config.INPUT_FONT_SIZE
        ctx.move_to(0, 52).text(" ".join(parts))

        # hints
        ctx.font_size = config.FONT_SIZE_META
        ctx.move_to(0, 78).text("type, or UDLR + OK taps")
        ctx.move_to(0, 92).text(
            "hold OK=%s  hold X=%s" % (submit_word, cancel_word))

    # --- lifecycle niceties --------------------------------------------------
    def background_update(self, delta):
        # Keep the radio gossiping while backgrounded so the feed is fresh
        # when the user returns.
        self.bridge.update(hw.ticks_ms())

    def _exit(self):
        # Stop the radio cleanly. (No LED ring to release -- chat never claimed it.)
        try:
            self.bridge.stop()
        except Exception:
            pass
        self.minimise()


# App-store contract: the store's launcher entry resolves this name.
__app_export__ = ChatApp
