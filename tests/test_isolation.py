#!/usr/bin/env python3
"""Cross-app isolation: chat adverts invisible to infection/twin_flame codecs
and vice versa.

SPEC-ISOL-001..002 (M1). Required so chat may run in the same RF field as
Infection/Twin Flame. The isolation holds because every codec checks its own
MAGIC strictly -- no shared registry, no ambiguity.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import badge_stubs
badge_stubs.install()

from chat import codec
from infection import comms as ic_comms
from twin_flame import sync_ble as tf_sync


def test_isolation_chat_out():
    """SPEC-ISOL-001: chat b"CH"/b"CP" -> None in infection + twin_flame codecs."""
    print("Testing chat adverts invisible to siblings...")
    chunk_adv = codec.encode_chunk(
        origin_id=1, msg_id=1, channel=0, hops_used=0,
        chunk_index=0, total_chunks=1, ttl_remaining_s=60, text="hi")
    presence_adv = codec.encode_presence(
        badge_id=1, channels_bitmap=0x01, nick="dan")
    # infection comms parses b"IC" chunks; chat b"CH" must be None
    assert ic_comms.parse_chunk(chunk_adv) is None, \
        "infection must not see chat chunk"
    # twin_flame parses b"TF" beacons; both chat adverts must be None
    assert tf_sync.parse_beacon(chunk_adv) is None, \
        "twin_flame must not see chat chunk"
    assert tf_sync.parse_beacon(presence_adv) is None, \
        "twin_flame must not see chat presence"
    print("  chat-out isolation OK")


def test_isolation_foreign_in():
    """SPEC-ISOL-002: infection b"IC" + twin_flame b"TF" -> None in chat codec."""
    print("Testing sibling adverts invisible to chat...")
    ic_adv = ic_comms.encode_chunk(
        sender_id=1, state=0, corruption_stage=0, generation=0,
        msg_id=1, chunk_index=0, total_chunks=1, text="yo")
    tf_beacon = tf_sync.encode_beacon({
        "badge_id": 1, "flags": 0, "quality": 128, "bpm_x10": 1200,
        "phase_ms": 0, "beat_count": 0, "palette_id": 0,
        "pattern_id": 0, "effect_id": 0, "effect_beat": 0})
    assert codec.parse_chunk(ic_adv) is None, \
        "chat must not see infection chunk"
    assert codec.parse_presence(ic_adv) is None
    assert codec.parse_chunk(tf_beacon) is None, \
        "chat must not see twin_flame beacon"
    assert codec.parse_presence(tf_beacon) is None
    # infection's game-state beacon magic is b"IN"; build one to be thorough.
    # (infection's comms only encodes b"IC"; the game-state beacon is a
    # different module. We rely on the strict-MAGIC property instead: any
    # b"IN"-tagged MSD would also be None. Spot-check with a hand-built blob.)
    in_blob = bytes((10, 0xFF)) + b"\xFF\xFF" + b"IN" + bytes((1,)) + b"\x00" * 8
    assert codec.parse_chunk(in_blob) is None
    assert codec.parse_presence(in_blob) is None
    print("  foreign-in isolation OK")


if __name__ == "__main__":
    test_isolation_chat_out()
    test_isolation_foreign_in()
    print("\nALL ISOLATION TESTS PASSED")