#!/usr/bin/env python3
"""Mesh end-to-end propagation: the headline proof the gossip design works.

Implements SPEC-MESH-001..005 (M3). Spins up N virtual badges in one process,
each a RadioBridge(GossipEngine, SimSync) on a shared in-process MeshBus, and
pumps ticks deterministically. Plain python3, no pytest, no sockets.

Two topology modes are exercised:

* **Fully connected** (default ``MeshBus()``): every node hears every other
  node directly. SPEC-MESH-001 uses this; the propagation bound is the spec
  contract.

* **Explicit graph** (``MeshBus(links=...)``): an adjacency map so a test can
  make the *relay* path load-bearing. SPEC-MESH-002 (late join), SPEC-MESH-003
  (node leaves) and SPEC-MESH-004 (rate-limit gates own sends, not relays) all
  rely on this: if the sink could hear the originator directly, the relay code
  would never be on the critical path and the spec would be vacuously
  satisfied.

The pump advances the whole mesh one cadence cycle per call, updating every
node at BOTH the presence phase and the gossip relay phase of the cycle. The
gossip slot is ``[TX_PRESENCE_SLOT_MS, cycle)``; an earlier harness that
stepped by a fixed delta never landed in it, so ``tick_tx`` never emitted a
relay chunk and every "relay" assertion passed purely via the originator's
own-burst direct delivery. ``now`` is monotonic across ``pump()`` calls and
never resets, so a node's own-send burst window (``TX_OWN_BURST_MS``) is
honoured globally rather than re-armed per pump.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import badge_stubs
badge_stubs.install()

from chat import radio, gossip, codec, config


# ---------------------------------------------------------------------------
# Harness: N virtual badges on one in-process mesh
# ---------------------------------------------------------------------------

class Harness:
    """N RadioBridge nodes on a shared MeshBus; deterministic pump."""

    def __init__(self, n, store_max=None, links=None):
        self.bus = radio.MeshBus(links=links)
        self.nodes = []
        self._cycle_ms = config.TX_PRESENCE_SLOT_MS + config.TX_GOSSIP_SLOT_MS
        self._store_max = store_max
        self._now = 0
        for i in range(n):
            self._spawn(i)

    def _spawn(self, i, *, port=None, seed=None):
        bid = 0xA000 + i
        sync = radio.SimSync(self.bus, port=i if port is None else port)
        eng = gossip.GossipEngine(
            self_id=bid, nick="u%d" % i,
            seed=(i if seed is None else seed), store_max=self._store_max)
        bridge = radio.RadioBridge(eng, sync)
        bridge.start(now=self._now)
        self.nodes.append(bridge)
        return bridge

    def node(self, i):
        return self.nodes[i]

    def engine(self, i):
        return self.nodes[i].engine

    def pump(self, cycles):
        """Advance the whole mesh by ``cycles`` cadence cycles.

        Each cycle updates every node twice: once in the presence slot
        (phase < TX_PRESENCE_SLOT_MS) and once in the gossip relay slot
        (phase >= TX_PRESENCE_SLOT_MS). Both slots must be entered or the
        relay path in ``tick_tx`` is never exercised.
        """
        for _ in range(cycles):
            # presence phase
            for node in self.nodes:
                node.update(self._now)
            self._now += config.TX_PRESENCE_SLOT_MS
            # gossip relay phase
            for node in self.nodes:
                node.update(self._now)
            self._now += config.TX_GOSSIP_SLOT_MS
        return self._now

    @property
    def now(self):
        return self._now

    def has_message(self, i, text):
        for m in self.engine(i).messages():
            if m.text == text:
                return True
        return False


# ---------------------------------------------------------------------------
# Topology helpers (explicit-graph meshes)
# ---------------------------------------------------------------------------

def _chain_links(n):
    """Adjacency for a chain of ``n`` ports: 0-1-2-...-(n-1)."""
    links = {}
    for i in range(n - 1):
        links.setdefault(i, set()).add(i + 1)
        links.setdefault(i + 1, set()).add(i)
    return links


# ---------------------------------------------------------------------------
# SPEC-MESH-001: fully-connected loopback mesh (the propagation bound)
# ---------------------------------------------------------------------------

def test_mesh_full_propagation():
    """SPEC-MESH-001: one originator reaches all N nodes within K cycles."""
    print("Testing full mesh propagation...")
    N = 6
    h = Harness(N)
    h.engine(0).send("yo", channel=0, now=h.now)
    # K derived from cadence: the own-burst window plus a relay margin. In a
    # fully-connected mesh the own-burst reaches everyone directly, so this is
    # a generous upper bound. Assert the bound, not a magic number.
    K = (config.TX_OWN_BURST_MS // h._cycle_ms) + N + 2
    h.pump(K)
    for i in range(N):
        assert h.has_message(i, "yo"), \
            "node %d never received the message" % i
    print("  full propagation OK (all %d nodes within %d cycles)" % (N, K))


# ---------------------------------------------------------------------------
# SPEC-MESH-002..004: explicit-graph mesh so the RELAY path is load-bearing
# ---------------------------------------------------------------------------
#
# These specs assert behaviour of the *relay* path. On a fully-connected
# loopback the sink hears the originator directly, so the assertions pass
# without the relay path ever firing. The graphs below deliberately put the
# sink out of direct earshot of the originator so the only route is via the
# relay under test.

def test_mesh_late_join():
    """SPEC-MESH-002: a late-joining node receives via relay.

    Topology (chain): B1(port0) -- B2(port1) -- B3(port2) -- B4(port3) -- B5(port4)

    B1 sends; the message relays down the chain to B5. Then B6 joins, linked
    ONLY to B5 (port99 -- port4). B6 cannot hear B1 directly and arrived after
    B1's own-burst, so B6 can only receive the message via B5 relaying it.
    """
    print("Testing late join via relay (chain topology)...")
    h = Harness(5, links=_chain_links(5))
    h.engine(0).send("hello-late", channel=0, now=h.now)
    # Propagate down the 4-hop chain (each cycle = one relay hop in the gossip
    # slot). A margin covers the own-burst + relay cadence.
    h.pump((config.TX_OWN_BURST_MS // h._cycle_ms) + 6)
    for i in range(5):
        assert h.has_message(i, "hello-late"), "chain node %d missing msg" % i
    # B6 joins linked only to B5 (far end). It can only get the message if B5
    # relays it. B6 arrived after B1's own-burst expired, so direct delivery
    # from B1 is impossible -- the relay is the only path.
    h._spawn(5, port=99, seed=6)
    h.bus.add_link(99, 4)
    # Pump within GOSSIP_TTL_S (well inside; relays still carry it).
    h.pump(8)
    assert h.has_message(len(h.nodes) - 1, "hello-late"), \
        "late joiner did not receive via relay"
    print("  late join OK")


def test_mesh_node_leaves():
    """SPEC-MESH-003: originator leaves; relays carry the message onward.

    Topology: B1(port0) -- B2(port1). B1 sends; B2 caches it from B1's
    own-burst (B2's recorded hops_used stays 0 because B2 only ever hears the
    origin at hops=0 -- the origin never echoes its own message, so there is
    no relay ping-pong to inflate the hops counter). B1 then leaves. A *new*
    node B3 joins, linked ONLY to B2 (port99 -- port1). B3 cannot hear B1
    (gone) and arrived after B1's burst, so B3 can only receive the message
    via B2 relaying it. This makes the "continues via the remaining relays"
    clause genuinely load-bearing rather than satisfied by direct delivery.

    Note on the hops counter: the engine's max-wins ``hops_used`` tracker
    inflates under relay ping-pong between non-origin neighbours (a known
    pathology, out of scope for this spec). Using B1's *direct* neighbour B2
    as the relay source avoids that -- B2's hops_used never rises above 0
    while B1 is bursting, so B2 can always relay onward.
    """
    print("Testing node-leaves resilience (2-node + joiner)...")
    h = Harness(2, links=_chain_links(2))
    h.engine(0).send("survivor", channel=0, now=h.now)
    # A couple of cycles: B2 caches the message from B1's own-burst (hops=0).
    h.pump(2)
    assert h.has_message(1, "survivor"), \
        "direct neighbour B2 should have the message before B1 leaves"
    # Sanity: B2's hops_used is still 0 (only heard the origin's burst).
    b2_entry = h.engine(1)._store.get((h.engine(0).self_id, 0))
    assert b2_entry is not None and b2_entry.hops_used == 0, \
        "precondition: B2 hops_used should be 0, got %r" % (
            b2_entry.hops_used if b2_entry else None)
    # B1 leaves (stops advertising + scanning).
    h.node(0).stop()
    # B3 joins linked only to B2. It can only get the message if B2 relays it
    # after B1's departure.
    h._spawn(2, port=99, seed=5)
    h.bus.add_link(99, 1)
    # Pump within GOSSIP_TTL_S (well inside; relays still carry it).
    h.pump(6)
    idx_b3 = len(h.nodes) - 1
    assert h.has_message(idx_b3, "survivor"), \
        "B3 (joined after B1 left) did not receive via B2's relay"
    # B2 still has it (the leave didn't evict it).
    assert h.has_message(1, "survivor"), \
        "B2 lost the message after B1 left"
    print("  node-leaves OK (B2 relayed to joiner after originator left)")


def test_mesh_rate_limit_own_only():
    """SPEC-MESH-004: rate-limit gates own sends, not relays.

    Topology (hub-and-spoke around B2):

        B1(port0) -- B2(port1, hub) -- B3(port2, sink)
                        |
                     B4(port3)  (a separate sender)

    B2 is the only path from B1/B4 to the sink B3. B2 sends once (arming its
    rate-limit cooldown) and a second own-send inside RATE_LIMIT_MS is refused.
    After B2's own-burst expires (so its relay slot is free), B4 sends a fresh
    message; B2 -- still rate-limited on its OWN sends -- must relay B4's
    message to B3. Because B3 can only hear B2, receiving B4's text at B3
    proves the relay fired during the rate-limit cooldown.
    """
    print("Testing rate-limit own-only (hub topology)...")
    links = {
        0: {1},          # B1 -> B2
        1: {0, 2, 3},    # B2 hub -> B1, sink B3, sender B4
        2: {1},          # sink B3 <- B2 only
        3: {1},          # sender B4 -> B2 only
    }
    h = Harness(4, links=links)
    t0 = h.now
    # B2 sends once; this arms its rate-limit cooldown for RATE_LIMIT_MS.
    out1 = h.engine(1).send("b2-own", channel=0, now=t0)
    assert out1 is not None, "B2's first own send must succeed"
    # A second own-send inside RATE_LIMIT_MS is refused (the spec's primary
    # clause). Asserted at the engine so the check is exact, not pump-dependent.
    out2 = h.engine(1).send("b2-again", channel=0,
                            now=t0 + config.RATE_LIMIT_MS // 2)
    assert out2 is None, \
        "B2 second own-send within cooldown must be refused"
    # Pump B2's own-burst out to its neighbours, then past TX_OWN_BURST_MS so
    # B2's relay slot is free (during the burst, tick_tx emits own chunks, not
    # relays). +2 cycles of margin past the burst window.
    burst_cycles = (config.TX_OWN_BURST_MS // h._cycle_ms) + 2
    h.pump(burst_cycles)
    # B4 (a different node) now sends. B2 is STILL rate-limited on its own
    # sends (cooldown is RATE_LIMIT_MS, much longer than the burst), but its
    # burst has expired so it will relay B4's message in the gossip slot.
    assert not h.engine(1).can_send(h.now), \
        "B2 must still be rate-limited when B4 sends (precondition)"
    h.engine(3).send("from-b4", channel=0, now=h.now)
    # Pump for B4's burst to reach B2 + B2's relay to reach sink B3.
    h.pump((config.TX_OWN_BURST_MS // h._cycle_ms) + 4)
    assert h.has_message(2, "from-b4"), \
        "sink B3 must receive B4's message via B2's relay while B2 is " \
        "rate-limited on its own sends"
    # The refused own-send never leaked onto the wire: "b2-again" is nowhere.
    for i in range(len(h.nodes)):
        assert not h.has_message(i, "b2-again"), \
            "rate-limited own-send 'b2-again' must never propagate"
    print("  rate-limit own-only OK (relay fired during own-send cooldown)")


# ---------------------------------------------------------------------------
# SPEC-MESH-005: nick rename (presence path, fully connected)
# ---------------------------------------------------------------------------

def test_mesh_nick_rename():
    """SPEC-MESH-005: nick rename propagates within one presence slot."""
    print("Testing nick rename propagation...")
    h = Harness(2)
    h.engine(0).set_nick("alice")
    # Pump so B2 hears B1's presence and learns "alice".
    h.pump(3)
    peers = h.node(1).peers(h.now)
    assert any(p["beacon"]["nick"] == "alice" for p in peers), \
        "B2 should have learned nick 'alice'"
    # B1 renames; the next presence beacon carries "alice2".
    h.engine(0).set_nick("alice2")
    # Pump within one presence slot cycle (a few cycles to be safe given the
    # set-payload-then-advertise-next-cycle ordering).
    h.pump(4)
    peers2 = h.node(1).peers(h.now)
    assert any(p["beacon"]["nick"] == "alice2" for p in peers2), \
        "B2 should see renamed nick 'alice2'"
    # badge_id unchanged.
    b1_id = h.engine(0).self_id
    assert any(p["beacon"]["badge_id"] == b1_id for p in peers2), \
        "B1's badge_id must be unchanged across rename"
    print("  nick rename OK")


if __name__ == "__main__":
    test_mesh_full_propagation()
    test_mesh_late_join()
    test_mesh_node_leaves()
    test_mesh_rate_limit_own_only()
    test_mesh_nick_rename()
    print("\nALL MESH TESTS PASSED")
