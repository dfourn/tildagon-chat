#!/usr/bin/env python3
"""Smoke test: instantiate ChatApp and run update()/draw() cycles.

Surfaces crashes in the app lifecycle that the pure-logic tests never exercise.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import badge_stubs
badge_stubs.install()

from chat import app as chatapp


class FakeCtx:
    """Minimal stand-in for the badge's 2D draw context.

    Every chained method returns self so the app's draw path (which relies on
    ctx.rgb(...).rectangle(...).fill() chaining) works.
    """
    _fs = 0
    _ta = "left"

    # Alignment constants: ints on the badge binding, strings in the sim fake.
    # Ints here to match the stricter (hardware) contract.
    START = 0
    END = 1
    CENTER = 2
    LEFT = 3
    RIGHT = 4

    def __getattribute__(self, name):
        # Everything that isn't explicitly defined below returns self so the
        # call chain keeps going.
        attr = object.__getattribute__(self, name)
        return attr

    def save(self):
        return self

    def restore(self):
        return self

    def rgb(self, *a, **k):
        return self

    def rectangle(self, *a, **k):
        return self

    def fill(self, *a, **k):
        return self

    def text(self, *a, **k):
        return self

    def move_to(self, *a, **k):
        return self

    @property
    def font_size(self):
        return self._fs

    @font_size.setter
    def font_size(self, v):
        self._fs = v

    @property
    def text_align(self):
        return self._ta

    @text_align.setter
    def text_align(self, v):
        self._ta = v


def main():
    print("Instantiating ChatApp...")
    a = chatapp.ChatApp()
    ctx = FakeCtx()
    print("  initial screen:", a.screen)

    # Run several update + draw cycles across screens.
    print("Running update/draw cycles...")
    for i in range(50):
        try:
            a.update(delta=100)
            a.draw(ctx)
        except Exception as e:
            print("CRASH at frame %d (screen=%s): %r" % (i, a.screen, e))
            import traceback
            traceback.print_exc()
            return 1

        # Drive the app through screens.
        if i == 3 and a.screen == "setup":
            # simulate finishing setup
            a.kb.text = "dan"
            a._finish_setup()
        if i == 10 and a.screen == "feed":
            a.screen = "compose"
            a.kb.reset("")
        if i == 20 and a.screen == "compose":
            a.kb.text = "hi"
            a._send_current()
    print("  no crash over 50 frames")
    return 0


if __name__ == "__main__":
    sys.exit(main())