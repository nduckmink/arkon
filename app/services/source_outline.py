"""
Source Outline Builder — heading-based TOC tree (PageIndex-inspired).

Parses markdown-style headings (`#`, `##`, ...) from extracted source text and
builds a hierarchical tree. Stored on Source.outline_json at ingest time so the
LLM (and Claude via MCP drill-down tools) can navigate long documents by
structure instead of doing similarity search over chunks.

Char offsets are computed against the concatenated full text (joined with the
same separator the worker uses to build full_text), enabling later page-range
or section-range fetches by Claude.
"""

import re
from typing import Optional

# Markdown ATX-style heading: 1-6 # followed by space + title
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$", re.MULTILINE)

# Joiner used by the worker when concatenating per-page content.
# MUST match worker.py to keep char offsets consistent.
PAGE_JOIN_SEPARATOR = "\n\n"


def assemble_full_text(pages: list[dict]) -> tuple[str, list[int]]:
    """
    Join per-page content into one string and return the char offsets at which
    each page begins. Use the offsets to slice the original page back out via
    `slice_pages_by_range()`.

        offsets[i] = start of pages[i] in the joined string
    """
    parts: list[str] = []
    offsets: list[int] = []
    cursor = 0
    for idx, page in enumerate(pages):
        offsets.append(cursor)
        content = page.get("content") or ""
        parts.append(content)
        cursor += len(content)
        if idx < len(pages) - 1:
            cursor += len(PAGE_JOIN_SEPARATOR)
    return PAGE_JOIN_SEPARATOR.join(parts), offsets


def slice_pages_by_range(
    full_text: str,
    page_offsets: list[int],
    page_numbers: list[int],
) -> list[dict]:
    """
    Return [{"page": int, "content": str}, ...] for a list of 1-based page
    numbers. Out-of-range pages are silently dropped. Page numbers must align
    with the order in which `assemble_full_text` was called (1-based).
    """
    if not page_offsets:
        return []
    total = len(page_offsets)
    out: list[dict] = []
    for pn in page_numbers:
        idx = pn - 1
        if idx < 0 or idx >= total:
            continue
        start = page_offsets[idx]
        end = page_offsets[idx + 1] - len(PAGE_JOIN_SEPARATOR) if idx + 1 < total else len(full_text)
        out.append({"page": pn, "content": full_text[start:end]})
    return out


def parse_page_range(spec: str) -> list[int]:
    """Parse '5-7', '3,8', '12', or combinations into a sorted unique list of 1-based ints."""
    result: set[int] = set()
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            try:
                start, end = int(a), int(b)
            except ValueError:
                continue
            if start <= end:
                result.update(range(start, end + 1))
        else:
            try:
                result.add(int(part))
            except ValueError:
                continue
    return sorted(result)


def build_outline(pages: list[dict]) -> list[dict]:
    """
    Build a TOC tree from a list of {"content": str, "page_number": int} pages.

    Returns a forest (list of top-level nodes). Each node:
        {
          "title": str,
          "level": int,         # 1-6, copied from heading depth
          "page": int,          # page_number where heading was found
          "char_start": int,    # offset into the concatenated full_text
          "char_end": int,      # exclusive — start of next sibling/parent or EOF
          "children": [ ... ],
        }

    If no markdown headings are detected, returns []. Callers should treat an
    empty outline as "structureless document" and fall back to whole-doc reads.
    """
    if not pages:
        return []

    # Walk pages, accumulating char offsets to match the worker's full_text concat.
    flat: list[dict] = []
    cursor = 0
    for idx, page in enumerate(pages):
        content = page.get("content") or ""
        page_num = page.get("page_number") or (idx + 1)
        for match in _HEADING_RE.finditer(content):
            hashes, title = match.group(1), match.group(2).strip()
            if not title:
                continue
            flat.append({
                "title": title,
                "level": len(hashes),
                "page": page_num,
                "char_start": cursor + match.start(),
            })
        cursor += len(content)
        if idx < len(pages) - 1:
            cursor += len(PAGE_JOIN_SEPARATOR)

    if not flat:
        return []

    total_len = cursor

    # Compute char_end as the next heading's start (any level), or total_len.
    for i, node in enumerate(flat):
        node["char_end"] = flat[i + 1]["char_start"] if i + 1 < len(flat) else total_len
        node["children"] = []

    # Build tree by stack: pop while top.level >= node.level.
    roots: list[dict] = []
    stack: list[dict] = []
    for node in flat:
        while stack and stack[-1]["level"] >= node["level"]:
            stack.pop()
        if stack:
            stack[-1]["children"].append(node)
        else:
            roots.append(node)
        stack.append(node)
    return roots


def slice_by_outline_node(
    full_text: str,
    char_start: int,
    char_end: int,
    max_chars: Optional[int] = None,
) -> str:
    """Return the text slice for a given outline node, optionally truncated."""
    text = full_text[char_start:char_end]
    if max_chars is not None and len(text) > max_chars:
        text = text[:max_chars] + "\n\n[…truncated…]"
    return text


def flatten_outline(nodes: list[dict]) -> list[dict]:
    """Walk a tree depth-first; useful for indexed listings and search."""
    out: list[dict] = []

    def _walk(items: list[dict]):
        for n in items:
            out.append({
                "title": n["title"],
                "level": n["level"],
                "page": n.get("page"),
                "char_start": n.get("char_start"),
                "char_end": n.get("char_end"),
            })
            if n.get("children"):
                _walk(n["children"])

    _walk(nodes)
    return out
