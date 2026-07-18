#!/usr/bin/env python3
"""Server models: message storage, presence, sanitization, page cap."""
import os
import sys
import tempfile

# chat_server is a sibling of chat/ under emf-new/
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))  # emf-new/
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "chat_server"))

import chat_server.models as models
import chat_server.config as config


def fresh_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = models.connect(path)
    return conn, path


def test_sanitize():
    print("Testing server sanitization...")
    assert models.sanitize_text("hello") == "hello"
    assert models.sanitize_text("cafe") == "cafe"
    assert models.sanitize_text("x" * 999) == "x" * config.MAX_TEXT_LEN
    assert models.sanitize_nick("Dan Rock") == "DanRock"
    assert models.sanitize_nick("") == "anon"
    print("  sanitize OK")


def test_post_and_recent():
    print("Testing post_message + recent_messages ordering...")
    conn, path = fresh_db()
    try:
        r1 = models.post_message(conn, "B1", "jules", "hello world")
        r2 = models.post_message(conn, "B2", "dan", "hi")
        assert r1["id"] < r2["id"], "ids monotonic"
        msgs = models.recent_messages(conn)
        assert [m["id"] for m in msgs] == [r1["id"], r2["id"]], "oldest-first"
        msgs = models.recent_messages(conn, since_id=r1["id"])
        assert [m["id"] for m in msgs] == [r2["id"]]
        assert all(m["text"] == models.sanitize_text(m["text"]) for m in msgs)
    finally:
        conn.close()
        os.remove(path)
    print("  post + recent OK")


def test_page_limit():
    print("Testing MESSAGE_PAGE_LIMIT cap...")
    conn, path = fresh_db()
    try:
        for i in range(config.MESSAGE_PAGE_LIMIT + 10):
            models.post_message(conn, "B", "x", "m%d" % i)
        msgs = models.recent_messages(conn)
        assert len(msgs) == config.MESSAGE_PAGE_LIMIT
    finally:
        conn.close()
        os.remove(path)
    print("  page limit OK (%d)" % config.MESSAGE_PAGE_LIMIT)


def test_presence():
    print("Testing presence heartbeat + online_count...")
    conn, path = fresh_db()
    try:
        assert models.online_count(conn) == 0
        models.heartbeat(conn, "B1", "jules")
        models.heartbeat(conn, "B2", "dan")
        assert models.online_count(conn) == 2
        nicks = models.online_nicks(conn)
        assert set(nicks) == {"jules", "dan"}
        # heartbeat again updates ts, doesn't add a row
        models.heartbeat(conn, "B1", "jules")
        assert models.online_count(conn) == 2
    finally:
        conn.close()
        os.remove(path)
    print("  presence OK")


if __name__ == "__main__":
    test_sanitize()
    test_post_and_recent()
    test_page_limit()
    test_presence()
    print("\nALL SERVER MODEL TESTS PASSED")