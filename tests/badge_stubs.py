"""Injects fake badge modules so `chat` imports under plain CPython.

Same technique as infection/tests/badge_stubs.py: synthesise the `app`,
`events.input`, `events.keyboard`, and `system.eventbus` modules so importing
chat.app works without the firmware. Button/event semantics mirror
modules/events/input.py (group + parent chain, equality by (name, group)) so
the hexpansion-keyboard path is host-testable.
"""

import os
import sys
import types


def install():
    tests_dir = os.path.dirname(os.path.abspath(__file__))
    chat_dir = os.path.dirname(tests_dir)
    repo_root = os.path.dirname(chat_dir)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    if "app" not in sys.modules:
        _app = types.ModuleType("app")

        class App:
            def __init__(self):
                self.overlays = []

            def minimise(self):
                self._minimised = True

            def draw_overlays(self, ctx):
                pass

        _app.App = App
        sys.modules["app"] = _app

    if "system" not in sys.modules:
        _system = types.ModuleType("system")
        _system.__path__ = []
        _eventbus_mod = types.ModuleType("system.eventbus")

        class _EventBus:
            """Synchronous, focus-less mini bus: enough for handler tests."""

            def __init__(self):
                self.handlers = []  # (event_type, handler)

            def on(self, event_type, handler, app):
                self.handlers.append((event_type, handler))

            def remove(self, event_type, handler, app):
                self.handlers = [
                    (t, h) for (t, h) in self.handlers
                    if not (t is event_type and h is handler)
                ]

            def deregister(self, app):
                pass

            def emit(self, event):
                for event_type, handler in tuple(self.handlers):
                    if isinstance(event, event_type):
                        handler(event)

        _eventbus_mod.eventbus = _EventBus()
        _system.eventbus = _eventbus_mod
        sys.modules["system"] = _system
        sys.modules["system.eventbus"] = _eventbus_mod

    if "events" not in sys.modules:
        _events = types.ModuleType("events")
        _events.__path__ = []

        class Button:
            """Mirrors firmware Button: (name, group) identity + parent chain."""

            def __init__(self, name, group="System", parent=None):
                self.name = name
                self.group = group
                self.parent = parent

            def __hash__(self):
                return hash((self.name, self.group))

            def __eq__(self, other):
                return self.name == other.name and self.group == other.group

            def __contains__(self, other):
                if other == self:
                    return True
                parent = self.parent
                while parent is not None:
                    if other == parent:
                        return True
                    parent = parent.parent
                return False

            def find_parent_in_group(self, group):
                if self.group == group:
                    return self
                parent = self.parent
                while parent is not None:
                    if parent.group == group:
                        return parent
                    parent = parent.parent
                return None

        _input = types.ModuleType("events.input")
        _input.Button = Button
        _input.BUTTON_TYPES = {
            n: Button(n, "System")
            for n in ("UP", "DOWN", "LEFT", "RIGHT", "CONFIRM", "CANCEL",
                      "UNDEFINED")
        }

        class ButtonDownEvent:
            def __init__(self, button):
                self.button = button

        class ButtonUpEvent:
            def __init__(self, button):
                self.button = button

        _input.ButtonDownEvent = ButtonDownEvent
        _input.ButtonUpEvent = ButtonUpEvent

        class Buttons:
            """Level tracker fed by the stub eventbus, like the firmware's."""

            def __init__(self, app):
                self.buttons = {}
                from system.eventbus import eventbus
                eventbus.on(ButtonDownEvent, self._down, app)
                eventbus.on(ButtonUpEvent, self._up, app)

            def _down(self, event):
                self.buttons[event.button] = True

            def _up(self, event):
                self.buttons[event.button] = False

            def get(self, button, default=None):
                return any(
                    value for (b, value) in self.buttons.items()
                    if b == button or button in b
                )

        _input.Buttons = Buttons
        _events.input = _input
        sys.modules["events"] = _events
        sys.modules["events.input"] = _input

        # events.keyboard: same shape as the firmware module (letters/digits/
        # symbols by name; modifiers with System parents where applicable).
        _kb = types.ModuleType("events.keyboard")
        BT = _input.BUTTON_TYPES
        letters = {c: Button(c, "Keyboard")
                   for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"}
        numbers = {c: Button(c, "Keyboard") for c in "0123456789"}
        symbols = {c: Button(c, "Keyboard")
                   for c in """!"#$%&\'()*+,-./:;<=>?@[\\]^_`{|}~"""}
        modifiers = {
            "SPACE": Button("SPACE", "Keyboard"),
            "SHIFT": Button("SHIFT", "Keyboard"),
            "CTRL": Button("CTRL", "Keyboard"),
            "ALT": Button("ALT", "Keyboard"),
            "ESCAPE": Button("ESCAPE", "Keyboard", BT["CANCEL"]),
            "BACKSPACE": Button("BACKSPACE", "Keyboard"),
            "ENTER": Button("ENTER", "Keyboard", BT["CONFIRM"]),
            "UP": Button("UP", "Keyboard", BT["UP"]),
            "DOWN": Button("DOWN", "Keyboard", BT["DOWN"]),
            "LEFT": Button("LEFT", "Keyboard", BT["LEFT"]),
            "RIGHT": Button("RIGHT", "Keyboard", BT["RIGHT"]),
        }
        kb_buttons = {}
        for d in (letters, numbers, symbols, modifiers):
            kb_buttons.update(d)
        # keebdex icon keys (no System parents)
        for icon in ("SQUARE", "TRIANGLE", "CROSS", "CIRCLE", "CLOUD",
                     "DIAMOND"):
            kb_buttons[icon] = Button(icon, "Keyboard")
        _kb.KEYBOARD_BUTTONS = kb_buttons
        _events.keyboard = _kb
        sys.modules["events.keyboard"] = _kb
