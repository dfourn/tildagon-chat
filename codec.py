"""Chat wire codec: message chunks + presence beacons over BLE adverts.

Pure module (no bluetooth import) so every encode/parse is host-testable on
plain CPython. Mirrors infection/comms.py's AD-structure framing and the
twin_flame forward-compat contract (frozen prefix, append-only, parsers ignore
trailing bytes).

Two advert types, both Manufacturer Specific Data (AD type 0xFF, company
0xFFFF), both assert len(adv) <= 31 (the BLE legacy-advert hard limit):

* Message chunk   -- MAGIC b"CH"  (14-byte value header + <=15 text bytes)
* Presence beacon -- MAGIC b"CP"  (11-byte value header + 16 nick + 2 reserved)

Byte math (plan chat-2026.md section 3):
    advert = 2-byte [len][0xFF] AD prefix + value
    chunk value    = 14-byte header + <=15 text  = <=29  -> advert <= 31
    presence value = 11-byte header + 16 nick + 2 reserved = 29 -> advert = 31

Cross-app isolation: chat magic b"CH"/b"CP" is checked strictly, so chat adverts
parse to None in infection (b"IC"/b"IN") and twin_flame (b"TF") codecs, and vice
versa. test_isolation.py proves both directions.
"""

from . import config

_AD_TYPE_MFG = 0xFF

# --- message chunk value offsets (after [len][0xFF]) ------------------------
_OFF_ORIGIN = 5
_OFF_MSG_ID = 9
_OFF_FLAGS = 11
_OFF_SEQ = 12
_OFF_TTL = 13
_OFF_TEXT = 14
_HEADER_LEN = 14

# --- presence beacon value offsets (after [len][0xFF]) ----------------------
_OFF_P_BADGE_ID = 5
_OFF_P_CHANNELS = 9
_OFF_P_FLAGS = 10
_OFF_P_NICK = 11
_P_HEADER_LEN = 11
_NICK_FIELD_LEN = 16
_RESERVED_LEN = 2

FLAG_MENTION = 0x40
FLAG_TYPING_NOW = 0x01


def sanitize_text(text, max_len=None):
    """Cap length and force printable ASCII (non-printable -> '?').

    No whitespace stripping here -- compose-time concern, not the codec's job.
    """
    max_len = config.MAX_TEXT_LEN if max_len is None else max_len
    out = []
    for ch in (text or "")[:max_len]:
        o = ord(ch)
        out.append(ch if 0x20 <= o <= 0x7E else "?")
    return "".join(out)


def sanitize_nick(nick, max_len=None):
    """ASCII only, no spaces, fallback to 'anon' when empty."""
    max_len = config.MAX_NICK_LEN if max_len is None else max_len
    out = []
    for ch in (nick or "")[:max_len]:
        o = ord(ch)
        if 0x20 < o <= 0x7E:
            out.append(ch)
    cleaned = "".join(out)
    return cleaned or "anon"


def chunk_text(text):
    """Split sanitized text into <=CHUNK_TEXT_MAX-char pieces."""
    text = sanitize_text(text)
    step = config.CHUNK_TEXT_MAX
    pieces = [text[i:i + step] for i in range(0, len(text), step)]
    return pieces if pieces else [""]


def _pack_flags(channel, hops_used, mention=False):
    flags = (channel & 0x07) | ((hops_used & 0x07) << 3)
    if mention:
        flags |= FLAG_MENTION
    return flags & 0xFF


def encode_chunk(origin_id, msg_id, channel, hops_used, chunk_index,
                 total_chunks, ttl_remaining_s, text, mention=False):
    """Build one chat-chunk advert. text: str <= 15 ASCII chars.

    Returns raw advertising payload bytes; asserts it fits 31 bytes.
    """
    company_lo = config.COMPANY & 0xFF
    company_hi = (config.COMPANY >> 8) & 0xFF
    flags = _pack_flags(channel, hops_used, mention)
    seq = ((chunk_index & 0x0F) << 4) | (total_chunks & 0x0F)
    clean = sanitize_text(text, max_len=config.CHUNK_TEXT_MAX)
    value = (
        bytes((company_lo, company_hi))
        + config.MAGIC_MSG
        + bytes((config.VERSION & 0xFF,))
        + (origin_id & 0xFFFFFFFF).to_bytes(4, "little")
        + (msg_id & 0xFFFF).to_bytes(2, "little")
        + bytes((flags, seq, ttl_remaining_s & 0xFF))
        + clean.encode()
    )
    length = 1 + len(value)
    adv = bytes((length, _AD_TYPE_MFG)) + value
    assert len(adv) <= 31, "chat chunk exceeds 31-byte BLE legacy limit"
    return adv


