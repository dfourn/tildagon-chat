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
from .notify import LedNotifier
from .radio import RadioBridge, make_sync

_BTN_NAMES = ("UP", "DOWN", "LEFT", "RIGHT", "CONFIRM", "CANCEL")

# Keyboard-group names the composer never consumes as text. Arrow keys keep
# their System parents so they drive the T9 cursor through the poll path.
_KB_PASS = (
    "UP", "DOWN", "LEFT", "RIGHT", "SHIFT", "CTRL", "LCTRL", "ALT", "TAB",
    "NOTHING", "CAP", "FN", "FNED",
)

SETUP, FEED, COMPOSE, SPLASH = "setup", "feed", "compose", "splash"


class ChatApp(app.App):
    """The launcher entry point. metadata.json names ChatApp."""

    def __init__(self):
        super().__init__()
        self.button_states = Buttons(self)
        self._btn_prev = {n: False for n in _BTN_NAMES}

        # identity
        self.bid = hw.badge_id()
        nick = (hw.read_text(config.NICK_PATH) or "").strip()

        # P2P transport: engine + radio bridge. make_sync() picks SimSync in
        # the simulator, BLESync on hardware (guarded by the firmware floor).
        # Single shared channel for all badges (0) -- no per-badge channel
        # switching, so two badges can never silently drift onto different
        # channels and stop seeing each other's messages.
        self.engine = GossipEngine(self_id=self.bid, nick=nick or "anon",
                                   channels_bitmap=1)
        self.bridge = RadioBridge(self.engine, make_sync())
        self._radio_ok = bool(self.bridge.start(now=hw.ticks_ms()))
        # LED ring: claimed only for the duration of a notify pulse; the
        # notifier releases it (blank + PatternEnable) when the pulse ends
        # and its writes stay inside the "No such LED"-safe indices.
        self.led_notify = LedNotifier()

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
        # Launch splash shows the app name + version (config.APP_VERSION) for
        # SPLASH_MS, or until any button/keyboard edge. It then falls through
        # to the natural first screen (SETUP if no nick yet, else FEED).
        self._splash_next = SETUP if not nick or nick == "anon" else FEED
        self._splash_start = hw.ticks_ms()
        self.screen = SPLASH
        self.preset_idx = 0
        self.kb.reset(nick if self._splash_next == SETUP else "")
        self.channel = 0

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
        # Any keyboard input dismisses the splash early.
        if self.screen == SPLASH:
            self._leave_splash()
            return
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
            else:
                # FEED and SETUP: ESC leaves the app. (SETUP used to fall
                # through, so the CANCEL parent leaked in as a backspace;
                # backspace on the keyboard is the BACKSPACE key.)
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

        # Relaunch-after-exit: the launcher caches the app instance, so after
        # _exit() stopped the bridge a later foreground update() means the
        # user launched us again -- restart the radio. (Never done from
        # background_update: while minimised a stopped bridge stays stopped.)
        if self.bridge.stopped:
            self._radio_ok = bool(self.bridge.start(now=now))

        pressed = self._poll_edges()

        # Pump the radio every frame: advertise, scan/drain, ingest, tx.
        self.bridge.update(now)

        # LED notify: pulse the ring when someone else's message lands on the
        # current channel (foreground only -- while backgrounded the events
        # queue up, bounded, and coalesce into one pulse on return).
        for _origin_id, chan in self.engine.take_new_messages():
            if chan == self.channel:
                self.led_notify.notify(now)
        self.led_notify.update(now)

        result = self._dispatch(now, pressed)

        # Retire suppress flags once the parented button is released, so a
        # consumed keyboard ENTER/ESCAPE can't eat a later real press.
        if self._suppress_confirm and not self._level("CONFIRM"):
            self._suppress_confirm = False
        if self._suppress_cancel and not self._level("CANCEL"):
            self._suppress_cancel = False
        return result

    def _dispatch(self, now, pressed):
        if self.screen == SPLASH:
            return self._update_splash(now, pressed)

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
    def _leave_splash(self):
        """Advance from the splash to the natural first screen."""
        self.screen = self._splash_next

    def _update_splash(self, now, pressed):
        # Auto-advance after SPLASH_MS, or any button tap dismisses early.
        if hw.ticks_diff(now, self._splash_start) >= config.SPLASH_MS:
            self._leave_splash()
            return True
        for n in _BTN_NAMES:
            if pressed[n]:
                self._leave_splash()
                break
        return True

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
        return True

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
        if self.screen == SPLASH:
            self._draw_splash(ctx)
        elif self.screen == SETUP:
            self._draw_keyboard(ctx, "pick a nickname", "done", "clear")
        elif self.screen == FEED:
            self._draw_feed(ctx)
        elif self.screen == COMPOSE:
            self._draw_keyboard(ctx, "say something", "send", "back")
        ctx.text_align = ctx.LEFT
        ctx.restore()
        self.draw_overlays(ctx)

    def _draw_splash(self, ctx):
        """Launch splash: app name, tagline, and the version in one clean place."""
        ctx.text_align = ctx.CENTER
        ctx.rgb(*config.COL_TEXT)
        ctx.font_size = 24
        ctx.move_to(0, -16).text("Chat")
        ctx.rgb(*config.COL_ACCENT)
        ctx.font_size = config.FONT_SIZE_TITLE
        ctx.move_to(0, 8).text("v" + config.APP_VERSION)
        ctx.rgb(*config.COL_MUTED)
        ctx.font_size = config.FONT_SIZE_META
        ctx.move_to(0, 40).text("serverless P2P over BLE")
        ctx.move_to(0, 92).text("tap to continue")

    def _draw_status_bar(self, ctx):
        """Top arc: radio diagnostics + nick, centred at y=-92."""
        now = hw.ticks_ms()
        ctx.font_size = config.FONT_SIZE_META
        ctx.text_align = ctx.CENTER
        sync_cls = self.bridge.sync.__class__.__name__
        ble_active = getattr(self.bridge.sync, "active", False)
        if self._radio_ok:
            ctx.rgb(*config.COL_MUTED)
            line = "%d nr @%s" % (
                len(self.bridge.peers(now)), self.engine.nick[:8])
        else:
            ctx.rgb(0.9, 0.4, 0.4)
            line = "NO RADIO @%s" % (self.engine.nick[:8],)
        ctx.move_to(0, -92).text(line)
        # Second diagnostic line: sync backend + BLE active + store size + id.
        # (The version lives on the launch splash, not here.)
        ctx.rgb(*config.COL_MUTED)
        ctx.move_to(0, -80).text(
            "%s act=%d st=%d id=%x" % (
                sync_cls[:3], int(ble_active),
                self.engine.store_size(), self.bid & 0xFFFF))

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
            ctx.move_to(0, 90).text("OK=write  hold=preset  L/R=cycle")

        # Persistent version footer -- the splash only shows for SPLASH_MS
        # (or 0 frames if a launch button edge bleeds through), so this is
        # the reliable way to confirm which build is on a badge.
        ctx.rgb(*config.COL_MUTED)
        ctx.move_to(0, 108).text("v" + config.APP_VERSION)

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
        # when the user returns. Never hold the ring while another app is
        # foregrounded: end any in-flight pulse (no-op when idle).
        self.led_notify.stop()
        self.bridge.update(hw.ticks_ms())

    def _exit(self):
        # Stop the radio cleanly and hand back the ring if a pulse is live.
        self.led_notify.stop()
        try:
            self.bridge.stop()
        except Exception:
            pass
        self.minimise()


# App-store contract: the store's launcher entry resolves this name.
__app_export__ = ChatApp
