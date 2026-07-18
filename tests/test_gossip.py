#!/usr/bin/env python3
"""Gossip engine: echo, finalisation, relay selection, TTL, eviction.

Implements SPEC-GOSSIP-001..007 (M2). Plain python3, no pytest.

The GossipEngine is a pure state machine (no radio imports): parsed chunk dicts
go in via ingest_chunk, advert bytes come out via send/tick_tx, all against an
injected clock (``now`` in ms). Relay selection is fresh-and-rare-first with a
seeded RNG tiebreak (deterministic in tests).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import badge_stubs
badge_stubs.install()

from chat import codec, config, gossip

B1 = 0xCAFEBABE   # self
B2 = 0x12345678   # a peer


def _mk_chunk(origin_id, msg_id, channel=0, hops=0, total=1, idx=0,
              ttl=config.GOSSIP_TTL_S, text="hi"):
    """Build + parse a single chunk dict (the shape ingest_chunk expects)."""
    adv = codec.encode_chunk(origin_id, msg_id, channel, hops, idx, total,
                             ttl, text)
    return codec.parse_chunk(adv)


def _gossip_now(offset=50):
    """A ``now`` value that lands in the gossip relay slot (phase >= PRESENCE).

    Presence slot occupies [0, TX_PRESENCE_SLOT_MS) of each
    (PRESENCE+GOSSIP) cycle; the gossip relay slot is the rest.
    """
    return config.TX_PRESENCE_SLOT_MS + offset


def test_gossip_echo_own():
    """SPEC-GOSSIP-001: send() seeds the store for immediate local echo."""
    print("Testing own-message echo on send()...")
    eng = gossip.GossipEngine(self_id=B1, nick="dan")
    out = eng.send("hello", channel=0, now=1000)
    msgs = eng.messages()
    assert len(msgs) == 1, "expected 1 finalised message, got %d" % len(msgs)
    m = msgs[0]
    assert m.text == "hello", "text mismatch: %r" % m.text
    assert m.origin_id == B1, "origin_id should be self"
    # send() returns the chunk adverts to broadcast
    assert isinstance(out, list) and len(out) >= 1, \
        "send must return a list of chunk adverts"
    parsed = [codec.parse_chunk(a) for a in out]
    assert all(p is not None for p in parsed), "returned adverts must parse"
    assert "".join(p["text"] for p in parsed) == "hello", "echo text mismatch"
    assert all(p["origin_id"] == B1 for p in parsed)
    print("  echo OK")


def test_gossip_finalise_once():
    """SPEC-GOSSIP-002: ingesting a full set finalises a message exactly once."""
    print("Testing single finalisation on ingest...")
    eng = gossip.GossipEngine(self_id=B1, nick="dan")
    pieces = ["AAAA", "BBBB", "CCCC", "DDDD"]
    results = []
    for idx, piece in enumerate(pieces):
        c = _mk_chunk(B2, msg_id=7, total=4, idx=idx, text=piece)
        results.append(eng.ingest_chunk(c, now=1000 + idx * 100))
    assert results == [False, False, False, True], \
        "finalise flags wrong: %r" % results
    msgs = eng.messages()
    assert len(msgs) == 1, "exactly one finalised message, got %d" % len(msgs)
    assert msgs[0].text == "AAAABBBBCCCCDDDD", "assembled text mismatch"
    print("  finalise-once OK")


def test_gossip_out_of_order():
    """SPEC-GOSSIP-003: out-of-order chunks still finalise once."""
    print("Testing out-of-order reassembly...")
    eng = gossip.GossipEngine(self_id=B1, nick="dan")
    pieces = ["AAAA", "BBBB", "CCCC", "DDDD"]
    order = [2, 0, 3, 1]
    finalised = 0
    for idx in order:
        c = _mk_chunk(B2, msg_id=9, total=4, idx=idx, text=pieces[idx])
        if eng.ingest_chunk(c, now=2000):
            finalised += 1
    assert finalised == 1, "finalised %d times (expected 1)" % finalised
    assert eng.messages()[0].text == "AAAABBBBCCCCDDDD"
    print("  out-of-order OK")


def test_gossip_relay_selection():
    """SPEC-GOSSIP-004: relay prefers fresh, then rare."""
    print("Testing relay selection (fresh-and-rare-first)...")
    gnow = _gossip_now()

    # --- freshness beats staleness (both relayed 0) ---
    eng = gossip.GossipEngine(self_id=B1, nick="dan", seed=42)
    eng.ingest_chunk(_mk_chunk(B2, msg_id=1, text="fresh"),
                     now=gnow - 1000)      # age 1s
    eng.ingest_chunk(_mk_chunk(B2, msg_id=2, text="stale"),
                     now=gnow - 60000)     # age 60s
    out = eng.tick_tx(gnow)
    assert out is not None, "expected a relay chunk in the gossip slot"
    p = codec.parse_chunk(out)
    assert p is not None and p["text"] == "fresh", \
        "fresh (age 1s) must beat stale (age 60s): got %r" % \
        (p["text"] if p else None)

    # --- rarity beats commonality (equally fresh) ---
    eng2 = gossip.GossipEngine(self_id=B1, nick="dan", seed=42)
    eng2.ingest_chunk(_mk_chunk(B2, msg_id=10, text="common"),
                      now=gnow - 1000)
    eng2.ingest_chunk(_mk_chunk(B2, msg_id=11, text="rare"),
                      now=gnow - 1000)
    # Simulate "common" already having been relayed 3x by this badge.
    # (Host test reaches into the store to set the rarity counter directly;
    #  production code only mutates this via the relay path.)
    eng2._store[(B2, 10)].times_relayed_by_me = 3
    out2 = eng2.tick_tx(gnow)
    p2 = codec.parse_chunk(out2)
    assert p2 is not None and p2["text"] == "rare", \
        "rare (relayed 0) must beat common (relayed 3): got %r" % \
        (p2["text"] if p2 else None)
    print("  relay selection OK")


def test_gossip_ttl_decrement():
    """SPEC-GOSSIP-005: relay decrements hops+TTL; drops at the bounds."""
    print("Testing relay TTL/hops decrement + drop-at-zero...")
    # --- decrement: hops 3->4, ttl 10 -> 6 over 4s ---
    eng = gossip.GossipEngine(self_id=B1, nick="dan", seed=1)
    t0 = _gossip_now(10)  # ingest time (gossip slot, but ingest is slot-agnostic)
    eng.ingest_chunk(_mk_chunk(B2, msg_id=5, hops=3, ttl=10, text="hey"),
                     now=t0)
    out = eng.tick_tx(t0 + 4000)  # 4s later; still in gossip slot
    assert out is not None, "expected a relay chunk"
    p = codec.parse_chunk(out)
    assert p is not None
    assert p["hops_used"] == 4, "hops should increment 3->4, got %r" % p["hops_used"]
    assert p["ttl_remaining_s"] == 6, \
        "ttl should decrement 10->6 over 4s, got %r" % p["ttl_remaining_s"]

    # --- drop: at hops cap (no relay) ---
    eng_h = gossip.GossipEngine(self_id=B1, nick="dan", seed=1)
    eng_h.ingest_chunk(
        _mk_chunk(B2, msg_id=6, hops=config.GOSSIP_TTL_HOPS, text="capped"),
        now=_gossip_now())
    assert eng_h.tick_tx(_gossip_now()) is None, \
        "hops-capped message must not be relayed"

    # --- drop: ttl exhausted (no relay) ---
    eng_t = gossip.GossipEngine(self_id=B1, nick="dan", seed=1)
    eng_t.ingest_chunk(_mk_chunk(B2, msg_id=7, ttl=1, text="expd"),
                       now=_gossip_now(10))
    # pump forward until the recomputed ttl hits 0; stay in the gossip slot
    far = _gossip_now(10) + 130 * 1000
    assert eng_t.tick_tx(far) is None, "expired message must not be relayed"
    print("  ttl/hops decrement + drop OK")


def test_gossip_store_cap():
    """SPEC-GOSSIP-006: store caps at GOSSIP_STORE_MAX (LRU by origin_ts)."""
    print("Testing store cap (LRU by origin_ts)...")
    eng = gossip.GossipEngine(self_id=B1, nick="dan", store_max=3)
    base = 1000
    for i in range(3):
        eng.ingest_chunk(_mk_chunk(B2, msg_id=i, text="m%d" % i),
                         now=base + i * 100)
    assert len(eng.messages()) == 3, "store should be at capacity"
    # finalise one more -> oldest (msg_id=0, smallest origin_ts) evicted
    eng.ingest_chunk(_mk_chunk(B2, msg_id=3, text="m3"),
                     now=base + 3 * 100)
    msgs = eng.messages()
    assert len(msgs) <= 3, "store must respect cap: %d" % len(msgs)
    assert len(msgs) == 3, "expected 3 after eviction, got %d" % len(msgs)
    ids = sorted(m.msg_id for m in msgs)
    assert ids == [1, 2, 3], "oldest not evicted / newest not retained: %r" % ids
    print("  store cap OK")


def test_gossip_prune_ttl():
    """SPEC-GOSSIP-007: prune() expires entries past GOSSIP_TTL_S."""
    print("Testing prune() TTL expiry...")
    eng = gossip.GossipEngine(self_id=B1, nick="dan")
    eng.send("temp", channel=0, now=0)        # origin_ts = 0, ttl = GOSSIP_TTL_S
    assert len(eng.messages()) == 1
    eng.prune(now=(config.GOSSIP_TTL_S + 10) * 1000)
    assert len(eng.messages()) == 0, "expired message should be pruned"
    # invariant: a pruned/expired message is never relayed
    assert eng.tick_tx(_gossip_now()) is None, \
        "nothing relayable should remain after prune"
    print("  prune TTL OK")


if __name__ == "__main__":
    test_gossip_echo_own()
    test_gossip_finalise_once()
    test_gossip_out_of_order()
    test_gossip_relay_selection()
    test_gossip_ttl_decrement()
    test_gossip_store_cap()
    test_gossip_prune_ttl()
    print("\nALL GOSSIP TESTS PASSED")