# PROMPT — resume Chat development

You are picking up the Chat app for EMF Tildagon/Spaceagon badges. Read
`RESUME.md` (state + hardware notes + verification ladder) and skim
`README.md` (user-facing behavior) before touching code. The design plan is
`../plans/chat-2026.md`. Published: https://github.com/dfourn/tildagon-chat
(v1.0.0, Tildagon app store, category Apps).

## Ground rules (hard-won this session — do not relearn them)

- **ASCII only** everywhere: wire codec caps at 0x7E and badge font coverage
  beyond ASCII is unverified. Emotes are text (`:)`, `<3`), never Unicode.
- **The sim and test fakes are laxer than hardware.** The launch crash was
  `ctx.text_align = "left"` — strings work in every fake, TypeError on the
  badge (int-only uctx binding). Any new draw-API usage must be checked
  against `drivers/gc9a01/mp_uctx.c` or verified on-badge before shipping.
- **Round screen**: keep text inside |x| < sqrt(120² − y²). Verify layout by
  rendering PNGs through the sim (see RESUME.md ladder step 3), not by eye.
- **Keyboard events**: the keebdexpansion emits firmware `ButtonDownEvent`s
  with Keyboard-group buttons; ENTER/ESCAPE have System parents
  (CONFIRM/CANCEL) so consuming them requires the suppress-flag dance in
  `app.py` — preserve it when touching input code.
- Deploy = `mpremote fs cp ... :/apps/chat/` + `mpremote reset` (import
  cache). Ship store updates by bumping `version` in `tildagon.toml` +
  tagging a release.
- Run `bash tests/run_all.sh` before any deploy; add tests beside behavior.

## First: known issues (fix before new features)

1. **Radio dead after exit→relaunch.** `_exit()` stops the RadioBridge, but
   the launcher caches the app instance; on relaunch `update()` finds
   `bridge._stopped` and the radio never restarts. Fix: restart the bridge
   (or build a fresh one) when foregrounded after a stop; test via two
   launch cycles in the sim-OS harness.
2. **Confirm keebdex typing on hardware end-to-end** if not already done —
   the event-capture window during development recorded no keystrokes
   (timing was suspected, driver strings confirmed the contract). If typing
   fails: rerun the badge keylogger (RESUME.md) while keys are pressed.
3. **ESC in SETUP backspaces** (flows through as CANCEL). Decide: no-op or
   exit-to-launcher; implement + test.
4. Stale `comms.py` re-export shim and `chat_server/` legacy dir — prune the
   shim once nothing imports it; archive the server dir out of the repo.

## Enhancement backlog (nice-to-haves, roughly by value/effort)

### Feed & messages
- **Scrollback**: UP/DOWN scroll history when >7 messages (move channel
  switching to hold-UP/DOWN or a keyboard shortcut); "▼ n newer"-style ASCII
  indicator when scrolled up; snap to bottom on new own message.
- **Mentions**: `@nick` detection on send sets the codec's existing
  `FLAG_MENTION` bit (wired end-to-end but unused); highlight mention lines
  and pulse one LED (config has LED_PULSE_MS/LED_NOTIFY_COALESCE_MS unused —
  respect the "No such LED" hazard note in app.py before touching the ring).
- **Per-sender colors**: stable color from origin_id hash for feed lines.
- **Word wrap**: two-line rendering for messages >26 chars instead of
  truncation; budget rows accordingly.
- **Unread count**: engine keeps gossiping in background_update; show
  "(n new)" on return to foreground, or a firmware ShowNotificationEvent on
  mention while backgrounded.

### Presence & social
- **Nearby screen**: PeerTable already returns RSSI-sorted peers with nicks
  (radio.py was written expecting a NearbyScreen); add a screen listing
  nick + signal bars, entered via a feed key.
- **Typing indicator**: presence codec already carries FLAG_TYPING_NOW; set
  it while in COMPOSE, render "nick is typing" under the feed.
- **Channel labels**: user-named channels (persisted like NICK_PATH) shown
  in the status arc instead of ch0-7.

### Input
- **Compose cursor editing** (keyboard LEFT/RIGHT move a cursor when the
  hexpansion is present; T9 keeps append-only semantics).
- **Shortcode expansion**: `:heart:` → `<3` style aliases on send.
- **Keebdex backlight**: the driver exposes LED groups (set_leds_color in
  its API) — flash the keys on mention; investigate the driver's public
  surface first, don't poke i2c directly.

### Protocol & robustness
- **Persistent gossip cache**: CACHE_PATH exists in config ("off by
  default") — persist finalised messages + restore on launch so the feed
  survives relaunches; bound writes (flash wear) to e.g. one save per minute
  or on background.
- **Adaptive cadence**: slow advertise/scan when no peers seen for N min
  (battery); restore on first presence heard.
- **Rename propagation test**: freshest-nick-wins exists — add an explicit
  mesh test for rename while messages are in flight.

### Tooling
- **Screenshot harness in-repo**: adopt the session's shoot_chat.py as
  `tests/screenshots.py` + checked-in evidence PNGs (space_scanner pattern).
- **Sim keyboard injection**: teach the sim harness to emit Keyboard-group
  ButtonDownEvents so hexpansion typing is testable in the sim GUI.
- **On-badge smoke in-repo**: adopt the badge_repro script as
  `tests/badge_smoke.py` (construct + draw via mpremote, boot-order import).

## Definition of done, per item

Host tests green → sim screenshots clean (if UI) → on-badge construct+draw
via mpremote → deployed with reset → behavior confirmed on hardware →
version bump + release if user-visible.
