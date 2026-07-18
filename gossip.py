"""Gossip engine: bounded message store + epidemic relay schedule.

Pure state machine (no radio imports) so every behaviour is host-testable on
plain CPython. Parsed chunk dicts (from chat.codec.parse_chunk) go in via
ingest_chunk(); advert bytes come out via send()/tick_tx(), all against an
injected ``now`` clock (ms). This mirrors the infection/comms.py split between
a pure reassembler and a burst transmitter, generalised to N cached messages
with a fresh-and-rare-first relay selection.

Plan reference: chat-2026.md section 4 (GossipStore + GossipEngine),
chat-2026-spec.md section 4 (SPEC-GOSSIP-001..007).

Loop-prevention + bounded radio cost come from two TTLs (both in config.py):
  * GOSSIP_TTL_HOPS   -- relays increment hops_used; at the cap, drop.
  * GOSSIP_TTL_S      -- relays recompute ttl_remaining from wall time; at 0, drop.
Both are checked before any relay chunk is emitted, and prune() evicts expired
entries so the store is self-bounding.

Anti-entropy relay selection (plan section 4): from relayable entries pick the
one maximising ``ttl_remaining - times_relayed_by_me`` (freshest wins; as a
message is relayed more its score drops and newer ones take the slot). Ties are
broken deterministically by (origin_id, msg_id) then by the module-level
``random`` (no random.Random on MicroPython; seedable for tests).
"""

import random

from . import codec, config


# ---------------------------------------------------------------------------
# Stored message
# ---------------------------------------------------------------------------

class StoredMessage:
    """One cached message: finalised or partial, own or relayed.

    Keyed in the store by (origin_id, msg_id). Tracks the reassembled chunks,
    the inferred origin timestamp (for TTL math + LRU eviction), the highest
    hops_used seen, how many times *this badge* has relayed it, and a
    round-robin cursor so successive relays cycle through the chunks fairly.
    """

    __slots__ = (
        "origin_id", "msg_id", "channel", "mention",
        "chunks", "total_chunks", "origin_ts",
        "ttl_expiry_ms", "hops_used",
        "times_relayed_by_me", "relay_cursor",
        "last_heard_ms", "last_relayed_ms",
        "nick",  # best-effort sender nick from presence dir (may be None)
    )

    def __init__(self, origin_id, msg_id, channel, total_chunks, origin_ts,
                 ttl_expiry_ms, hops_used, mention=False, nick=None):
        self.origin_id = origin_id
        self.msg_id = msg_id
        self.channel = channel
        self.mention = mention
        self.chunks = {}            # chunk_index -> text piece
        self.total_chunks = total_chunks
        self.origin_ts = origin_ts
        self.ttl_expiry_ms = ttl_expiry_ms
        self.hops_used = hops_used
        self.times_relayed_by_me = 0
        self.relay_cursor = 0
        self.last_heard_ms = origin_ts
        self.last_relayed_ms = 0
        self.nick = nick

    def complete(self):
        return len(self.chunks) >= self.total_chunks

    def text(self):
        """Assembled text in chunk order (missing pieces -> "")."""
        return "".join(self.chunks.get(i, "")
                       for i in range(self.total_chunks))


