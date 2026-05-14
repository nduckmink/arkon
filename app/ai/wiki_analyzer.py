"""
Pre-analysis step for the wiki mini-agent.

analyze_source() makes a single cheap LLM call before the agent loop to
produce a "map" — what entities/concepts the source contains, which existing
wiki pages should be updated, what new pages to create. The agent receives
this map in its initial user message and uses it as a starting point.

Design notes:
- Runs as a plain llm.generate() call (no tools), temperature=0.1, max 2048 tokens.
- full_text is capped at ANALYSIS_CHARS to keep cost low. The agent still reads
  the full document via read_source_excerpt during the generation phase.
- Every exception is caught and returns None so the caller can fall back to
  running the agent without pre-analysis — this step must never block ingestion.
- existing_pages are passed in so the LLM can reference actual slugs rather than
  hallucinating them.
"""

import json
import re
from typing import Optional

from loguru import logger

from app.ai.providers.base import LLMProvider

ANALYSIS_CHARS = 30_000

ANALYSIS_SYSTEM = """\
You are a document analyst. Your job is to read a source document and produce
a structured JSON analysis that a wiki compiler agent will use as a starting map.

Return ONLY valid JSON — no explanation, no markdown fences, no extra text.
If you are uncertain about a field, use an empty list or "other".
"""

ANALYSIS_PROMPT_TEMPLATE = """\
## Knowledge base context
Knowledge type: {kt_name}
Description: {kt_desc}

## Existing wiki pages (slug → title)
{existing_pages_list}

## Source document title
{source_title}

## Source document (first {char_count} chars)
{source_excerpt}

---

Analyze the document above and return a JSON object with exactly these fields:

{{
  "document_type": "<regulation|sop|report|technical_spec|other>",
  "primary_language": "<vi|en|...>",
  "key_themes": ["<theme>", ...],
  "named_entities": [
    {{"name": "...", "type": "<person|org|product|regulation>", "significance": "..."}}
  ],
  "key_concepts": [
    {{"name": "...", "suggested_slug": "concept/...", "description": "..."}}
  ],
  "existing_pages_to_update": [
    {{"slug": "<must be from the existing pages list above>", "reason": "..."}}
  ],
  "new_pages_to_create": [
    {{"suggested_slug": "...", "page_type": "<entity|concept|topic|source>", "title": "..."}}
  ],
  "source_page_slug": "source/<short-slug>",
  "compilation_notes": "<any important note for the compiler agent>"
}}

Rules:
- existing_pages_to_update MUST only reference slugs from the provided existing pages list.
- new_pages_to_create should NOT duplicate existing slugs.
- Keep lists concise — top 5-10 items max per list.
- Return raw JSON only.
"""


async def analyze_source(
    llm: LLMProvider,
    source_title: str,
    full_text: str,
    existing_pages: list[dict],
    kt_name: Optional[str],
    kt_desc: Optional[str],
) -> Optional[dict]:
    """
    Run a single-shot LLM analysis of a source document.

    Returns a dict with the analysis results, or None if analysis failed.
    Caller should always handle None and proceed with the agent unchanged.

    Args:
        llm: Configured LLM provider instance.
        source_title: Human-readable source title.
        full_text: Full document text (will be capped at ANALYSIS_CHARS internally).
        existing_pages: List of dicts with keys: slug, title, page_type.
        kt_name: Knowledge type name, or None.
        kt_desc: Knowledge type description, or None.
    """
    try:
        excerpt = full_text[:ANALYSIS_CHARS]

        existing_lines = "\n".join(
            f"  - {p['slug']}: {p.get('title', '')}" for p in existing_pages[:200]
        ) or "  (none)"

        prompt = ANALYSIS_PROMPT_TEMPLATE.format(
            kt_name=kt_name or "(none)",
            kt_desc=kt_desc or "(none)",
            existing_pages_list=existing_lines,
            source_title=source_title,
            char_count=len(excerpt),
            source_excerpt=excerpt,
        )

        raw = await llm.generate(
            prompt=prompt,
            system=ANALYSIS_SYSTEM,
            temperature=0.1,
        )

        from app.utils.text import parse_json_loose
        result = parse_json_loose(raw)
        logger.debug(f"WikiAnalyzer: analysis complete for '{source_title}'")
        return result

    except Exception as e:
        logger.warning(f"WikiAnalyzer: analysis failed for '{source_title}': {e}")
        return None


def format_analysis_section(analysis: Optional[dict]) -> str:
    """Format the analysis dict into a markdown section for the agent's user message."""
    if not analysis:
        return ""

    lines = ["## Pre-Analysis (advisory — verify with tools before acting)\n"]

    doc_type = analysis.get("document_type", "")
    lang = analysis.get("primary_language", "")
    if doc_type or lang:
        lines.append(f"**Document type:** {doc_type} | **Language:** {lang}\n")

    themes = analysis.get("key_themes", [])
    if themes:
        lines.append(f"**Key themes:** {', '.join(themes[:8])}\n")

    entities = analysis.get("named_entities", [])
    if entities:
        lines.append("\n**Named entities:**")
        for e in entities[:10]:
            lines.append(f"- {e.get('name', '')} ({e.get('type', '')}) — {e.get('significance', '')}")

    concepts = analysis.get("key_concepts", [])
    if concepts:
        lines.append("\n**Key concepts to cover:**")
        for c in concepts[:10]:
            lines.append(f"- `{c.get('suggested_slug', '')}` — {c.get('name', '')}: {c.get('description', '')}")

    to_update = analysis.get("existing_pages_to_update", [])
    if to_update:
        lines.append("\n**Existing pages likely needing update:**")
        for u in to_update:
            lines.append(f"- `{u.get('slug', '')}` — {u.get('reason', '')}")

    to_create = analysis.get("new_pages_to_create", [])
    if to_create:
        lines.append("\n**Suggested new pages:**")
        for p in to_create:
            lines.append(f"- `{p.get('suggested_slug', '')}` ({p.get('page_type', '')}) — {p.get('title', '')}")

    source_slug = analysis.get("source_page_slug", "")
    if source_slug:
        lines.append(f"\n**Suggested source page slug:** `{source_slug}`")

    notes = analysis.get("compilation_notes", "")
    if notes:
        lines.append(f"\n**Compiler notes:** {notes}")

    return "\n".join(lines) + "\n"
