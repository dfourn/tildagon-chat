"""Radio transport + mesh wiring for the P2P chat app.

Adapts the twin_flame/sync_ble.py transport contract (start/set_payload/
advertise/poll/drain/stop) to the chat GossipEngine. Two backends:

* ``BLESync``  -- hardware: raw ``bluetooth`` module, non-connectable
                  ``gap_advertise`` + passive ``gap_scan``, IRQ-driven receive,
                  bounded buffer. Guarded by ``hw.fw_allows_ble`` (the firmware
                  floor that keeps old firmware from hard-hanging the badge).
* ``SimSync``  -- in-process loopback over a shared ``MeshBus`` (no sockets) so
                  N virtual badges can be spun up in one test process and pumped
                  deterministically. ``make_sync()`` picks SimSync when the
                  ``_sim`` module is present (simulator), else BLE.

``RadioBridge`` is the glue: each tick it advertises, polls, drains received
adverts, feeds them to the engine (chunk or presence), pulls the engine's next
tx advert (own-burst / relay chunk / None=presence), sets the sync payload, and
prunes. ``peers(now)`` exposes the PeerTable for the NearbyScreen.

``PeerTable`` is copied (not imported cross-package) from
twin_flame/sync_ble.py and trimmed to chat presence beacons: RSSI EMA smoothing,
stale-pruning by ``PEER_MAX_AGE_MS``, freshest-nick-wins so renames propagate
without a rename protocol (plan section 5).

Plan reference: chat-2026.md section 2 (transport), section 5 (presence),
section 16 (critical files). Codec/radio logic imports no ``bluetooth`` so the
unit-testable surface stays host-testable (plan section 12).
"""

from . import codec, config, hw


# ---------------------------------------------------------------------------
# PeerTable (presence stream -- adapted from twin_flame/sync_ble.py)
# ---------------------------------------------------------------------------

class PeerTable:
    """Owns presence snapshots, RSSI EMA smoothing, and freshness pruning.

    Keyed by a transport key (BLE MAC hex / sim port). On ``update()`` the
    latest presence beacon wins -- so a nick rename ripples through the mesh
    naturally with no rename protocol. ``peers(now)`` prunes entries older
    than ``config.PEER_MAX_AGE_MS`` and returns strongest-RSSI-first.
    """

    def __init__(self):
        self._peers = {}  # key -> {"beacon": dict, "rssi_ema": float, "t": int}

    def update(self, key, beacon, rssi, now_ms):
        if key in self._peers:
            entry = self._peers[key]
            old = entry["rssi_ema"]
            entry["rssi_ema"] = old + (rssi - old) * config.RSSI_EMA
            entry["beacon"] = beacon
            entry["t"] = now_ms
        else:
            self._peers[key] = {
                "beacon": beacon,
                "rssi_ema": float(rssi),
                "t": now_ms,
            }

    def peers(self, now_ms):
        """Fresh peer dicts (beacon + rssi_ema), strongest-RSSI-first."""
        out = []
        for key in list(self._peers.keys()):
            entry = self._peers[key]
            if now_ms - entry["t"] <= config.PEER_MAX_AGE_MS:
                out.append(entry)
            else:
                del self._peers[key]
        out.sort(key=lambda p: p["rssi_ema"], reverse=True)
        return out

    def get(self, badge_id, now_ms):
        for entry in self.peers(now_ms):
            if entry["beacon"].get("badge_id") == badge_id:
                return entry
        return None

    def __len__(self):
        return len(self._peers)


# ---------------------------------------------------------------------------
# Sync interface
# ---------------------------------------------------------------------------

class Sync:
    """Common pump interface for every transport backend."""

    def start(self, payload):
        raise NotImplementedError

    def set_payload(self, payload):
        raise NotImplementedError

    def advertise(self):
        pass

    def poll(self):
        pass

    def drain(self):
        return []

    def stop(self):
        pass


# ---------------------------------------------------------------------------
# In-process mesh bus (host tests / sim -- no sockets)
# ---------------------------------------------------------------------------

