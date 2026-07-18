# RESUME — Chat app session state

Snapshot of the 2026-07-18 session so any session (or person) can pick it up
cold. FINAL STATE: launch crash fixed; keebdexpansion typing + emote keys
wired; UI rebuilt for the round screen; presets/backspace input fixes;
9/9 host suites incl. test_hexkb.py; deployed to the badge; **published** as
https://github.com/dfourn/tildagon-chat (public, `tildagon-app` topic,
release v1.0.0, category Apps — store indexing was still pending at session
end; if absent, check https://apps.badge.emfcamp.org/errors first).
Next work: see PROMPT.md (known issues + enhancement backlog).

## What this covers

`emf-new/chat/` — P2P BLE gossip chat for the EMF 2026 badge (Tildagon fw
v2.1.1, Spaceagon frontboard). Reported symptom: "crashes on launch".

## Root cause found and fixed

- **Launch crash was `ctx.text_align = "left"` (string) in `app.py` draw code.**
  The badge's real uctx binding (`drivers/gc9a01/mp_uctx.c`) coerces property
  writes with `mp_obj_get_int` → `TypeError: can't convert str to int` on the
  first draw frame. The sim fake (`sim/fakes/ctx.py`) and the test stubs accept
  strings, so tests + sim stayed green. Fixed by using `ctx.LEFT/RIGHT/CENTER`
  constants (all 7 sites); verified on-badge via `mpremote run` (construct +
  draw OK, fw v2.1.1, radio starts).
- The earlier fix recorded in `__init__.py` (ChatApp re-export) addressed a
  wrong theory: the launcher imports `apps.<dir>.app` directly, so the empty
  `__init__.py` never caused the crash. Docstring corrected.

## In flight (this session's active work)

User requirements added mid-session:
1. **Type with the keebdexpansion keyboard hexpansion** (nickname + messages).
2. **UI designed for the circular screen** (current layout anchors x=-116;
   at y=±104 only ±60px is visible → text cut off).

Findings for (1):
- Keebdexpansion (EEPROM: vendor 0xBAD3, product 0x4EEB, name "keebdex",
  port 4) carries `app.mpy`; the HexpansionManagerApp mounts + starts it.
- The driver polls keys over I2C (IRQ pin) and **emits standard firmware
  `ButtonDownEvent`/`ButtonUpEvent` with `events.keyboard.KEYBOARD_BUTTONS`**
  (group "Keyboard"; letters as single uppercase names; SPACE/BACKSPACE/ENTER;
  ENTER/ESCAPE/arrows have System-button parents). Same contract
  `app_components/dialog.py TextDialog` consumes — that's the reference.
- Driver binary saved at scratchpad `keeb_app.mpy` (strings inspected).
- Gotcha: ENTER/ESCAPE also reach the six-button poll path via their System
  parents (`Buttons.get` matches parent chains) — the app must suppress the
  duplicate CONFIRM/CANCEL edge when it consumes a keyboard key.

Plan (tasks): wire ButtonDownEvent handler into ChatApp (T9 stays as
fallback); redesign feed/setup/compose for the round display (centered, safe
widths = sqrt(120^2 - y^2)); upgrade badge_stubs (real Button semantics,
events.keyboard, system.eventbus) + host test for the typing flow; verify by
sim screenshots; deploy to `/apps/chat` and confirm typing on hardware.

## Verification ladder (use in this order)

1. `cd emf-new && bash chat/tests/run_all.sh`
2. Homebrew `micropython` + stubs (scratchpad `mpstubs/smoke_mp.py`)
3. Headless sim OS boot (scratchpad `boot_sim_launch_chat.py`; chat is
   symlinked into `badge-2024-software/sim/apps/chat`)
4. On-badge: `mpremote run <script>` — must `from system.scheduler import
   scheduler` FIRST (boot import order, else eventbus circular import), then
   import `apps.chat.app`, construct, `draw(display.get_ctx())`
5. Deploy: `mpremote fs cp <files> :/apps/chat/...` then `mpremote reset`
   (reset required — import cache)

## Hardware notes

- Badge on `/dev/cu.usbmodem101` (enumeration was flaky: charge-only
  cable/wrong port initially; check `ioreg -p IOUSB`).
- fw v2.1.1; frontboard class `frontboards.twentysix.TwentyTwentySix`;
  BLE floor passed (`radio_ok = True`).
- Also on badge: `danix` (terminal app, T9-only — no keebdex integration to
  reuse), twin_flame, infection, matrix_rain, space_scanner.