def parse_chunk(adv):
    """Walk AD structures; return a chat-chunk dict or None."""
    i = 0
    n = len(adv)
    while i + 1 < n:
        ln = adv[i]
        if ln == 0:
            break
        typ = adv[i + 1]
        val = adv[i + 2:i + 1 + ln]
        if typ == _AD_TYPE_MFG and len(val) >= _HEADER_LEN:
            company = val[0] | (val[1] << 8)
            if (company == config.COMPANY and
                    val[2:4] == config.MAGIC_MSG and
                    val[4] >= config.VERSION_MIN):
                flags = val[_OFF_FLAGS]
                seq = val[_OFF_SEQ]
                try:
                    text = bytes(val[_OFF_TEXT:]).decode()
                except (UnicodeError, ValueError):
                    return None
                for ch in text:
                    if not 0x20 <= ord(ch) <= 0x7E:
                        return None
                return {
                    "origin_id": int.from_bytes(
                        val[_OFF_ORIGIN:_OFF_ORIGIN + 4], "little"),
                    "msg_id": int.from_bytes(
                        val[_OFF_MSG_ID:_OFF_MSG_ID + 2], "little"),
                    "channel": flags & 0x07,
                    "hops_used": (flags >> 3) & 0x07,
                    "mention": bool(flags & FLAG_MENTION),
                    "chunk_index": (seq >> 4) & 0x0F,
                    "total_chunks": seq & 0x0F,
                    "ttl_remaining_s": val[_OFF_TTL],
                    "text": text,
                }
        i += ln + 1
    return None


def encode_presence(badge_id, channels_bitmap, nick, typing_now=False):
    """Build the presence beacon advert.

    Nick sanitised + space-padded to 16 bytes; 2 reserved trailing bytes keep
    the advert a constant 31 bytes.
    """
    company_lo = config.COMPANY & 0xFF
    company_hi = (config.COMPANY >> 8) & 0xFF
    nick_clean = sanitize_nick(nick)
    nick_bytes = nick_clean.encode()[:config.MAX_NICK_LEN]
    nick_field = nick_bytes + b" " * (config.MAX_NICK_LEN - len(nick_bytes))
    flags = FLAG_TYPING_NOW if typing_now else 0
    value = (
        bytes((company_lo, company_hi))
        + config.MAGIC_PRESENCE
        + bytes((config.VERSION & 0xFF,))
        + (badge_id & 0xFFFFFFFF).to_bytes(4, "little")
        + bytes((channels_bitmap & 0xFF, flags & 0xFF))
        + nick_field
        + b"\x00" * _RESERVED_LEN
    )
    length = 1 + len(value)
    adv = bytes((length, _AD_TYPE_MFG)) + value
    assert len(adv) <= 31, "chat presence exceeds 31-byte BLE legacy limit"
    return adv


def parse_presence(adv):
    """Walk AD structures; return a presence-beacon dict or None."""
    i = 0
    n = len(adv)
    while i + 1 < n:
        ln = adv[i]
        if ln == 0:
            break
        typ = adv[i + 1]
        val = adv[i + 2:i + 1 + ln]
        if typ == _AD_TYPE_MFG and len(val) >= _P_HEADER_LEN:
            company = val[0] | (val[1] << 8)
            if (company == config.COMPANY and
                    val[2:4] == config.MAGIC_PRESENCE and
                    val[4] >= config.VERSION_MIN):
                nick_raw = bytes(
                    val[_OFF_P_NICK:_OFF_P_NICK + _NICK_FIELD_LEN])
                try:
                    nick = nick_raw.decode("ascii").rstrip(" ")
                except (UnicodeError, ValueError):
                    return None
                if not nick:
                    nick = "anon"
                return {
                    "badge_id": int.from_bytes(
                        val[_OFF_P_BADGE_ID:_OFF_P_BADGE_ID + 4], "little"),
                    "channels_bitmap": val[_OFF_P_CHANNELS],
                    "typing_now": bool(val[_OFF_P_FLAGS] & FLAG_TYPING_NOW),
                    "nick": nick,
                }
        i += ln + 1
    return None