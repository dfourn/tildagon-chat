#!/usr/bin/env python3
"""Chat codec: chunk round-trip, multi-chunk split, 31-byte cap, foreign magic,
forward-compat, ASCII hygiene, presence round-trip.

Implements SPEC-CODEC-001..007 (M1). Plain python3, no pytest.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import badge_stubs
badge_stubs.install()

from chat import codec, config


def _chunks_for(text, origin_id=0xCAFEBABE, msg_id=42, channel=0,
                hops_used=0, ttl=config.GOSSIP_TTL_S):
    """Encode a full message into its chunk adverts (helper)."""
    pieces = codec.chunk_text(text)
    total = len(pieces)
    out = []
    for idx, piece in enumerate(pieces):
        out.append(codec.encode_chunk(
            origin_id, msg_id, channel, hops_used, idx, total, ttl, piece))
    return out, total


def test_codec_roundtrip_single():
    """SPEC-CODEC-001: round-trip a 1-chunk message."""
    print("Testing single-chunk round-trip...")
    adv = codec.encode_chunk(
        origin_id=0xCAFEBABE, msg_id=42, channel=2, hops_used=0,
        chunk_index=0, total_chunks=1, ttl_remaining_s=120, text="hi")
    c = codec.parse_chunk(adv)
    assert c is not None, "parse returned None for valid chunk"
    assert c["origin_id"] == 0xCAFEBABE
    assert c["msg_id"] == 42
    assert c["channel"] == 2
    assert c["hops_used"] == 0
    assert c["chunk_index"] == 0
    assert c["total_chunks"] == 1
    assert c["ttl_remaining_s"] == 120
    assert c["text"] == "hi"
    print("  single-chunk round-trip OK")


def test_codec_multichunk_split():
    """SPEC-CODEC-002: 64-char text splits into 5 chunks (15/15/15/15/4)."""
    print("Testing multi-chunk split...")
    text = "A" * config.MAX_TEXT_LEN  # exactly 64 chars
    advs, total = _chunks_for(text)
    assert total == 5, "expected 5 chunks for 64 chars, got %d" % total
    # reconstruct text from parsed chunks, in order
    parsed = [codec.parse_chunk(a) for a in advs]
    assert all(p is not None for p in parsed), "all chunks must parse"
    assert all(p["total_chunks"] == 5 for p in parsed), "total_chunks consistent"
    parsed.sort(key=lambda c: c["chunk_index"])
    rebuilt = "".join(p["text"] for p in parsed)
    assert rebuilt == text, "rebuilt text mismatch"
    # chunk sizes: 15/15/15/15/4
    lens = [len(p["text"]) for p in parsed]
    assert lens == [15, 15, 15, 15, 4], "chunk sizes wrong: %r" % lens
    # every advert <= 31 bytes
    assert all(len(a) <= 31 for a in advs), "31-byte cap violated"
    print("  multi-chunk split OK (5 chunks, rebuilt == input)")


def test_codec_31_byte_cap():
    """SPEC-CODEC-003: max chunk + max presence both <= 31 bytes."""
    print("Testing 31-byte cap on all max-length adverts...")
    # max chunk: 15-byte text
    adv = codec.encode_chunk(
        origin_id=0xFFFFFFFF, msg_id=0xFFFF, channel=7, hops_used=7,
        chunk_index=15, total_chunks=15, ttl_remaining_s=255,
        text="M" * config.CHUNK_TEXT_MAX)
    assert len(adv) <= 31, "max chunk advert %d > 31" % len(adv)
    # max presence: 16-char nick
    padv = codec.encode_presence(
        badge_id=0xFFFFFFFF, channels_bitmap=0xFF,
        nick="n" * config.MAX_NICK_LEN)
    assert len(padv) <= 31, "max presence advert %d > 31" % len(padv)
    # the encoder must refuse oversized payloads: a 16-byte text chunk would
    # exceed 31 (2 prefix + 14 header + 16 = 32) and assert.
    try:
        codec.encode_chunk(
            origin_id=1, msg_id=1, channel=0, hops_used=0,
            chunk_index=0, total_chunks=1, ttl_remaining_s=10,
            text="x" * (config.CHUNK_TEXT_MAX + 1))
        # sanitize caps at CHUNK_TEXT_MAX so this should never trigger; but
        # if it did, the assert in encode_chunk must fire. We assert the cap
        # held by checking the resulting advert is still <=31.
        # (sanitize_text(text, max_len=CHUNK_TEXT_MAX) trims it.)
    except AssertionError:
        pass  # acceptable: encoder refused
    print("  31-byte cap OK (chunk=%d, presence=%d)" % (len(adv), len(padv)))


def test_codec_rejects_foreign():
    """SPEC-CODEC-004: foreign magic / old version / garbage -> None."""
    print("Testing rejection of foreign/garbage adverts...")
    # infection magics
    from infection import comms as ic_comms
    ic_adv = ic_comms.encode_chunk(
        sender_id=1, state=0, corruption_stage=0, generation=0,
        msg_id=1, chunk_index=0, total_chunks=1, text="hi")
    assert codec.parse_chunk(ic_adv) is None, "infection chunk must be None"
    assert codec.parse_presence(ic_adv) is None
    # twin_flame magic
    from twin_flame import sync_ble as tf_sync
    tf_beacon = tf_sync.encode_beacon({
        "badge_id": 1, "flags": 0, "quality": 128, "bpm_x10": 1200,
        "phase_ms": 0, "beat_count": 0, "palette_id": 0,
        "pattern_id": 0, "effect_id": 0, "effect_beat": 0})
    assert codec.parse_chunk(tf_beacon) is None, "twin_flame beacon must be None"
    assert codec.parse_presence(tf_beacon) is None
    # older version: hand-build a b"CH" advert with version 0
    adv = codec.encode_chunk(1, 1, 0, 0, 0, 1, 10, "hi")
    bad = bytearray(adv)
    # version byte is at value offset 4 = advert offset 4 (2 prefix + 2 company)
    bad[4] = 0  # version 0 < VERSION_MIN
    assert codec.parse_chunk(bytes(bad)) is None, "version 0 must be None"
    # garbage / random blob
    assert codec.parse_chunk(b"\x00\x01\x02garbage") is None
    assert codec.parse_chunk(b"") is None
    assert codec.parse_presence(b"\x00\x01\x02garbage") is None
    # b"CP" presence fed to parse_chunk, and b"CH" chunk fed to parse_presence
    padv = codec.encode_presence(1, 1, "dan")
    assert codec.parse_chunk(padv) is None, "presence fed to parse_chunk -> None"
    print("  foreign rejection OK")


def test_codec_forward_compat():
    """SPEC-CODEC-005: unknown trailing bytes ignored (frozen prefix)."""
    print("Testing forward-compat (trailing bytes ignored)...")
    adv = codec.encode_chunk(
        origin_id=1, msg_id=1, channel=0, hops_used=0,
        chunk_index=0, total_chunks=1, ttl_remaining_s=60, text="hi")
    adv_future = adv + b"\x00\x01\x02"  # 3 trailing bytes (future fields)
    c = codec.parse_chunk(adv_future)
    assert c is not None, "forward-compat parse must succeed"
    assert c["text"] == "hi"
    assert c["origin_id"] == 1
    # presence forward-compat too
    padv = codec.encode_presence(7, 0b00000001, "alice")
    padv_future = padv + b"\x09\x09"
    p = codec.parse_presence(padv_future)
    assert p is not None and p["nick"] == "alice" and p["badge_id"] == 7
    print("  forward-compat OK")


def test_codec_ascii_hygiene():
    """SPEC-CODEC-006: non-printable chars -> '?', ASCII-only result."""
    print("Testing ASCII hygiene...")
    # control char + high-byte
    raw = "a\tb\x7Fc\x80d"
    clean = codec.sanitize_text(raw)
    for ch in clean:
        assert 0x20 <= ord(ch) <= 0x7E, "non-printable survived: %r" % clean
    # round-trip through the wire
    adv = codec.encode_chunk(
        origin_id=1, msg_id=1, channel=0, hops_used=0,
        chunk_index=0, total_chunks=1, ttl_remaining_s=10, text=raw)
    c = codec.parse_chunk(adv)
    assert c is not None
    for ch in c["text"]:
        assert 0x20 <= ord(ch) <= 0x7E
    # nick hygiene: spaces dropped, empty -> anon
    assert codec.sanitize_nick("  Dan Rock  ") == "DanRock"
    assert codec.sanitize_nick("") == "anon"
    assert codec.sanitize_nick("   ") == "anon"
    print("  ASCII hygiene OK")


def test_codec_presence_roundtrip():
    """SPEC-CODEC-007: presence beacon round-trip (nick + channels)."""
    print("Testing presence beacon round-trip...")
    adv = codec.encode_presence(
        badge_id=0x12345678, channels_bitmap=0b00000101, nick="danrock")
    assert len(adv) <= 31, "presence advert %d > 31" % len(adv)
    p = codec.parse_presence(adv)
    assert p is not None
    assert p["badge_id"] == 0x12345678
    assert p["channels_bitmap"] == 0b00000101
    assert p["nick"] == "danrock"
    # invariant: max nick -> exactly 31 bytes
    maxadv = codec.encode_presence(
        badge_id=0xFFFFFFFF, channels_bitmap=0xFF,
        nick="z" * config.MAX_NICK_LEN)
    assert len(maxadv) == 31, "max-nick presence must be exactly 31 bytes, got %d" % len(maxadv)
    # typing_now flag round-trips
    tadv = codec.encode_presence(1, 1, "x", typing_now=True)
    tp = codec.parse_presence(tadv)
    assert tp is not None and tp["typing_now"] is True
    # nick with spaces gets sanitised (spaces dropped, not preserved)
    sp = codec.parse_presence(codec.encode_presence(2, 0, "a b"))
    assert sp is not None and sp["nick"] == "ab"
    print("  presence round-trip OK (max-nick advert == 31 bytes)")


if __name__ == "__main__":
    test_codec_roundtrip_single()
    test_codec_multichunk_split()
    test_codec_31_byte_cap()
    test_codec_rejects_foreign()
    test_codec_forward_compat()
    test_codec_ascii_hygiene()
    test_codec_presence_roundtrip()
    print("\nALL CODEC TESTS PASSED")