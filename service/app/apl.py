"""APL documents for Echo Show screens.

Hard-won detail (measured on real Echo Show hardware): **`datasources` bindings
do not reach this device.** A document using ``${payload.text}`` renders only the
root ``Frame`` and nothing inside — dark background, no text, no image — while a
literal ``"LITERAL OK"`` in the same document renders fine.

So every document is built per message with the values already inlined and no
``datasources`` are sent at all. A few hundred extra bytes, zero surprises.

Kept deliberately boring: no ``resources``, no ``styles``, no ``${}`` anywhere.
The same applies to ``alexa-layouts`` imports: they add a resolution step that
can silently fail, and primitive frames render everywhere.

Sizing note: many Echo Shows report **960x480** (HUB profile), not 1280x800.
Laying out for the bigger canvas overflows and clips the text, which is what the
first version of this file got wrong.
"""
from __future__ import annotations

import re
from typing import Any

from .config import ART_IMAGE, BRAND_TITLE, PUBLIC_BASE

APL_VERSION = "1.6"

# (max characters, font size) — tuned for a 960-wide viewport.
_TIERS = ((70, 28), (150, 24), (260, 20))
_MAX_CHARS = 300


def _literal(text: str) -> str:
    """Defuse APL expression syntax in data we did not write.

    The device evaluates ``${…}`` (and ``#{…}``) inside *any* string property,
    so a message such as "${HOME} is full" turns into the same broken binding
    this module exists to avoid. A space keeps the words and kills the syntax.
    """
    return str(text).replace("${", "$ {").replace("#{", "# {")


def screen_text(text: str) -> str:
    """Strip audio tags and clamp the line so the layout never overflows.

    The same string goes to the TTS provider (which needs ``[excited]`` etc.) and
    to APL (which would draw the brackets literally), so the screen gets a
    cleaned copy.
    """
    clean = re.sub(r"\[[^\]]{1,40}\]", "", _literal(text))
    clean = re.sub(r"\s+", " ", clean).strip()
    if len(clean) > _MAX_CHARS:
        cut = clean[:_MAX_CHARS]
        stop = cut.rfind(". ")
        clean = (cut[: stop + 1] if stop > _MAX_CHARS * 0.6 else cut + "…").strip()
    return clean


def _font_size(text: str) -> int:
    for limit, value in _TIERS:
        if len(text) <= limit:
            return value
    return _TIERS[-1][1]


def _art(art: str | None) -> str | None:
    if art:
        return art
    if ART_IMAGE:
        return ART_IMAGE
    return None


def build_document(
    text: str,
    *,
    title: str = "",
    subtitle: str = "",
    art: str | None = None,
) -> dict[str, Any]:
    """Inline every value: no resources, no styles, no datasources."""
    line = screen_text(text)
    body_size = _font_size(line)
    header = _literal(title or BRAND_TITLE)
    subtitle = _literal(subtitle)
    image = _art(art)

    column_items: list[dict[str, Any]] = [
        {"type": "Text", "text": header, "fontSize": 18,
         "color": "#FF8FC7", "fontWeight": "900"},
        {"type": "Text", "text": line, "fontSize": body_size, "color": "#FFFFFF",
         "paddingTop": 8, "maxLines": 4, "textOverflow": "ellipsis"},
    ]
    if subtitle:
        column_items.append({"type": "Text", "text": subtitle, "fontSize": 16,
                             "color": "#9A97A6", "paddingTop": 12})

    row_items: list[dict[str, Any]] = []
    if image:
        row_items.append({"type": "Image", "source": image, "width": 200,
                          "height": 200, "borderRadius": 20, "scale": "best-fill"})
    row_items.append({
        "type": "Container", "direction": "column",
        "width": 600 if image else 860,
        "paddingLeft": 32 if image else 0,
        "items": column_items,
    })

    return {
        "type": "APL",
        "version": APL_VERSION,
        "theme": "dark",
        "mainTemplate": {
            "items": [{
                "type": "Frame",
                "width": "100%",
                "height": "100%",
                "backgroundColor": "#0E0C13",
                "items": [{
                    "type": "Container",
                    "direction": "row",
                    "alignItems": "center",
                    "width": "100%",
                    "height": "100%",
                    "paddingLeft": 40,
                    "paddingRight": 40,
                    "items": row_items,
                }],
            }],
        },
    }


def _action_button(label: str, action: str, accent: bool = False) -> dict[str, Any]:
    """Text button that sends an APL UserEvent back to the skill."""
    bg = "#FF8FC7" if accent else "#241E2E"
    fg = "#12101A" if accent else "#FFFFFF"
    return {
        "type": "TouchWrapper",
        "onPress": [{"type": "SendEvent", "arguments": [action]}],
        "items": [{
            "type": "Frame",
            "width": 128,
            "height": 64,
            "borderRadius": 32,
            "backgroundColor": bg,
            "items": [{
                "type": "Text", "text": label, "fontSize": 22, "color": fg,
                "fontWeight": "700", "textAlign": "center",
                "width": "100%", "height": "100%",
            }],
        }],
    }


