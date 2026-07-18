"""Chat helpers (P2P era).

The server-era HTTP ChatClient lived here; it is gone under the P2P plan (no
laptop server). What remains is a thin re-export of the text/nick hygiene
functions whose canonical home is now chat/codec.py, kept so existing imports
(``from chat import comms; comms.sanitize_text(...)``) and SPEC-CODEC-006's
"chat/comms.py:sanitize_text semantics" reference still resolve.

The radio adapter (RadioBridge wiring GossipEngine <-> Sync) lives in
chat/radio.py and is tested by test_comms.py against a fake Sync (M3).
This module stays intentionally small.
"""

# Re-exported for import compatibility. Canonical implementations live in
# codec.py so the wire format and its hygiene stay in one place.
from .codec import sanitize_text, sanitize_nick  # noqa: F401