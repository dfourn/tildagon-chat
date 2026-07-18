"""Chat app tunables (badge side, P2P 2026).

All tunables live here, twin_flame/infection style. Times are int milliseconds
unless the name says otherwise. Radio cadences are M4-spike defaults and may be
retuned after the on-badge gap_advertise re-issue probe (plan chat-2026.md
section 15) without regressing the mesh propagation tests, which assert the
propagation bound, not a magic tick count.
"""

# --- protocol ---------------------------------------------------------------
MAGIC_MSG = b"CH"            # chat message chunk advert
MAGIC_PRESENCE = b"CP"       # chat presence beacon advert
COMPANY = 0xFFFF             # reserved internal/testing company id (hobby badge)
VERSION = 1
VERSION_MIN = 1              # version FLOOR: accept newer, read known prefix

# --- radio cadence (M4-spike defaults) --------------------------------------
ADV_INTERVAL_US = 150_000    # validated on-badge by EMFight alongside WiFi
SCAN_INTERVAL_US = 30_000
SCAN_WINDOW_US = 30_000
TX_PRESENCE_SLOT_MS = 600    # share of the radio for presence (the directory)
TX_GOSSIP_SLOT_MS = 400      # share for one cached-message chunk
TX_OWN_BURST_MS = 9000       # post-send burst window for your own message
RATE_LIMIT_MS = 20000        # min gap between YOUR sends (not relays)

# --- gossip -----------------------------------------------------------------
GOSSIP_TTL_HOPS = 4          # re-broadcast at most this many relay hops
GOSSIP_TTL_S = 120           # ...and at most this many seconds since origin ts
GOSSIP_STORE_MAX = 256       # bounded message cache (LRU by origin ts)

# --- wire limits ------------------------------------------------------------
# advert = 2-byte [len][0xFF] AD prefix + value; value = header + text/nick.
MAX_TEXT_LEN = 64            # tweet-short; fits the medium (was 140 in server era)
MAX_NICK_LEN = 16            # fits presence value: 11 + 16 + 2 reserved = 29
CHUNK_TEXT_MAX = 15          # 31 - 2 (AD prefix) - 14 (value header)

# --- identity / peers -------------------------------------------------------
PEER_MAX_AGE_MS = 5000
RSSI_EMA = 0.25
RSSI_NEAR_DBM = -40
RSSI_FAR_DBM = -90

# --- channels ---------------------------------------------------------------
NUM_CHANNELS = 8

# --- LED (no-strobe) --------------------------------------------------------
LED_PULSE_MS = 400
LED_NOTIFY_COALESCE_MS = 2000
LED_NEW_PEER_COALESCE_MS = 10000
LED_FRAME_MS = 80
LED_NOTIFY_COLOR = (36, 160, 120)  # 0-255 mint (dimmed COL_ACCENT); notify blink

# --- persistence ------------------------------------------------------------
NICK_PATH = "/flash/chat_nick.txt"       # remembered across launches
CHANNEL_PATH = "/flash/chat_channel.txt"  # last channel tag
CACHE_PATH = "/flash/chat_cache.json"     # optional gossip cache (off by default)

# --- input ------------------------------------------------------------------
SUBMIT_HOLD_MS = 600         # CONFIRM hold threshold to send; CANCEL hold to clear

# --- quick-send presets -----------------------------------------------------
PRESETS = ["hi :)", "lol", "cheers!", "woot", "<3", "..."]

# --- rendering --------------------------------------------------------------
DISPLAY_MESSAGE_COUNT = 20   # how many messages we keep on-screen
FONT_SIZE_MSG = 11
FONT_SIZE_META = 9
FONT_SIZE_TITLE = 14
INPUT_FONT_SIZE = 13
SCROLL_LINES = 7             # feed rows that fit the round message area

# --- colours (rgb 0..1) -----------------------------------------------------
COL_BG = (0.04, 0.05, 0.07)
COL_TEXT = (0.9, 0.92, 0.95)
COL_MUTED = (0.45, 0.55, 0.68)
COL_ACCENT = (0.42, 0.94, 0.77)
COL_MINE = (0.48, 0.64, 1.0)
COL_OTHER = (0.70, 0.80, 0.95)