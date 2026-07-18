#!/usr/bin/env python3
"""Keyboard: cluster nav, letter cycling, commit/submit/clear, hold timing."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import badge_stubs
badge_stubs.install()

from chat import keyboard as kb
from chat import config


def test_clusters_complete():
    """Every letter A-Z plus basic punctuation appears in CLUSTERS."""
    joined = "".join(kb.CLUSTERS)
    for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ ":
        assert ch in joined, "missing %r in CLUSTERS" % ch


def test_navigation():
    print("Testing cluster/letter navigation...")
    k = kb.KeyboardState(max_len=40)
    assert k.cluster_idx == 5, "starts on space cluster"
    k.down()
    assert k.cluster_idx == 6
    k.up()
    assert k.cluster_idx == 5
    k.up()
    assert k.cluster_idx == 4
    # wrap around: from idx 4, twelve downs returns to 4
    for _ in range(len(kb.CLUSTERS)):
        k.down()
    assert k.cluster_idx == 4, "down wraps modulo cluster count"
    # letter cycling within a cluster
    k.cluster_idx = 0  # ABC
    assert k.current_char() == "A"
    k.right()
    assert k.current_char() == "B"
    k.right()
    assert k.current_char() == "C"
    k.right()
    assert k.current_char() == "A", "letter wraps within cluster"
    k.left()
    assert k.current_char() == "C"
    print("  navigation OK")


def test_commit_and_cap():
    print("Testing commit + max_len cap...")
    k = kb.KeyboardState(max_len=3)
    k.cluster_idx = 0  # ABC
    k.confirm_tap()   # A
    k.right(); k.confirm_tap()  # B
    k.right(); k.confirm_tap()  # C
    assert k.text == "ABC", "got %r" % k.text
    k.confirm_tap()   # capped
    assert k.text == "ABC", "max_len respected: %r" % k.text
    print("  commit + cap OK")


def test_submit_and_clear():
    print("Testing submit (hold) and clear (hold)...")
    k = kb.KeyboardState(max_len=40)
    k.cluster_idx = 0
    k.confirm_tap()
    k.right(); k.confirm_tap()
    assert k.text == "AB"
    assert not k.submitted
    k.confirm_hold()
    assert k.submitted, "confirm_hold sets submitted"
    assert k.text == "AB", "confirm_hold commits pending char then submits"
    k.reset()
    assert not k.submitted and k.text == ""
    # reset() returns the cursor to the space cluster; reselect ABC.
    k.cluster_idx = 0
    # cancel hold clears
    k.confirm_tap(); k.confirm_tap()
    assert k.text == "AA"
    k.cancel_hold()
    assert k.text == "", "cancel_hold clears all"
    print("  submit/clear OK")


def test_backspace():
    print("Testing backspace (cancel tap)...")
    k = kb.KeyboardState(max_len=40)
    k.cluster_idx = 0
    k.confirm_tap(); k.confirm_tap()
    assert k.text == "AA"
    k.cancel_tap()
    assert k.text == "A"
    k.cancel_tap()
    assert k.text == ""
    k.cancel_tap()  # no-op on empty
    assert k.text == ""
    print("  backspace OK")


def test_hold_tracker():
    print("Testing HoldTracker tap vs hold...")
    ht = kb.HoldTracker(hold_ms=600)
    assert not ht.held(0)
    ht.press(100)
    assert not ht.held(400), "under threshold not held"
    assert ht.held(700), ">= threshold fires once"
    assert not ht.held(800), "fires only once"
    assert ht.release(900) is None, "already fired via held()"

    ht2 = kb.HoldTracker(hold_ms=600)
    ht2.press(0)
    assert ht2.release(300) == "tap", "short release is tap"
    ht2.press(0)
    assert ht2.release(700) == "hold", "long release is hold"
    assert ht2.release(800) is None, "no double-fire on release"
    print("  hold tracker OK")


if __name__ == "__main__":
    test_clusters_complete()
    test_navigation()
    test_commit_and_cap()
    test_submit_and_clear()
    test_backspace()
    test_hold_tracker()
    print("\nALL KEYBOARD TESTS PASSED")