def now_playing_document(
    track: str,
    *,
    artist: str = "",
    art: str | None = None,
    progress: float = 0.0,
    elapsed: str = "",
    duration: str = "",
) -> dict[str, Any]:
    """Now Playing screen: cover, title, progress bar and tappable controls.

    Note: while AudioPlayer is running the device usually swaps to its own native
    player and replaces this screen. Kept because some devices keep it, and
    because the tap handlers still work when it is visible.
    """
    pct = max(0, min(100, progress))
    time_line = f"{elapsed} / {duration}" if elapsed and duration else ""
    track, artist = _literal(track), _literal(artist)
    image = _art(art)
    return {
        "type": "APL",
        "version": APL_VERSION,
        "theme": "dark",
        "mainTemplate": {
            "items": [{
                "type": "Frame",
                "width": "100%",
                "height": "100%",
                "backgroundColor": "#0E0C13",
                "items": [{
                    "type": "Container",
                    "direction": "row",
                    "alignItems": "center",
                    "width": "100%",
                    "height": "100%",
                    "paddingLeft": 56,
                    "paddingRight": 56,
                    "items": [
                        *([{ "type": "Image", "source": image, "width": 300,
                             "height": 300, "borderRadius": 24, "scale": "best-fill"}]
                          if image else []),
                        {
                            "type": "Container",
                            "direction": "column",
                            "width": 620,
                            "paddingLeft": 44 if image else 0,
                            "items": [
                                {"type": "Text", "text": "NOW PLAYING", "fontSize": 20,
                                 "color": "#FF8FC7", "fontWeight": "900"},
                                {"type": "Text", "text": track, "fontSize": 34,
                                 "color": "#FFFFFF", "paddingTop": 10,
                                 "maxLines": 2, "textOverflow": "ellipsis"},
                                {"type": "Text", "text": artist, "fontSize": 22,
                                 "color": "#9A97A6", "paddingTop": 6},
                                {"type": "Frame", "width": 520, "height": 10,
                                 "borderRadius": 5, "backgroundColor": "#2A2433",
                                 "paddingTop": 26,
                                 "items": [{"type": "Frame", "width": f"{pct}%",
                                            "height": 10, "borderRadius": 5,
                                            "backgroundColor": "#FF8FC7"}]},
                                {"type": "Text", "text": time_line, "fontSize": 18,
                                 "color": "#9A97A6", "paddingTop": 8},
                                {"type": "Container", "direction": "row",
                                 "paddingTop": 22,
                                 "items": [
                                     _action_button("Prev", "previous"),
                                     {"type": "Frame", "width": 16},
                                     _action_button("Pause", "pause", accent=True),
                                     {"type": "Frame", "width": 16},
                                     _action_button("Next", "next"),
                                 ]},
                            ],
                        },
                    ],
                }],
            }],
        },
    }


def now_playing_directive(track: str, **kwargs: Any) -> dict[str, Any]:
    return {
        "type": "Alexa.Presentation.APL.RenderDocument",
        "token": "nowPlaying",
        "version": "1.0",
        "document": now_playing_document(track, **kwargs),
    }


def gallery_document(items: list[dict[str, Any]], *, title: str = "GALLERY",
                     subtitle: str = "") -> dict[str, Any]:
    """Grid of thumbnails. Each tile sends a tap event with its index."""
    row: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        if index:
            row.append({"type": "Frame", "width": 24})
        row.append({
            "type": "TouchWrapper",
            "onPress": [{"type": "SendEvent", "arguments": ["open", index]}],
            "items": [{
                "type": "Container",
                "direction": "column",
                "width": 200,
                "items": [
                    {"type": "Image", "source": item["art"], "width": 200,
                     "height": 150, "borderRadius": 12, "scale": "best-fill"},
                    {"type": "Text", "text": _literal(item.get("title", "")), "fontSize": 14,
                     "color": "#B9B6C4", "paddingTop": 6,
                     "maxLines": 1, "textOverflow": "ellipsis"},
                ],
            }],
        })

    title, subtitle = _literal(title), _literal(subtitle)
    subtitle_items: list[dict[str, Any]] = []
    if subtitle:
        subtitle_items.append({"type": "Text", "text": subtitle, "fontSize": 14,
                               "color": "#9A97A6", "paddingTop": 4})

    return {
        "type": "APL",
        "version": APL_VERSION,
        "theme": "dark",
        "mainTemplate": {
            "items": [{
                "type": "Frame",
                "width": "100%",
                "height": "100%",
                "backgroundColor": "#0E0C13",
                "items": [{
                    "type": "Container",
                    "direction": "column",
                    "width": "100%",
                    "height": "100%",
                    "paddingLeft": 32,
                    "paddingRight": 32,
                    "paddingTop": 20,
                    "items": [
                        {"type": "Text", "text": title, "fontSize": 18,
                         "color": "#FF8FC7", "fontWeight": "900"},
                        *subtitle_items,
                        {"type": "Container", "direction": "row",
                         "paddingTop": 18, "items": row},
                    ],
                }],
            }],
        },
    }


def gallery_directive(items: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
    return {
        "type": "Alexa.Presentation.APL.RenderDocument",
        "token": "gallery",
        "version": "1.0",
        "document": gallery_document(items, **kwargs),
    }


def render_directive(
    text: str,
    *,
    title: str = "",
    subtitle: str = "",
    art: str | None = None,
) -> dict[str, Any]:
    """Build the RenderDocument directive with the values inlined."""
    return {
        "type": "Alexa.Presentation.APL.RenderDocument",
        "token": "main",
        "version": "1.0",
        "document": build_document(text, title=title, subtitle=subtitle, art=art),
    }
