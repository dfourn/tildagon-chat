#!/usr/bin/env python3
"""Firmware floor for the BLE radio: fw_allows_ble over shipped version strings.

SPEC-FW-001..002 (M1). ble.BLE().active(True) hard-HANGS Tildagon firmware
older than v2.0.0-alpha.3, so we refuse the radio on old/unknown firmware.
Pure string predicate, host-tested (ported from twin_flame's coverage).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import badge_stubs
badge_stubs.install()

from chat import hw


def test_fw_floor_rejects_old():
    """SPEC-FW-001: firmware below v2.0.0-alpha.3 -> False."""
    print("Testing fw_allows_ble rejects old/unknown firmware...")
    for v in ("v1.6.0", "v2.0.0-alpha.2", "v1.11.3", "", "garbage", None):
        assert hw.fw_allows_ble(v) is False, \
            "expected False for %r, got True" % v
    assert hw.fw_allows_ble("v2.0.0-alpha.0") is False
    assert hw.fw_allows_ble("v2.0.0-alpha.1") is False
    assert hw.fw_allows_ble("v2.0.0-alpha.foo") is False
    assert hw.fw_allows_ble("v2.0.0.0") is True
    print("  rejects-old OK")


def test_fw_floor_accepts_new():
    """SPEC-FW-002: v2.0.0-alpha.3 and newer -> True."""
    print("Testing fw_allows_ble accepts new firmware...")
    for v in ("v2.0.0-alpha.3", "v2.0.0-alpha.6", "v2.0.0",
              "v2.1.0", "v3.0.0-rc.1"):
        assert hw.fw_allows_ble(v) is True, \
            "expected True for %r, got False" % v
    assert hw.fw_allows_ble("v2.0.0-beta.1") is True
    assert hw.fw_allows_ble("v2.0.0-rc.1") is True
    assert hw.fw_allows_ble("v2.0.1") is True
    assert hw.fw_allows_ble("v2.0.1-alpha.0") is True
    assert hw.fw_allows_ble("v2.5.3") is True
    assert hw.fw_allows_ble("V2.1.0") is True
    print("  accepts-new OK")


if __name__ == "__main__":
    test_fw_floor_rejects_old()
    test_fw_floor_accepts_new()
    print("\nALL FIRMWARE FLOOR TESTS PASSED")
