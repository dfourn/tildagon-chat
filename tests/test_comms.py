#!/usr/bin/env python3
"""Radio adapter wiring: RadioBridge drives GossipEngine <-> Sync (M3).

The server-era HTTP ChatClient tests lived here; that class is gone under the
P2P plan. Per plans/chat-2026.md section 12 / chat-2026-spec.md section 5 this
file now tests the RadioBridge against a fake Sync: pump order, beacon
classification (chunk vs presence vs unknown), PeerTable wiring, payload
selection (own-burst chunk vs presence fallback), and stop semantics.
Propagation across N nodes is test_mesh.py's job; this file pins the
single-node adapter contract. Plain python3, no pytest, no sockets.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import badge_stubs
badge_stubs.install()

from chat import radio, gossip, codec, config


class FakeSync(radio.Sync):
    """Records every call in order; drain() hands back injected beacons."""

    def __init__(self, start_result=True):
        self.calls = []
        self.payloads = []          # every set_payload value, in order
        self.inbox = []             # (key, beacon_dict, rssi, t_ms) to drain
        self.started_with = None
        self.start_result = start_result
        self.stopped = False

    def start(self, payload):
        self.calls.append("start")
        self.started_with = payload
        return self.start_result

    def set_payload(self, payload):
        self.calls.append("set_payload")
        self.payloads.append(payload)

    def advertise(self):
        self.calls.append("advertise")

    def poll(self):
        self.calls.append("poll")

    def drain(self):
        self.calls.append("drain")
        out = self.inbox[:]
        self.inbox = []
        return out

    def stop(self):
        self.calls.append("stop")
        self.stopped = True


def make_bridge(start_result=True, self_id=0xA001, nick="alice"):
    sync = FakeSync(start_result=start_result)
    eng = gossip.GossipEngine(self_id=self_id, nick=nick, seed=1)
    bridge = radio.RadioBridge(eng, sync)
    return bridge, eng, sync


def chunk_beacon(text="yo", origin_id=0xB002, msg_id=7, channel=0):
    """A parsed single-chunk beacon, round-tripped through the real codec."""
    adv = codec.encode_chunk(
        origin_id=origin_id, msg_id=msg_id, channel=channel, hops_used=0,
        chunk_index=0, total_chunks=1,
        ttl_remaining_s=config.GOSSIP_TTL_S, text=text)
    return codec.parse_chunk(adv)


def presence_beacon(badge_id=0xB002, nick="bob"):
    adv = codec.encode_presence(
        badge_id=badge_id, channels_bitmap=0x01, nick=nick)
    return codec.parse_presence(adv)


def test_bridge_start_carries_presence():
    """start() hands the engine's presence beacon to sync.start()."""
    print("Testing bridge start carries presence beacon...")
    bridge, eng, sync = make_bridge()
    assert bridge.start(now=0) is True
    assert sync.calls == ["start"]
    assert isinstance(sync.started_with, bytes)
    assert len(sync.started_with) <= 31
    parsed = codec.parse_presence(sync.started_with)
    assert parsed is not None and parsed["badge_id"] == 0xA001
    # A refused transport start (e.g. BLE floor) surfaces to the caller.
    bridge2, _, _ = make_bridge(start_result=False)
    assert bridge2.start(now=0) is False
    print("  start OK")


def test_bridge_pump_order():
    """update() pumps advertise -> poll -> drain -> set_payload (plan s.7)."""
    print("Testing bridge pump order...")
    bridge, eng, sync = make_bridge()
    bridge.start(now=0)
    assert bridge.update(0) is True
    assert sync.calls == ["start", "advertise", "poll", "drain", "set_payload"]
    print("  pump order OK")


def test_bridge_ingests_chunk():
    """A drained chunk beacon lands in the engine's finalised messages."""
    print("Testing bridge ingests chunk beacons...")
    bridge, eng, sync = make_bridge()
    bridge.start(now=0)
    sync.inbox.append(("k1", chunk_beacon(text="yo"), -50, 0))
    bridge.update(0)
    texts = [m.text for m in eng.messages()]
    assert texts == ["yo"]
    print("  chunk ingest OK")


def test_bridge_ingests_presence():
    """A drained presence beacon lands in the PeerTable via peers(now)."""
    print("Testing bridge ingests presence beacons...")
    bridge, eng, sync = make_bridge()
    bridge.start(now=0)
    sync.inbox.append(("k2", presence_beacon(badge_id=0xB002, nick="bob"), -42, 0))
    bridge.update(0)
    peers = bridge.peers(0)
    assert len(peers) == 1
    assert peers[0]["beacon"]["badge_id"] == 0xB002
    assert peers[0]["beacon"]["nick"].strip() == "bob"
    assert peers[0]["rssi_ema"] == -42.0
    # No chat message was fabricated from a presence beacon.
    assert eng.messages() == []
    print("  presence ingest OK")


def test_bridge_unknown_beacon_ignored():
    """Beacons that are neither chunk nor presence are dropped harmlessly."""
    print("Testing bridge ignores unknown beacons...")
    bridge, eng, sync = make_bridge()
    bridge.start(now=0)
    sync.inbox.append(("k3", {"weird": 1}, -50, 0))
    bridge.update(0)
    assert eng.messages() == []
    assert bridge.peers(0) == []
    print("  unknown beacon OK")


def test_bridge_payload_selection():
    """Own-send burst puts chunk adverts on air; idle falls back to presence."""
    print("Testing bridge payload selection...")
    bridge, eng, sync = make_bridge()
    bridge.start(now=0)
    # Idle, presence slot (now=0 is inside TX_PRESENCE_SLOT_MS): presence.
    bridge.update(0)
    parsed = codec.parse_presence(sync.payloads[-1])
    assert parsed is not None and parsed["badge_id"] == 0xA001
    # After send(), within TX_OWN_BURST_MS the payload is an own chunk.
    assert eng.send("hello mesh", 0, now=0) is not None
    bridge.update(1)
    chunk = codec.parse_chunk(sync.payloads[-1])
    assert chunk is not None and chunk["origin_id"] == 0xA001
    assert len(sync.payloads[-1]) <= 31
    print("  payload selection OK")


def test_bridge_stop():
    """stop() stops the sync; further update() calls are inert."""
    print("Testing bridge stop...")
    bridge, eng, sync = make_bridge()
    bridge.start(now=0)
    bridge.stop()
    assert sync.stopped is True
    n_calls = len(sync.calls)
    assert bridge.update(0) is False
    assert len(sync.calls) == n_calls  # no radio traffic after stop
    print("  stop OK")


if __name__ == "__main__":
    test_bridge_start_carries_presence()
    test_bridge_pump_order()
    test_bridge_ingests_chunk()
    test_bridge_ingests_presence()
    test_bridge_unknown_beacon_ignored()
    test_bridge_payload_selection()
    test_bridge_stop()
    print("\nALL COMMS TESTS PASSED")
