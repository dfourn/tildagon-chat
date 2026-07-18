# Chat

Serverless P2P chat for EMF Tildagon badges. No WiFi, no server, no pairing:
messages gossip badge-to-badge over connectionless BLE adverts and relay
through the mesh (up to 4 hops, 120 s TTL), so a message hops across the field
via whoever is in range. Out of range just means you hear fewer messages —
never a crash.

## Features

- **P2P gossip mesh** — chunked messages in 31-byte BLE adverts, epidemic
  relay with hop + time TTLs, bounded store, loop-safe.
- **Keyboard hexpansion support** — type on the keebdexpansion: letters,
  SHIFT for caps, BACKSPACE, ENTER to send, ESC to back out. The icon keys
  insert emotes: circle `:)`, cross `:(`, triangle `:D`, square `:|`,
  cloud `:o`, diamond `<3`. On the feed, just start typing to compose.
- **Six-button T9 fallback** — full text entry with no hexpansion.
- **Channels 0-7** — cheap proximity rooms; UP/DOWN on the feed switches.
- **Quick-send presets** — LEFT/RIGHT to pick, hold CONFIRM to send.
- **Presence** — nearby badge count + nicknames from presence beacons.
- **Round-screen UI** — laid out for the circular 240 px display.
- **Rate limiting** — own sends gated (20 s); relays always flow.

Needs firmware **v2.0.0-alpha.3 or newer** for BLE (older firmware shows a
"no radio" banner and the app still works as a local scratchpad).

## Controls

| Screen | Input | Action |
|---|---|---|
| **SETUP** | keyboard / T9 | type a nickname |
| | ENTER / hold CONFIRM | save & continue |
| | BACKSPACE / CANCEL tap | delete char |
| | hold CANCEL | clear |
| **FEED** | any letter key | start composing |
| | ENTER / CONFIRM tap | open compose |
| | hold CONFIRM | send shown preset |
| | LEFT/RIGHT | cycle presets |
| | UP/DOWN | switch channel |
| | ESC / CANCEL | exit app |
| **COMPOSE** | keyboard / T9 | type message |
| | ENTER / hold CONFIRM | send |
| | BACKSPACE / CANCEL tap | delete char |
| | ESC / hold CANCEL | back to feed |

T9: UP/DOWN pick a letter cluster, LEFT/RIGHT pick the letter, CONFIRM tap
commits it.

## Install

From the Tildagon **App store** on the badge (category: Apps), or manually:

```bash
mpremote fs cp -r chat :/apps/chat
mpremote reset   # required: MicroPython caches imports
```

## Development

Pure-logic modules (codec, gossip engine, keyboard, radio bridge) are
host-testable on plain CPython with no firmware present:

```bash
bash tests/run_all.sh
```

Layout: `codec.py` (wire format), `gossip.py` (store + relay schedule),
`radio.py` (BLE / sim transports + bridge), `keyboard.py` (T9 + hexpansion
key translation), `hw.py` (guarded hardware imports), `app.py` (screens).

## License

MIT — see LICENSE.
