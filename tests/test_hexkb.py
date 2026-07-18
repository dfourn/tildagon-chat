#!/usr/bin/env python3
"""Hexpansion keyboard path: typing, shift, emotes, ENTER/ESCAPE suppression.

Drives ChatApp through the stub eventbus exactly the way the keebdexpansion
driver does on the badge: ButtonDownEvent/ButtonUpEvent with Keyboard-group
buttons (letters by uppercase name; ENTER/ESCAPE carry System parents).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import badge_stubs
badge_stubs.install()

from events.input import ButtonDownEvent, ButtonUpEvent
from events.keyboard import KEYBOARD_BUTTONS
from system.eventbus import eventbus

from chat import app as chatapp
from chat import keyboard as kbmod


def press(name):
    """One full key press: Down + Up events, like the driver emits."""
    btn = KEYBOARD_BUTTONS[name]
    eventbus.emit(ButtonDownEvent(btn))
    eventbus.emit(ButtonUpEvent(btn))


def make_app():
    eventbus.handlers = []  # drop the previous test's app registrations
    a = chatapp.ChatApp()
    a.screen = chatapp.SETUP
    a.kb.reset("")
    return a


def test_hex_key_text():
    assert kbmod.hex_key_text("D") == "d"
    assert kbmod.hex_key_text("D", shift=True) == "D"
    assert kbmod.hex_key_text("5") == "5"
    assert kbmod.hex_key_text("!") == "!"
    assert kbmod.hex_key_text("SPACE") == " "
    assert kbmod.hex_key_text("CIRCLE") == ":)"
    assert kbmod.hex_key_text("DIAMOND") == "<3"
    assert kbmod.hex_key_text("SHIFT") is None
    assert kbmod.hex_key_text("TAB") is None


def test_type_nick_and_enter():
    a = make_app()
    for k in ("D", "A", "N"):
        press(k)
    assert a.kb.text == "dan", a.kb.text
    press("ENTER")
    assert a.screen == chatapp.FEED
    assert a.engine.nick == "dan"


def test_shift_uppercase():
    a = make_app()
    press("D")
    eventbus.emit(ButtonDownEvent(KEYBOARD_BUTTONS["SHIFT"]))
    press("A")
    eventbus.emit(ButtonUpEvent(KEYBOARD_BUTTONS["SHIFT"]))
    press("N")
    assert a.kb.text == "dAn", a.kb.text


def test_backspace_and_emotes():
    a = make_app()
    for k in ("H", "I", "X"):
        press(k)
    press("BACKSPACE")
    press("SPACE")
    press("CIRCLE")
    assert a.kb.text == "hi :)", repr(a.kb.text)


def test_typing_in_feed_opens_compose_and_enter_sends():
    a = make_app()
    a.kb.text = "dan"
    a._finish_setup()
    assert a.screen == chatapp.FEED
    press("H")
    assert a.screen == chatapp.COMPOSE
    press("I")
    assert a.kb.text == "hi"
    press("ENTER")
    assert a.screen == chatapp.FEED
    msgs = a.engine.messages(channel=a.channel)
    assert len(msgs) == 1 and msgs[0].text == "hi", [m.text for m in msgs]


def test_enter_suppresses_confirm_tap():
    """The ENTER press must not leak a CONFIRM tap that inserts a T9 char."""
    a = make_app()
    press("D")
    # ENTER down: handler consumes it; the poll path then sees CONFIRM level
    # high (via the System parent) exactly as on hardware.
    eventbus.emit(ButtonDownEvent(KEYBOARD_BUTTONS["ENTER"]))
    a.update(50)   # poll: CONFIRM edge while suppressed
    eventbus.emit(ButtonUpEvent(KEYBOARD_BUTTONS["ENTER"]))
    a.update(50)   # poll: release -> would be a "tap" without suppression
    assert a.screen == chatapp.FEED       # ENTER finished setup
    assert a.engine.nick == "d"
    # No stray T9 preview char leaked into the (reset) composer.
    assert a.kb.text in ("", "d"), repr(a.kb.text)
    # Flag retired once the level dropped: a real CONFIRM tap works again.
    assert not a._suppress_confirm


def test_escape_suppresses_cancel():
    a = make_app()
    a.kb.text = "dan"
    a._finish_setup()
    press("H")
    assert a.screen == chatapp.COMPOSE
    eventbus.emit(ButtonDownEvent(KEYBOARD_BUTTONS["ESCAPE"]))
    a.update(50)
    eventbus.emit(ButtonUpEvent(KEYBOARD_BUTTONS["ESCAPE"]))
    a.update(50)
    # ESCAPE backed out to the feed and did NOT backspace or exit the app.
    assert a.screen == chatapp.FEED
    assert not getattr(a, "_minimised", False)
    assert not a._suppress_cancel


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print("%d passed" % len(tests))
    return 0


if __name__ == "__main__":
    sys.exit(main())
