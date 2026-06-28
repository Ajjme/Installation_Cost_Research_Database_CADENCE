"""Rule-based roofing material classification.

The classifier assigns each product to one of a small controlled set of
``material_class`` values using a deterministic, ordered set of keyword rules.
Rules are intentionally simple and auditable -- order matters, because the
first matching rule wins.

Public API:
    classify_text(text) -> str
    classify_product(row) -> str
    MATERIAL_CLASSES (the controlled vocabulary)
"""

from __future__ import annotations

from typing import Any, Mapping

# Controlled vocabulary of allowed material classes.
MATERIAL_CLASSES = (
    "asphalt_3_tab",
    "asphalt_architectural",
    "asphalt_premium_architectural",
    "metal_corrugated_panel",
    "metal_standing_seam",
    "tile_clay",
    "tile_concrete",
    "tile_unspecified",
    "unclassified",
)

# Fields (in priority order) that are concatenated to form the text used for
# classification. Missing fields are skipped.
CLASSIFICATION_FIELDS = (
    "product_name",
    "brand",
    "material",
    "product_type",
    "breadcrumb",
    "specifications",
)

_PREMIUM_TERMS = (
    "premium",
    "designer",
    "luxury",
    "grand manor",
    "camelot",
    "presidential",
    "berkshire",
    "impact resistant",
    "impact-resistant",
    "class 4",
)

_ARCHITECTURAL_TERMS = (
    "architectural",
    "dimensional",
    "laminated",
    "timberline",
    "oakridge",
    "duration",
)

_METAL_WORDS = ("metal", "steel", "galvalume", "aluminum")
_CORRUGATED_TERMS = ("corrugated", "ribbed", "exposed fastener")


def _normalize(text: Any) -> str:
    """Lowercase and collapse whitespace; tolerate non-string input."""
    if text is None:
        return ""
    return " ".join(str(text).lower().split())


def classify_text(text: Any) -> str:
    """Classify a single blob of text into a material class.

    Rules are evaluated in priority order; the first match wins.
    """
    t = _normalize(text)
    if not t:
        return "unclassified"

    # Asphalt: 3-tab is the most specific asphalt signal.
    if "3-tab" in t or "3 tab" in t or "three tab" in t:
        return "asphalt_3_tab"

    # Premium architectural asphalt (checked before plain architectural).
    if any(term in t for term in _PREMIUM_TERMS) and (
        "shingle" in t or any(a in t for a in _ARCHITECTURAL_TERMS)
    ):
        return "asphalt_premium_architectural"

    # Standalone "impact resistant" / "class 4" shingles are premium even
    # without other architectural cues.
    if ("impact resistant" in t or "impact-resistant" in t or "class 4" in t) and "shingle" in t:
        return "asphalt_premium_architectural"

    if any(term in t for term in _ARCHITECTURAL_TERMS):
        return "asphalt_architectural"

    # Metal.
    if "standing seam" in t:
        return "metal_standing_seam"

    if any(term in t for term in _CORRUGATED_TERMS) and any(w in t for w in _METAL_WORDS):
        return "metal_corrugated_panel"

    # Tile.
    if "clay" in t or "terra cotta" in t or "terracotta" in t:
        return "tile_clay"

    if "concrete" in t and "tile" in t:
        return "tile_concrete"

    if "tile" in t:
        return "tile_unspecified"

    return "unclassified"


def classify_product(row: Mapping[str, Any]) -> str:
    """Classify a product row (mapping) into a material class.

    Concatenates the configured classification fields and delegates to
    :func:`classify_text`. Safe against missing keys and non-string values.
    """
    if row is None:
        return "unclassified"

    parts = []
    for field in CLASSIFICATION_FIELDS:
        value = row.get(field) if hasattr(row, "get") else None
        norm = _normalize(value)
        if norm:
            parts.append(norm)

    return classify_text(" ".join(parts))