class MeshBus:
    """Shared in-process loopback connecting N SimSync instances.

    Each SimSync registers a port (any hashable) + receives a dedicated deque.
    ``publish(port, payload)`` copies the bytes into every *other* registered
    inbox. Fully deterministic and pumpable; never the shipping path.
    """

    def __init__(self, links=None):
        # ``links`` (optional): {port: set(neighbour_ports)} for a non-fully-
        # connected mesh, so end-to-end tests can make the relay path load-
        # bearing (plan section 5; SPEC-MESH-003/004). Default ``None`` is the
        # fully-connected loopback every existing caller assumes.
        self._inboxes = {}     # port -> list
        self._rssi = -50       # synthetic loopback RSSI
        self._links = links

    def register(self, port):
        if port not in self._inboxes:
            self._inboxes[port] = []

    def add_link(self, a, b):
        """Add an undirected edge for a dynamic topology (e.g. a node joining
        after another left). Only valid in explicit-graph mode (``links=``
        passed at construction); the fully-connected default has no edge list.
        """
        if self._links is None:
            raise RuntimeError("add_link requires a links-configured MeshBus")
        self._links.setdefault(a, set()).add(b)
        self._links.setdefault(b, set()).add(a)

    def publish(self, src_port, payload):
        neighbours = self._links[src_port] if self._links is not None else None
        for port, inbox in self._inboxes.items():
            if port == src_port:
                continue
            if neighbours is not None and port not in neighbours:
                continue
            inbox.append(payload)

    def take(self, port):
        inbox = self._inboxes.get(port)
        if not inbox:
            return []
        out = inbox[:]
        inbox.clear()
        return out

    @property
    def rssi(self):
        return self._rssi


# ---------------------------------------------------------------------------
# SimSync (in-process, host-testable)
# ---------------------------------------------------------------------------

class SimSync(Sync):
    """One node on a MeshBus. ``advertise()`` publishes, ``poll()`` drains."""

    def __init__(self, bus, port):
        self._bus = bus
        self._port = port
        self._payload = None
        self._buffer = []      # (key, beacon_dict, rssi, t_ms)
        self.active = False
        self._now_fn = hw.ticks_ms
        bus.register(port)

    def start(self, payload):
        self._payload = payload
        self._buffer = []
        self.active = True
        return True

    def set_payload(self, payload):
        self._payload = payload

    def advertise(self):
        if self._payload is None:
            return
        self._bus.publish(self._port, self._payload)

    def poll(self):
        for raw in self._bus.take(self._port):
            self._ingest_raw(raw)

    def _ingest_raw(self, raw):
        now = self._now_fn()
        # Try both advert types; whichever parses wins.
        chunk = codec.parse_chunk(raw)
        if chunk is not None:
            key = "%s:msg:%x:%x" % (
                self._port, chunk["origin_id"], chunk["msg_id"])
            self._buffer.append((key, chunk, self._bus.rssi, now))
            return
        presence = codec.parse_presence(raw)
        if presence is not None:
            key = "%s:pres:%x" % (self._port, presence["badge_id"])
            self._buffer.append((key, presence, self._bus.rssi, now))

    def drain(self):
        out = self._buffer[:]
        self._buffer = []
        return out

    def stop(self):
        self.active = False
        self._payload = None
        self._buffer = []


# ---------------------------------------------------------------------------
# BLE hardware backend (modeled on twin_flame/sync_ble.py BLESync)
# ---------------------------------------------------------------------------

# ON-BADGE: the radio path. gap_advertise re-issue at the gossip cadence is the
# NOTES.md-flagged unvalidated cost; the M4 spike gates it. The host-testable
# surface (MeshBus + GossipEngine) covers all propagation logic; BLESync itself
# is only exercised on hardware.

try:
    import bluetooth as _bluetooth
    _HAS_BLE = True
except Exception:
    # Catch broadly (not just ImportError): on newer (2026) firmware the
    # module exists but may raise non-ImportError during init. Space Scanner's
    # discovery.py (hardware-proven) uses exactly this guard; a narrow
    # except-ImportError would crash the app. See space_scanner/discovery.py.
    _bluetooth = None
    _HAS_BLE = False

_IRQ_SCAN_RESULT = 5