class DisplayMessage:
    """Read-only view of a finalised message, oldest-first for the feed."""

    __slots__ = ("origin_id", "msg_id", "channel", "text", "origin_ts",
                 "hops_used", "mention", "nick")

    def __init__(self, stored):
        self.origin_id = stored.origin_id
        self.msg_id = stored.msg_id
        self.channel = stored.channel
        self.text = stored.text()
        self.origin_ts = stored.origin_ts
        self.hops_used = stored.hops_used
        self.mention = stored.mention
        self.nick = stored.nick


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class GossipEngine:
    """Owns the message store + transmit schedule. No radio imports.

    Construct with an injected ``now``-style clock only if you want the engine
    to read time itself; all public methods take an explicit ``now`` (ms) so
    tests are fully deterministic without monkeypatching.
    """

    def __init__(self, self_id, nick, *, channels_bitmap=0x01, now=None,
                 seed=None, store_max=None):
        self.self_id = self_id
        self.nick = codec.sanitize_nick(nick)
        self.channels_bitmap = channels_bitmap & 0xFF
        self._store_max = (config.GOSSIP_STORE_MAX
                           if store_max is None else store_max)
        self._store = {}              # (origin_id, msg_id) -> StoredMessage
        self._finalised = []          # StoredMessage refs, oldest-first
        self._next_msg_id = 0
        # Own-send burst state.
        self._own_chunks = []         # advert bytes, one per chunk
        self._own_send_ts = None
        self._own_cursor = 0
        self._own_msg_id = None       # msg_id of the in-burst own message
        if seed is not None:
            random.seed(seed)

    # -- ingestion ----------------------------------------------------------

    def ingest_chunk(self, chunk, now):
        """Feed one parsed chunk dict; return True iff a message *just* finalised.

        Reconstructs origin_ts from the chunk's ttl_remaining_s so TTL math and
        LRU eviction agree (a ttl=10 message heard at t0 expires ~10s later,
        not GOSSIP_TTL_S later). Own chunks (origin_id == self_id) are ignored
        on ingest -- a badge never scans its own adverts, but defensively we
        don't double-finalise if one slips in.
        """
        if chunk is None:
            return False
        total = chunk["total_chunks"]
        if total < 1 or chunk["chunk_index"] >= total:
            return False
        if chunk["origin_id"] == self.self_id:
            return False  # our own message is already in the store via send()

        ttl_now = chunk["ttl_remaining_s"]
        age_s = max(0, config.GOSSIP_TTL_S - ttl_now)
        origin_ts = now - age_s * 1000
        ttl_expiry_ms = origin_ts + config.GOSSIP_TTL_S * 1000

        key = (chunk["origin_id"], chunk["msg_id"])
        entry = self._store.get(key)
        if entry is None:
            entry = StoredMessage(
                origin_id=chunk["origin_id"],
                msg_id=chunk["msg_id"],
                channel=chunk["channel"],
                total_chunks=total,
                origin_ts=origin_ts,
                ttl_expiry_ms=ttl_expiry_ms,
                hops_used=chunk["hops_used"],
                mention=chunk.get("mention", False),
            )
            self._store[key] = entry

        entry.chunks[chunk["chunk_index"]] = chunk["text"]
        # Keep the freshest TTL window we've seen (a later chunk may carry a
        # less-decremented ttl from a closer relay).
        if ttl_expiry_ms > entry.ttl_expiry_ms:
            entry.ttl_expiry_ms = ttl_expiry_ms
            entry.origin_ts = origin_ts
        # Track the max hops so relay decrement is monotone.
        if chunk["hops_used"] > entry.hops_used:
            entry.hops_used = chunk["hops_used"]
        entry.last_heard_ms = now

        if entry.complete() and entry not in self._finalised:
            self._finalised.append(entry)
            self._enforce_cap()
            return True
        return False

    def ingest_presence(self, presence, now):
        """Refresh the nick directory + peer table from a presence beacon.

        Minimal here: remember the latest nick per badge_id so DisplayMessage
        can label relayed lines. The full PeerTable lives in radio.py where
        RSSI smoothing + freshness pruning belong (plan section 5).
        """
        if presence is None:
            return
        bid = presence.get("badge_id")
        if bid is None or bid == self.self_id:
            return
        nick = presence.get("nick") or "anon"
        for entry in self._store.values():
            if entry.origin_id == bid and not entry.nick:
                entry.nick = nick

    # -- origination --------------------------------------------------------

    def send(self, text, channel, now):
        """Chunk + seed MY new message; return its chunk adverts to broadcast.

        Returns None when rate-limited (within RATE_LIMIT_MS of the last own
        send); only own sends are gated, relays always flow. When allowed, the
        store is seeded finalised so the feed shows the line immediately (a
        badge never scans its own adverts -- local echo is mandatory). Also
        arms the own-send burst so tick_tx prefers these chunks for
        TX_OWN_BURST_MS.
        """
        if not self.can_send(now):
            return None
        pieces = codec.chunk_text(text)
        msg_id = self._next_msg_id
        self._next_msg_id = (self._next_msg_id + 1) & 0xFFFF
        total = len(pieces)
        ttl = config.GOSSIP_TTL_S
        adverts = []
        chunks_map = {}
        for idx, piece in enumerate(pieces):
            adv = codec.encode_chunk(
                origin_id=self.self_id, msg_id=msg_id, channel=channel & 0x07,
                hops_used=0, chunk_index=idx, total_chunks=total,
                ttl_remaining_s=ttl, text=piece)
            adverts.append(adv)
            chunks_map[idx] = codec.parse_chunk(adv)["text"]

        entry = StoredMessage(
            origin_id=self.self_id, msg_id=msg_id, channel=channel & 0x07,
            total_chunks=total, origin_ts=now,
            ttl_expiry_ms=now + ttl * 1000, hops_used=0,
            nick=self.nick)
        entry.chunks = chunks_map
        self._store[(self.self_id, msg_id)] = entry
        self._finalised.append(entry)
        self._enforce_cap()

        self._own_chunks = adverts
        self._own_send_ts = now
        self._own_cursor = 0
        self._own_msg_id = msg_id
        return adverts

    def can_send(self, now):
        """Rate-limit own sends (not relays)."""
        return (self._own_send_ts is None or
                now - self._own_send_ts >= config.RATE_LIMIT_MS)

    def cooldown_remaining_ms(self, now):
        if self._own_send_ts is None:
            return 0
        left = config.RATE_LIMIT_MS - (now - self._own_send_ts)
        return left if left > 0 else 0

    # -- presence -----------------------------------------------------------

    def presence_beacon(self, now, typing_now=False):
        """The advert to carry in a presence slot."""
        return codec.encode_presence(
            badge_id=self.self_id,
            channels_bitmap=self.channels_bitmap,
            nick=self.nick, typing_now=typing_now)

    def set_nick(self, nick):
        """Update nick; the next presence_beacon() carries the new name."""
        self.nick = codec.sanitize_nick(nick)

    def set_channels(self, bitmap):
        self.channels_bitmap = bitmap & 0xFF

    # -- transmit schedule --------------------------------------------------

    def _ttl_remaining_s(self, entry, now):
        return max(0, (entry.ttl_expiry_ms - now) // 1000)

    def _relayable(self, entry, now):
        if entry.origin_id == self.self_id:
            return False
        if not entry.complete():
            return False
        if entry.hops_used + 1 > config.GOSSIP_TTL_HOPS:
            return False
        if self._ttl_remaining_s(entry, now) <= 0:
            return False
        return True

    def _select_relay(self, now):
        """Fresh-and-rare-first selection (plan section 4).

        Score = ttl_remaining(now) - times_relayed_by_me; max wins. Ties are
        broken by deterministic (origin_id, msg_id) ascending, then by the
        module-level random for extra mesh spread. Returns a StoredMessage or
        None.
        """
        best = None
        best_score = None
        for entry in self._store.values():
            if not self._relayable(entry, now):
                continue
            score = self._ttl_remaining_s(entry, now) - entry.times_relayed_by_me
            if best is None or score > best_score or (
                    score == best_score and
                    (entry.origin_id, entry.msg_id) <
                    (best.origin_id, best.msg_id)):
                best = entry
                best_score = score
            elif score == best_score:
                # seeded-RNG spread on genuine score+key ties (rare in tests,
                # helpful in a real mesh). Deterministic given a fixed seed.
                if random.random() < 0.5:
                    best = entry
        return best

    def _relay_chunk_for(self, entry, now):
        """Re-encode the next chunk for ``entry`` with hops+1 and ttl elapsed."""
        idx = entry.relay_cursor % entry.total_chunks
        entry.relay_cursor = (entry.relay_cursor + 1) % entry.total_chunks
        new_ttl = self._ttl_remaining_s(entry, now)
        if new_ttl <= 0:
            return None
        adv = codec.encode_chunk(
            origin_id=entry.origin_id, msg_id=entry.msg_id,
            channel=entry.channel, hops_used=entry.hops_used + 1,
            chunk_index=idx, total_chunks=entry.total_chunks,
            ttl_remaining_s=new_ttl, text=entry.chunks[idx],
            mention=entry.mention)
        entry.times_relayed_by_me += 1
        entry.last_relayed_ms = now
        return adv

    def tick_tx(self, now):
        """Next advert to carry NOW, or None meaning "carry presence".

        Schedule (plan section 4 / config cadence):
          * if within TX_OWN_BURST_MS of a send, cycle through own chunks;
          * else alternate presence (TX_PRESENCE_SLOT_MS) / gossip relay
            (TX_GOSSIP_SLOT_MS) by phase; in the gossip slot, return a relay
            chunk if any relayable message exists.
        """
        # Own-send burst takes precedence.
        if (self._own_send_ts is not None and
                now - self._own_send_ts < config.TX_OWN_BURST_MS and
                self._own_chunks):
            adv = self._own_chunks[self._own_cursor % len(self._own_chunks)]
            self._own_cursor += 1
            return adv

        cycle = config.TX_PRESENCE_SLOT_MS + config.TX_GOSSIP_SLOT_MS
        phase = now % cycle
        if phase < config.TX_PRESENCE_SLOT_MS:
            return None  # presence slot
        # Gossip relay slot.
        entry = self._select_relay(now)
        if entry is None:
            return None
        return self._relay_chunk_for(entry, now)

    # -- eviction -----------------------------------------------------------

    def prune(self, now):
        """Drop entries whose TTL has expired."""
        for key in list(self._store.keys()):
            entry = self._store[key]
            if self._ttl_remaining_s(entry, now) <= 0:
                self._store.pop(key, None)
                if entry in self._finalised:
                    self._finalised.remove(entry)
                # If the in-burst own message expired, stop rebroadcasting it.
                # (_own_send_ts is kept so can_send() rate-limiting holds,
                #  but an empty _own_chunks makes tick_tx fall through.)
                if key[0] == self.self_id and key[1] == self._own_msg_id:
                    self._own_chunks = []

    def _enforce_cap(self):
        """LRU by origin_ts: evict smallest-origin_ts over the cap."""
        while len(self._finalised) > self._store_max:
            # smallest origin_ts first; stable on ties.
            victim = min(self._finalised, key=lambda e: e.origin_ts)
            self._finalised.remove(victim)
            self._store.pop((victim.origin_id, victim.msg_id), None)

    # -- readouts -----------------------------------------------------------

    def messages(self, channel=None):
        """Finalised messages, oldest-first, optionally filtered by channel."""
        out = []
        for entry in self._finalised:
            if channel is not None and entry.channel != channel:
                continue
            out.append(DisplayMessage(entry))
        return out

    def store_size(self):
        return len(self._store)