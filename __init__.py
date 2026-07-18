"""Chat 2026 package (P2P).

Re-exports ``ChatApp`` so the badge launcher can resolve
``"callable": "ChatApp"`` from metadata.json by importing the ``chat`` package
-- the same contract twin_flame and infection use (``from .app import <App>``).

Note: this re-export is convention, not the launcher's actual path -- the
launcher imports ``apps.<dir>.app`` directly, so an empty ``__init__`` never
crashed launches. The real 2026-07-18 launch crash was hardware-only:
``ctx.text_align = "left"`` string assignments in app.py's draw path
(TypeError on the badge's int-constant uctx binding; the sim fake and test
stubs accept strings, which is why everything stayed green off-badge).
Fixed by using the ``ctx.LEFT/RIGHT/CENTER`` constants.
"""
from .app import ChatApp

__all__ = ["ChatApp"]