class BLESync(Sync):
    """Advertise our payload + passively scan for chat adverts. Hardware-only."""

    def __init__(self):
        self._ble = None
        self._payload = None
        self._adv_interval_us = config.ADV_INTERVAL_US
        self._buffer = []
        self.active = False

    @staticmethod
    def available():
        return _HAS_BLE and hw._ble_fw_safe()

    def start(self, payload):
        if not self.available():
            return False
        try:
            self._ble = _bluetooth.BLE()
            self._ble.active(True)
            self._ble.irq(self._irq)
            self._payload = payload
            self._buffer = []
            self._ble.gap_advertise(
                self._adv_interval_us, adv_data=payload, connectable=False)
            self._ble.gap_scan(0, config.SCAN_INTERVAL_US,
                               config.SCAN_WINDOW_US, True)
            self.active = True
            return True
        except Exception:
            self.stop()
            return False

    def _irq(self, event, data):
        if event != _IRQ_SCAN_RESULT:
            return
        _addr_type, addr, _adv_type, rssi, adv_data = data
        # MUST copy out of transient IRQ buffers before parsing/storing.
        raw = bytes(adv_data)
        key = bytes(addr).hex()
        chunk = codec.parse_chunk(raw)
        if chunk is not None:
            self._buffer.append((key, chunk, rssi, hw.ticks_ms()))
            self._cap()
            return
        presence = codec.parse_presence(raw)
        if presence is not None:
            self._buffer.append((key, presence, rssi, hw.ticks_ms()))
            self._cap()

    def _cap(self):
        if len(self._buffer) > 32:
            self._buffer.pop(0)

    def set_payload(self, payload):
        if payload == self._payload:
            return
        self._payload = payload
        if not self.active or self._ble is None:
            return
        try:
            # Stop the current advert before re-issuing with new data. Some
            # BLE stacks silently drop the new payload if the old one is still
            # active. Space Scanner never changes its payload so never hits
            # this; chat switches between presence/chunk and must be robust.
            self._ble.gap_advertise(None)
            self._ble.gap_advertise(
                self._adv_interval_us, adv_data=payload, connectable=False)
        except Exception:
            pass

    def drain(self):
        out = self._buffer[:]
        self._buffer = []
        return out

    def stop(self):
        if self._ble is not None:
            for fn in (
                lambda: self._ble.gap_scan(None),
                lambda: self._ble.gap_advertise(None),
                lambda: self._ble.irq(None),
                lambda: self._ble.active(False),
            ):
                try:
                    fn()
                except Exception:
                    pass
        self._ble = None
        self._payload = None
        self._buffer = []
        self.active = False


def make_sync():
    """SimSync in the simulator, else BLE. (Plan section 2.)"""
    try:
        import _sim  # noqa: F401 -- present only in the simulator
        return SimSync(MeshBus(), port="sim")
    except ImportError:
        return BLESync()


# ---------------------------------------------------------------------------
# RadioBridge: engine <-> sync glue
# ---------------------------------------------------------------------------

class RadioBridge:
    """Drives one badge: each ``update(now)`` pumps the radio + engine.

    The pump order (plan section 7) is load-bearing:
      advertise -> poll -> drain -> ingest each received beacon -> set_payload
      to the engine's next tx (own-burst / relay / None=presence) -> prune.

    Presence beacons refresh the PeerTable (RSSI EMA + freshest nick wins) so
    NearbyScreen + nick directory stay current with no server.
    """

    def __init__(self, engine, sync, *, peer_table=None):
        self.engine = engine
        self.sync = sync
        self.peer_table = peer_table if peer_table is not None else PeerTable()
        self._payload = None
        self._stopped = False

    @property
    def stopped(self):
        return self._stopped

    def peers(self, now):
        return self.peer_table.peers(now)

    def start(self, now=0):
        # Restartable: the launcher caches app instances, so a relaunch after
        # _exit() re-enters through here with _stopped set. Both backends
        # support a fresh start() after stop().
        self._stopped = False
        self._payload = self.engine.presence_beacon(now)
        return self.sync.start(self._payload)

    def stop(self):
        self._stopped = True
        self.sync.stop()

    def update(self, now):
        """One pump. Returns True while active."""
        if self._stopped:
            return False
        self.sync.advertise()
        self.sync.poll()
        for key, beacon, rssi, t in self.sync.drain():
            kind = self._classify(beacon)
            if kind == "presence":
                self.engine.ingest_presence(beacon, now)
                self.peer_table.update(key, beacon, rssi, now)
            elif kind == "chunk":
                self.engine.ingest_chunk(beacon, now)
        tx = self.engine.tick_tx(now)
        payload = tx if tx is not None else self.engine.presence_beacon(now)
        self.sync.set_payload(payload)
        self.engine.prune(now)
        return True

    @staticmethod
    def _classify(beacon):
        # Presence beacons carry badge_id; chunks carry origin_id.
        if "badge_id" in beacon:
            return "presence"
        if "origin_id" in beacon:
            return "chunk"
        return "unknown"