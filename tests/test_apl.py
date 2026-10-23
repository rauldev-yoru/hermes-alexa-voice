"""APL documents: everything inlined, nothing the Echo Show silently drops."""
from __future__ import annotations

import json

from app import apl

NUMERIC = {"fontSize", "lineHeight", "letterSpacing", "paddingTop", "paddingLeft",
           "paddingRight", "borderRadius"}


def _walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def _all_documents():
    return [
        apl.render_directive("hello ${payload.text}", title="T", subtitle="S",
                             art="https://example.test/a.png"),
        apl.now_playing_directive("track", artist="a", progress=140, elapsed="1:00",
                                  duration="2:00", art="https://example.test/a.png"),
        apl.gallery_directive([{"art": "https://example.test/1.jpg", "title": "one"}],
                              title="G", subtitle="1 newest"),
    ]


def test_no_bindings_datasources_or_resources():
    for directive in _all_documents():
        assert "datasources" not in directive
        doc = directive["document"]
        for key in ("resources", "styles", "import", "parameters"):
            assert key not in doc
            assert key not in doc["mainTemplate"]


def test_no_binding_syntax_survives_from_text():
    # Message text like "${HOME} is full" is evaluated as an APL expression by
    # the device — the exact failure PITFALLS.md #3 describes.
    for directive in _all_documents():
        assert "${" not in json.dumps(directive)
    doc = apl.build_document("${HOME} full", title="${t}", subtitle="#{s}")
    dumped = json.dumps(doc)
    assert "${" not in dumped and "#{" not in dumped
    texts = [n["text"] for n in _walk(doc) if n.get("type") == "Text"]
    assert any("HOME" in t for t in texts)  # the words are kept, only defused
    gallery = apl.gallery_document([{"art": "https://e.test/1.jpg", "title": "${x}"}],
                                   title="${g}", subtitle="${s}")
    assert "${" not in json.dumps(gallery)
    playing = apl.now_playing_document("${t}", artist="${a}")
    assert "${" not in json.dumps(playing)


def test_numeric_properties_are_numbers():
    for directive in _all_documents():
        for node in _walk(directive["document"]):
            for key in NUMERIC & node.keys():
                assert isinstance(node[key], (int, float)), (key, node[key])


def test_audio_tags_are_stripped_for_the_screen():
    assert apl.screen_text("[excited] Done! [laughs] all  good") == "Done! all good"


def test_long_text_is_clamped():
    text = "word " * 200
    out = apl.screen_text(text)
    assert len(out) <= apl._MAX_CHARS + 1
    assert out.endswith("…")


def test_clamp_prefers_sentence_boundary():
    text = ("A" * 250) + ". " + ("b" * 200)
    out = apl.screen_text(text)
    assert out.endswith(".")
    assert "b" not in out


def test_font_size_shrinks_with_length():
    assert apl._font_size("x" * 10) > apl._font_size("x" * 200)


def test_progress_is_clamped():
    def bar(progress):
        doc = apl.now_playing_document("t", progress=progress)
        return next(n["width"] for n in _walk(doc)
                    if n.get("type") == "Frame" and n.get("height") == 10
                    and n.get("backgroundColor") == "#FF8FC7")

    assert bar(250) == "100%"
    assert bar(-5) == "0%"
    assert bar(42) == "42%"
