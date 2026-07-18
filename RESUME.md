# RESUME — Chat app session state

Snapshot so any session (or person) can pick it up cold.

**CURRENT (this session, 2026-07-18 evening): debugging "messages don't pass
between 2 adjacent badges."** Prior session (launch crash + keebdex typing)
is in the section at the bottom — that work shipped and is deployed.

## Reported symptom

Two Tildagon badges sitting next to each other, both running Chat: a message
sent on one never appears on the other. Each badge only sees its own messages.

## Root cause found (this session)

**Version mismatch between badges.** Badge #1 had a **stale, different build**
of the chat app deployed (verified: `app.py` was 17732 bytes vs the current
19611 in the repo; the on-badge dir also had `README.md` + `tests/` that aren't
shipped runtime files). Two badges running mismatched codecs silently drop
each other's BLE adverts → exactly "messages don't show up between badges."

The current source code is **correct** — it is NOT a one-way-radio bug:

- `radio.py` `BLESync.start()` sets up **both** TX and RX:
  - `self._ble.gap_advertise(..., connectable=False)` — broadcast our advert
  - `self._ble.gap_scan(0, SCAN_INTERVAL_US, SCAN_WINDOW_US, True)` — passive scan
- `radio.py` `_irq()` (IRQ `_IRQ_SCAN_RESULT = 5`) parses incoming adverts in
  both `chunk` and `presence` formats, copies them out of the transient IRQ
  buffer, and appends to `self._buffer`.
- `RadioBridge.update()` (called every frame from `app.py update()`) drains the
  buffer and ingests each beacon into the gossip engine.
- The framing (`codec.py`) round-trips cleanly and fits the 31-byte BLE legacy
  advert limit (2-byte AD prefix + 14-byte header + ≤15 text). Cross-app
  isolation by `MAGIC_MSG=b"CH"` / `MAGIC_PRESENCE=b"CP"` is strict.
- Firmware floor passes: badge #1 is on `v2.1.1`, `hw.fw_allows_ble("v2.1.1")`
  → `True`. BLE is allowed to start (no hard-hang risk).

## Done this session

1. Read the full radio stack: `radio.py`, `codec.py`, `config.py`, `hw.py`,
   `gossip.py`, `app.py`. Cross-referenced with `twin_flame/sync_ble.py` and
   `infection/sync_ble.py` (identical BLE pattern — proven working).
2. Ruled out the "old firmware / no radio" theory via on-badge probe:
   `ota.get_version()` → `'v2.1.1'`, `fw_allows_ble()` → `True`.
3. **Found the version mismatch** (file sizes didn't match repo source).
4. **Wiped + redeployed current app to badge #1** (verified on-badge:
   `app.py` = 19611 bytes, imports clean via launcher path). Badge #1 now has
   the fix.
5. Added **on-screen radio diagnostics** to `app.py` `_draw_status_bar()`:
   now shows two lines at the top — `<n> nr @<nick> ch<ch>` and
   `<sync> act=<0/1> st=<store> id=<bid>` — so the radio state is visible
   without USB.
6. Created `../deploy-chat.sh` (repo root): stages the clean app, wipes the
   old one, deploys, resets. Run once per badge.

## Pending (blocked on physical USB)

- **Badge #2 still has the old app version.** Must deploy the current version
  to it before interop will work. USB was extremely flaky all session — macOS
  repeatedly saw zero USB devices (`ioreg -p IOUSB -c IOUSBHostDevice` empty).
  The badge DID enumerate earlier (successful deploys at 22:42 and 22:54), so
  the hardware is capable; the failures were physical (power-only cable /
  badge screen off / hub).
- **To finish:** get a data-capable cable, badge screen ON, run
  `./deploy-chat.sh` against badge #2. Then both badges run identical code.
- **Verify:** launch Chat on both, watch the top status line — `nr` count
  should go 0→1 within ~5s (presence beacons), then send a message and confirm
  it appears on the other badge.

### Re-verification pass (2026-07-18 23:18)

Source-side checks re-run cold; everything still green, nothing to change:
- Host suite: `bash emf-new/chat/tests/run_all.sh` → **9/9 PASS**
  (codec, comms, fw_floor, gossip, hexkb, isolation, keyboard, mesh,
  server_models).
- `emf-new/chat/app.py` = **19611 bytes** (matches the version deployed to
  badge #1; confirms the repo is the source of truth).
- USB recheck: `ls /dev/cu.usb*` → none; `ioreg -p IOUSB -c IOUSBHostDevice`
  → 0 devices. Badge #2 deploy still physically blocked — needs a
  data-capable cable + badge screen on + replug.
- No code changes required: the only remaining step is the physical deploy.

## Key files (this session)

- `radio.py` — `BLESync` (lines 246-332), `RadioBridge.update()` (385-402).
  The TX+RX pump is at lines 269-272 (start) and 279-294 (IRQ RX).
- `codec.py` — chunk/presence encode+parse. `parse_chunk`/`parse_presence`
  walk AD structures; strict magic check gives cross-app isolation.
- `hw.py` — `fw_allows_ble()` (110-145) + `_ble_fw_safe()` (148-154) gate BLE.
- `gossip.py` — `GossipEngine`: ingest/store/relay. Pure state machine.
- `app.py` — `_draw_status_bar()` (369-389, now with diagnostics), the radio
  lifecycle in `__init__` (91-92) + `update` (193-199) + `_exit` (468-475).
- `../deploy-chat.sh` — the deploy script (run once per badge).

## Deploy command (when USB works)

```bash
cd /Users/dan/rockstar-dev/emf-spacegon && ./deploy-chat.sh
# or with explicit port:
./deploy-chat.sh /dev/cu.usbmodem101
```

One-liner to check + deploy in a single paste:
```bash
ls /dev/cu.usb* 2>/dev/null && cd /Users/dan/rockstar-dev/emf-spacegon && ./deploy-chat.sh || echo "STILL NO BADGE — check: screen on? data cable? direct port?"
```

## Hardware notes (this session)

- Badge #1: `/dev/cu.usbmodem101` when it enumerates. fw `v2.1.1`.
- USB enumeration was flaky all evening — `ioreg`/`system_profiler` often
  showed zero USB devices. Charge-only cable is the prime suspect.
- On-badge `/apps/` also has: `space_scanner`, `pong_test`, `danix`,
  `twin_flame`, `infection`, `matrix_rain`, and several others.

---

# PRIOR SESSION (already shipped) — launch crash + keebdex typing

FINAL STATE: launch crash fixed; keebdexpansion typing + emote keys wired; UI
rebuilt for the round screen; presets/backspace input fixes; 9/9 host suites
incl. test_hexkb.py; deployed to the badge; published as
https://github.com/dfourn/tildagon-chat (public, `tildagon-app` topic,
release v1.0.0, category Apps).

## Root cause (prior session)

Launch crash was `ctx.text_align = "left"` (string) in `app.py` draw code.
The badge's real uctx binding coerces property writes with `mp_obj_get_int` →
`TypeError`. Fixed by using `ctx.LEFT/RIGHT/CENTER` constants (all 7 sites).
The sim fake + test stubs accepted strings, so they stayed green off-badge.

## Verification ladder (use in this order)

1. `cd emf-new && bash chat/tests/run_all.sh`
2. Homebrew `micropython` + stubs
3. Headless sim OS boot (chat is symlinked into
   `badge-2024-software/sim/apps/chat`)
4. On-badge: `mpremote resume exec` after full boot (resume = no soft-reset,
   so booted framework modules stay live), then `import apps.chat.app`
5. Deploy: `mpremote cp -r <dir> :/apps/` then `mpremote reset` (reset required)