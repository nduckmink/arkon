"""
Phase 3 (REFINE) of the MRP pipeline.

Each page in the Compilation Plan gets a dedicated writer. The writer receives
pre-assembled evidence (claims + excerpts) so it never needs to scan the full
document — contrast with the old wiki_agent which did exploratory reading.

Two writer modes:
  - Simple: 1 llm.generate() call for pages with few evidence items
  - Complex: mini agent loop (max 10 steps, 3 tools) for large pages

All writers run in parallel (asyncio.Semaphore(MAX_WRITER_CONCURRENCY)).
"""

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers.base import EmbeddingProvider, LLMProvider
from app.utils.progress import ProgressTracker

if TYPE_CHECKING:
    from app.database.models import SourceCompilationPlan

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_WRITER_CONCURRENCY = 4
WRITER_COMPLEX_THRESHOLD_EVIDENCE = 8
WRITER_COMPLEX_THRESHOLD_EXISTING_CHARS = 3_000
WRITER_AGENT_MAX_STEPS = 10
WRITER_AGENT_TIMEOUT = 300  # seconds per LLM call in complex writer

# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class PageWriteResult:
    slug: str
    title: str
    page_type: str
    action: str          # CREATE | UPDATE
    content_md: str
    summary: str
    citations: list[dict] = field(default_factory=list)
    # [{"ref": "[^1]", "absolute_offset": int, "evidence_length": int}]
    entity_names: list[str] = field(default_factory=list)
    related_kb_pages: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "slug": self.slug,
            "title": self.title,
            "page_type": self.page_type,
            "action": self.action,
            "content_md": self.content_md,
            "summary": self.summary,
            "citations": self.citations,
            "entity_names": self.entity_names,
            "related_kb_pages": self.related_kb_pages,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PageWriteResult":
        return cls(
            slug=d.get("slug", ""),
            title=d.get("title", ""),
            page_type=d.get("page_type", "concept"),
            action=d.get("action", "CREATE"),
            content_md=d.get("content_md", ""),
            summary=d.get("summary", ""),
            citations=d.get("citations", []),
            entity_names=d.get("entity_names", []),
            related_kb_pages=d.get("related_kb_pages", []),
        )


# ---------------------------------------------------------------------------
# Evidence assembly
# ---------------------------------------------------------------------------

def assemble_evidence(
    plan_item: dict,
    claims: list[dict],
    full_text: str,
) -> list[dict]:
    """
    Collect all claims whose subject matches any entity_name in the plan item.
    Matches use whole-word/whole-phrase comparison (case-insensitive) so short
    names like "AI" don't accidentally match "AIRPLANE" or "MAIL".
    """
    import re

    entity_names_lower = [n.lower().strip() for n in plan_item.get("entity_names", []) if n and n.strip()]
    if not entity_names_lower:
        return []

    # Pre-compile a word-boundary pattern per entity name. We escape the name so
    # punctuation in the name is treated literally.
    patterns = [re.compile(rf"\b{re.escape(name)}\b", re.IGNORECASE) for name in entity_names_lower]

    evidence = []
    for claim in claims:
        subj_raw = (claim.get("subject") or "").strip()
        if not subj_raw:
            continue
        subj_lower = subj_raw.lower()

        # Exact match (after normalization) — the strongest signal.
        if subj_lower in entity_names_lower:
            matched = True
        else:
            # Word-boundary match for multi-word subjects like "Acme Corp's CEO"
            matched = any(p.search(subj_raw) for p in patterns)

        if not matched:
            continue

        offset = claim.get("absolute_offset", 0)
        length = min(claim.get("evidence_length", 200), 500)
        excerpt = full_text[offset: offset + length] if full_text else ""
        evidence.append({
            "statement": claim.get("statement", ""),
            "subject": claim.get("subject", ""),
            "confidence": claim.get("confidence", "explicit"),
            "source_excerpt": excerpt,
            "absolute_offset": offset,
            "evidence_length": length,
        })
    return evidence


# ---------------------------------------------------------------------------
# System prompt — ported from wiki_compiler.py with full quality rules
# ---------------------------------------------------------------------------

WRITER_SYSTEM = """\
You are an enterprise knowledge wiki writer. Your job is to write a single,
high-quality wiki page by reading the SOURCE TEXT provided and using the
evidence checklist as guidance for what to cover.

# Mindset: COMPILE, do NOT summarize
You are not writing an executive summary. You are extracting structured knowledge
and rewriting it into a reusable wiki page. The output should contain MORE
information density than a summary — organized differently, but not condensed.

A summary loses specifics. A wiki page preserves them in a queryable structure.
If someone reads the wiki page two years from now, they should still be able to
find the actual numbers, regulations, procedures, names, and edge cases — not
just a high-level recap.

# What to KEEP from the source (do not lose these)
- Specific numbers: thresholds, dosages, timeframes, dimensions, percentages.
- Named regulations, laws, articles, code references.
- Equipment names, model numbers, product specs.
- Procedure steps in order, with actual actions (not "follow the procedure"
  but "1. do X 2. do Y 3. do Z").
- Worked examples and exceptions — usually the highest-value content.
- Named parties, roles, contact paths, escalation chains.
- Definitions verbatim or near-verbatim if the source is authoritative.
- Cause-effect statements ("X causes Y because Z") — preserve all three parts.

# What to DROP
- Marketing language, mission statements, ceremonial filler.
- Source-specific framing: "This document explains...", "In Section 3 below..."
- Repeated boilerplate, tables of contents, cover page metadata.
- Prose that just rephrases what was already said.

# Language rule
Write in the SAME LANGUAGE as the source document. Never translate content.

# Page structure — CRITICAL
Each page must be a proper encyclopedic article, NOT a flat bullet list:

1. **Opening paragraph** — 2-4 sentences defining what this thing is. No heading.
2. **Sections with H2 headings** — group related facts under clear headings.
   Each section starts with prose before any sub-bullets.
3. **Bold key terms** on first use. Link them to their wiki pages with [[ ]].
4. **Examples or implications** where the source provides them.
5. **See also** section at the end — wikilinks to related pages.

# What NOT to do
- Do NOT dump raw bullet points from the source as the entire content.
- Do NOT write a page that is just a title + 3 bullets. That is not a wiki page.
- Do NOT omit the opening prose paragraph.
- Do NOT include a Citations or Footnotes section.
- Do NOT use [^N] footnote markers.
- Do NOT translate the content language.

# Wikilinks
- Use [[slug]] or [[slug|display text]] to cross-link.
- CRITICAL: You may ONLY link to slugs from the "Available pages" list.
  Do NOT invent or hallucinate slugs.

# Minimum depth
- concept/topic pages: at least 200 words of actual prose+structure.
- entity pages: at least 100 words.
- source pages: at least 150 words.

# Image markers
- PRESERVE image markers verbatim: ![caption](image://<uuid>)
- Place each marker where it's most contextually relevant.
- Do NOT invent image UUIDs.
"""

SOURCE_CONTEXT_FALLBACK_CHARS = 60_000  # fallback when model is unknown

# Approximate context windows for known models (in tokens).
# We use ~60% of input window for source text, leaving room for
# system prompt, evidence blocks, and output tokens.
# 1 token ≈ 4 chars (English), conservative estimate.
# Last updated: 2026-05-11
_MODEL_CONTEXT_TOKENS: dict[str, int] = {
    # Google Gemini — all 1M context
    "gemini-3.1-pro": 1_000_000,
    "gemini-3.1-flash": 1_000_000,
    "gemini-3.0-flash": 1_000_000,
    "gemini-2.5-flash": 1_000_000,
    "gemini-2.5-pro": 1_000_000,
    "gemini-2.0-flash": 1_000_000,
    # OpenAI GPT-5.x
    "gpt-5.5-instant": 1_000_000,
    "gpt-5.4": 1_000_000,
    "gpt-5.2": 256_000,
    # OpenAI GPT-4.x (legacy but still used)
    "gpt-4.1-mini": 1_000_000,
    "gpt-4.1-nano": 1_000_000,
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    # Anthropic Claude 4.x — all 1M context
    "claude-4.7-opus": 1_000_000,
    "claude-4.6-sonnet": 1_000_000,
    "claude-sonnet-4-20250514": 1_000_000,
    "claude-haiku-4-20250514": 200_000,
}

# Source text gets 60% of the context budget; the rest is for system prompt,
# evidence blocks, existing content, and output tokens.
_SOURCE_BUDGET_RATIO = 0.60
_CHARS_PER_TOKEN = 4  # conservative estimate


def _get_source_context_budget(model_id: str | None) -> int:
    """
    Calculate the maximum chars allowed for source context based on the
    model's context window. Falls back to SOURCE_CONTEXT_FALLBACK_CHARS
    if the model is unknown.
    """
    if not model_id:
        return SOURCE_CONTEXT_FALLBACK_CHARS

    # Try exact match first, then prefix match for versioned models
    ctx_tokens = _MODEL_CONTEXT_TOKENS.get(model_id)
    if ctx_tokens is None:
        for key, val in _MODEL_CONTEXT_TOKENS.items():
            if model_id.startswith(key):
                ctx_tokens = val
                break

    if ctx_tokens is None:
        return SOURCE_CONTEXT_FALLBACK_CHARS

    budget_chars = int(ctx_tokens * _CHARS_PER_TOKEN * _SOURCE_BUDGET_RATIO)

    # Cap at 800k chars (~200k tokens) — beyond this, diminishing returns
    # and most LLMs struggle with very long context anyway.
    return min(budget_chars, 800_000)


# ---------------------------------------------------------------------------
# Source context builder
# ---------------------------------------------------------------------------

def _build_source_context(
    full_text: str,
    evidence: list[dict],
    model_id: str | None = None,
) -> str:
    """
    Build source context for the writer.

    Budget is dynamically calculated based on the model's context window:
      - gemini-2.5-flash (1M tokens) → up to ~800k chars of source
      - gpt-4o (128k tokens)         → up to ~307k chars
      - unknown model                → 60k chars fallback

    For short documents (fits in budget): include the full text.
    For long documents: smart extraction — section-level relevance scoring
    based on evidence density, with full sections preserved for coherence.
    """
    budget = _get_source_context_budget(model_id)

    if len(full_text) <= budget:
        return full_text

    # --- Long document: smart section extraction ---
    # 1. Split into sections by headings (H1-H4) or paragraph blocks
    sections = _split_into_sections(full_text)

    # 2. Score each section by evidence density
    scored = _score_sections(sections, evidence)

    # 3. Always include first section (intro/overview) if it's reasonably short
    result_parts: list[tuple[int, str]] = []  # (original_index, text)
    total = 0

    if scored and scored[0][0] == 0:
        # First section is already scored highest or close
        pass

    # Include the opening section (first 2000 chars at minimum)
    intro = full_text[:2000]
    intro_end = full_text.find("\n#", 2000)
    if intro_end > 0:
        intro = full_text[:intro_end]
    result_parts.append((0, intro))
    total += len(intro)

    # 4. Greedily add highest-scored sections until budget is filled
    for orig_idx, text, _score in scored:
        if total + len(text) > budget:
            # Try to fit a truncated version if section is very long
            remaining = budget - total
            if remaining > 1000:
                result_parts.append((orig_idx, text[:remaining] + "\n\n[…section truncated…]"))
                total += remaining
            break
        # Skip if overlaps with intro
        if orig_idx == 0 and any(idx == 0 for idx, _ in result_parts):
            continue
        result_parts.append((orig_idx, text))
        total += len(text)

    # 5. Sort by original document order for coherent reading
    result_parts.sort(key=lambda x: x[0])

    # 6. Assemble with position markers
    parts = []
    for i, (orig_idx, text) in enumerate(result_parts):
        if i > 0:
            parts.append("\n\n[…skipped sections…]\n\n")
        parts.append(text)

    if total < len(full_text):
        parts.append(f"\n\n[…document continues… total {len(full_text)} chars, showing {total}…]")

    logger.info(
        f"MRP WRITER source context: {len(full_text)} chars → {total} chars "
        f"({total*100//len(full_text)}%), budget={budget}, model={model_id}"
    )

    return "".join(parts)


def _split_into_sections(text: str) -> list[tuple[int, str]]:
    """
    Split text into sections by markdown headings (H1-H4).
    Returns list of (char_offset, section_text).
    If no headings found, splits by double-newline paragraphs.
    """
    import re
    heading_pattern = re.compile(r'^(#{1,4})\s+', re.MULTILINE)

    matches = list(heading_pattern.finditer(text))
    if not matches:
        # No headings — split by paragraph blocks (~3000 chars each)
        chunks = []
        for i in range(0, len(text), 3000):
            # Try to break at paragraph boundary
            end = min(i + 3000, len(text))
            if end < len(text):
                para_break = text.rfind("\n\n", i, end)
                if para_break > i:
                    end = para_break + 2
            chunks.append((i, text[i:end]))
        return chunks

    sections = []
    # Text before first heading
    if matches[0].start() > 0:
        sections.append((0, text[:matches[0].start()]))

    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append((start, text[start:end]))

    return sections


def _score_sections(
    sections: list[tuple[int, str]],
    evidence: list[dict],
) -> list[tuple[int, str, float]]:
    """
    Score sections by relevance to evidence items.
    Returns sorted list of (section_index, text, score) — highest score first.

    Scoring signals:
      1. Evidence overlap: how many evidence items fall within this section
      2. Evidence proximity: distance-weighted score for nearby evidence
      3. Section position: slight boost for earlier sections (usually more important)
    """
    if not evidence:
        # No evidence — return sections in order with equal scores
        return [(i, text, 1.0) for i, (_, text) in enumerate(sections)]

    # Build evidence offsets
    ev_offsets = [ev.get("absolute_offset", 0) for ev in evidence]

    scored = []
    for sec_idx, (sec_start, sec_text) in enumerate(sections):
        sec_end = sec_start + len(sec_text)

        # Count evidence items that fall within this section
        direct_hits = sum(1 for off in ev_offsets if sec_start <= off < sec_end)

        # Proximity score: evidence items near this section
        proximity = 0.0
        for off in ev_offsets:
            if sec_start <= off < sec_end:
                proximity += 1.0  # direct hit
            else:
                dist = min(abs(off - sec_start), abs(off - sec_end))
                if dist < 5000:
                    proximity += max(0, 1.0 - dist / 5000)

        # Position bonus: earlier sections get slight boost
        position_bonus = max(0, 1.0 - sec_idx * 0.02)

        score = direct_hits * 3.0 + proximity + position_bonus
        scored.append((sec_idx, sec_text, score))

    # Sort by score descending
    scored.sort(key=lambda x: -x[2])
    return scored


# ---------------------------------------------------------------------------
# Simple writer — 1 LLM call
# ---------------------------------------------------------------------------

_SIMPLE_WRITER_PROMPT = """\
## Task
{action} the following wiki page.

## Page specification
- Slug: {slug}
- Title: {title}
- Type: {page_type}

## Available pages (ONLY use these slugs for [[wikilinks]])
{all_plan_slugs}

{existing_section}

## Source document text
Read this carefully. Extract all relevant facts for this page's topic.

{source_context}

## Evidence checklist ({evidence_count} items)
The following items were pre-extracted and should be covered in the page.
Use them as a checklist — make sure you don't miss any of these facts.
But also look for additional relevant information in the source text above.

{evidence_blocks}
{image_section}
## Instructions
Write the complete wiki page in markdown based on the source text above.
Cross-link to other pages using [[slug]] or [[slug|display text]] — ONLY
use slugs from the "Available pages" list. Do NOT invent new slugs.
Do NOT include Citations or Footnotes sections.

Return ONLY the markdown content, no other text.
"""


def _format_evidence_blocks(evidence: list[dict]) -> tuple[str, list[dict]]:
    """Format evidence as a checklist for the prompt. Returns (formatted_string, empty_list)."""
    lines = []
    for i, ev in enumerate(evidence, 1):
        lines.append(
            f"{i}. [{ev['confidence'].upper()}] {ev['subject']}\n"
            f"   {ev['statement']}"
        )
    return "\n\n".join(lines), []


_IMAGE_MARKER_RE = re.compile(r"!\[([^\]]*)\]\(image://([0-9a-fA-F-]+)\)")


def _collect_relevant_image_markers(
    evidence: list[dict],
    full_text: str,
    window: int = 1500,
) -> list[str]:
    """
    Find image markers near this page's evidence offsets. Markers in source text
    are emitted with their captions; writer is told to place them where relevant.
    Returns unique markers preserving first-seen order.
    """
    if not full_text:
        return []
    seen: set[str] = set()
    ordered: list[str] = []
    for ev in evidence:
        off = ev.get("absolute_offset", 0)
        start = max(0, off - window)
        end = min(len(full_text), off + ev.get("evidence_length", 200) + window)
        for m in _IMAGE_MARKER_RE.finditer(full_text, start, end):
            marker = m.group(0)
            if marker not in seen:
                seen.add(marker)
                ordered.append(marker)
    return ordered


async def _write_page_simple(
    llm: LLMProvider,
    plan_item: dict,
    evidence: list[dict],
    existing_content: Optional[str],
    all_plan_slugs: list[str],
    source_context: str = "",
    image_markers: Optional[list[str]] = None,
) -> tuple[str, str, list[dict]]:
    """
    Returns (content_md, summary, citations_meta).
    """
    # Format available slugs for the prompt (exclude self)
    own_slug = plan_item.get("slug", "")
    available = [s for s in all_plan_slugs if s != own_slug]
    all_plan_slugs_str = "\n".join(f"- [[{s}]]" for s in available) if available else "(none — this is the only page)"

    existing_section = (
        f"## Existing page content (UPDATE — integrate new evidence into this)\n\n{existing_content}\n"
        if existing_content else ""
    )
    evidence_blocks, citations_meta = _format_evidence_blocks(evidence)

    image_section = ""
    if image_markers:
        image_section = (
            "\n## Images near this page's evidence\n"
            "The following image markers appear near the evidence for this page. "
            "Embed each marker VERBATIM in the most contextually appropriate section, "
            "or omit if not relevant. Do NOT invent image UUIDs.\n\n"
            + "\n".join(f"- {m}" for m in image_markers)
            + "\n"
        )

    prompt = _SIMPLE_WRITER_PROMPT.format(
        action=plan_item.get("action", "CREATE"),
        slug=plan_item.get("slug", ""),
        title=plan_item.get("title", ""),
        page_type=plan_item.get("page_type", "concept"),
        all_plan_slugs=all_plan_slugs_str,
        existing_section=existing_section,
        source_context=source_context or "(no source text available)",
        evidence_count=len(evidence),
        evidence_blocks=evidence_blocks or "(no pre-extracted evidence)",
        image_section=image_section,
    )

    raw = await asyncio.wait_for(
        llm.generate(prompt, system=WRITER_SYSTEM, temperature=0.15),
        timeout=WRITER_AGENT_TIMEOUT,
    )

    # Extract summary from first non-heading paragraph
    lines = raw.strip().splitlines()
    summary_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if stripped:
            summary_lines.append(stripped)
            if len(" ".join(summary_lines)) > 100:
                break
    summary = " ".join(summary_lines)[:300]

    return raw.strip(), summary, citations_meta


# ---------------------------------------------------------------------------
# Complex writer — mini agent loop
# ---------------------------------------------------------------------------

_COMPLEX_WRITER_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_kb_page",
            "description": "Read the full markdown content of an existing wiki page.",
            "parameters": {
                "type": "object",
                "properties": {"slug": {"type": "string", "description": "Page slug"}},
                "required": ["slug"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_source_excerpt",
            "description": "Read more context from the source document by character offset.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_char": {"type": "integer"},
                    "length": {"type": "integer", "description": "Max 10000"},
                },
                "required": ["start_char"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "Submit the completed wiki page content. Must be the final call.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content_md": {"type": "string", "description": "Full markdown content using [[slug]] wikilinks"},
                    "summary": {"type": "string", "description": "One-sentence summary"},
                },
                "required": ["content_md", "summary"],
            },
        },
    },
]

_COMPLEX_WRITER_SYSTEM = WRITER_SYSTEM + """

# Tool workflow
1. Optionally call read_kb_page for any related page you want to reference.
2. Optionally call read_source_excerpt to read more context from the source.
3. Call finish with the complete page content and summary.
"""


async def _write_page_complex(
    llm: LLMProvider,
    plan_item: dict,
    evidence: list[dict],
    existing_content: Optional[str],
    full_text: str,
    session: AsyncSession,
    source,
    all_plan_slugs: list[str],
) -> tuple[str, str, list[dict]]:
    """
    Mini agent loop for pages with many evidence items or large existing content.
    Returns (content_md, summary, citations_meta).
    """
    from app.ai.agent_protocol import assistant_message_from_turn, tool_results_message
    from app.services import wiki_service

    scope_type = source.scope_type or "global"
    scope_id = source.scope_id

    evidence_blocks, citations_meta = _format_evidence_blocks(evidence)
    existing_section = (
        f"\n## Existing page content (UPDATE — integrate):\n{existing_content}\n"
        if existing_content else ""
    )

    # Format available slugs (exclude self)
    own_slug = plan_item.get("slug", "")
    available = [s for s in all_plan_slugs if s != own_slug]
    slugs_list = "\n".join(f"- [[{s}]]" for s in available) if available else "(none)"

    # Build source context
    source_context = _build_source_context(full_text, evidence, model_id=llm.config.model_id)

    image_markers = _collect_relevant_image_markers(evidence, full_text)
    image_section = ""
    if image_markers:
        image_section = (
            "\n## Images near this page's evidence\n"
            "Embed each marker VERBATIM where contextually appropriate, or omit "
            "if not relevant. Do NOT invent image UUIDs.\n"
            + "\n".join(f"- {m}" for m in image_markers)
            + "\n"
        )

    initial_msg = (
        f"Write a wiki page for: **{plan_item.get('title', '')}** "
        f"(slug: `{own_slug}`, type: {plan_item.get('page_type', 'concept')})\n"
        f"Action: {plan_item.get('action', 'CREATE')}\n\n"
        f"## Available pages (ONLY use these for [[wikilinks]])\n{slugs_list}\n"
        f"{existing_section}\n"
        f"## Source document text\n{source_context}\n\n"
        f"## Evidence checklist ({len(evidence)} items)\n{evidence_blocks}"
        f"{image_section}"
    )

    messages = [{"role": "user", "content": initial_msg}]
    result_content = None
    result_summary = None

    for step in range(WRITER_AGENT_MAX_STEPS):
        from app.ai.agent_protocol import AssistantTurn
        try:
            turn: AssistantTurn = await asyncio.wait_for(
                llm.generate_with_tools(
                    messages=messages,
                    tools=_COMPLEX_WRITER_TOOLS,
                    system=_COMPLEX_WRITER_SYSTEM,
                    temperature=0.15,
                ),
                timeout=WRITER_AGENT_TIMEOUT,
            )
        except Exception as e:
            err_msg = f"{type(e).__name__}: {str(e)}"
            logger.error(f"MRP complex writer LLM call failed at step {step}: {err_msg}")
            raise

        messages.append(assistant_message_from_turn(turn))

        if not turn.tool_calls:
            break

        tool_results = []
        for call in turn.tool_calls:
            if call.name == "finish":
                result_content = call.arguments.get("content_md", "")
                result_summary = call.arguments.get("summary", "")
                tool_results.append((call.id, call.name, {"done": True}))
                break
            elif call.name == "read_kb_page":
                slug = call.arguments.get("slug", "")
                page = await wiki_service.get_page_by_slug(session, slug, scope_type=scope_type, scope_id=scope_id)
                if page:
                    result: Any = {"slug": page.slug, "title": page.title, "content_md": page.content_md}
                else:
                    result = {"error": f"Page '{slug}' not found"}
                tool_results.append((call.id, call.name, result))
            elif call.name == "read_source_excerpt":
                start = max(0, int(call.arguments.get("start_char", 0)))
                length = min(int(call.arguments.get("length", 5000)), 10000)
                excerpt = full_text[start: start + length] if full_text else ""
                tool_results.append((call.id, call.name, {"excerpt": excerpt, "start_char": start}))
            else:
                tool_results.append((call.id, call.name, {"error": f"Unknown tool: {call.name}"}))

        if result_content is not None:
            break

        messages.append(tool_results_message(tool_results))

    if result_content is None:
        # Agent didn't call finish — extract from last text response
        for msg in reversed(messages):
            if msg.get("role") == "assistant":
                content = msg.get("content", "")
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            result_content = block.get("text", "")
                            break
                elif isinstance(content, str):
                    result_content = content
                if result_content:
                    break
        result_content = result_content or f"# {plan_item.get('title', '')}\n\n(content generation incomplete)"
        result_summary = plan_item.get("title", "")

    # Quick summary extraction if not provided
    if not result_summary:
        for line in result_content.splitlines():
            s = line.strip()
            if s and not s.startswith("#"):
                result_summary = s[:300]
                break
        result_summary = result_summary or plan_item.get("title", "")

    return result_content.strip(), result_summary, citations_meta


# ---------------------------------------------------------------------------
# Phase 3 orchestrator
# ---------------------------------------------------------------------------

async def run_refine_phase(
    session: AsyncSession,
    source,
    plan: "SourceCompilationPlan",
    chunk_extracts: list,
    full_text: str,
    llm: LLMProvider,
    embedding_provider: Optional[EmbeddingProvider],
    kt_slug: Optional[str],
    tracker: ProgressTracker,
) -> list[PageWriteResult]:
    """
    Run Phase 3 (REFINE): write all pages in the compilation plan in parallel.
    Returns list of PageWriteResult objects ready for Phase 4 (VERIFY).
    """
    from app.services import wiki_service

    plan_dict = plan.plan_json
    pages_spec = plan_dict.get("pages", [])
    all_claims = plan_dict.get("_claims", [])

    # Sort by priority (lower number = higher priority)
    pages_spec = sorted(pages_spec, key=lambda p: p.get("priority", 99))

    # Collect ALL slugs from the plan so writers can cross-link accurately
    all_plan_slugs = [p.get("slug", "") for p in pages_spec if p.get("slug")]

    scope_type = source.scope_type or "global"
    scope_id = source.scope_id

    await tracker.update(78, f"Writing {len(pages_spec)} wiki pages...")

    semaphore = asyncio.Semaphore(MAX_WRITER_CONCURRENCY)

    async def _write_one(plan_item: dict) -> Optional[PageWriteResult]:
        async with semaphore:
            action = plan_item.get("action", "CREATE").upper()
            slug = plan_item.get("slug", "")
            title = plan_item.get("title", slug)
            page_type = plan_item.get("page_type", "concept")
            related_kb_pages = plan_item.get("related_kb_pages", [])

            # Assemble evidence
            evidence = assemble_evidence(plan_item, all_claims, full_text)

            # Fetch existing content for UPDATE
            existing_content: Optional[str] = None
            if action == "UPDATE":
                existing_page = await wiki_service.get_page_by_slug(
                    session, slug, scope_type=scope_type, scope_id=scope_id,
                )
                if existing_page:
                    existing_content = existing_page.content_md

            # Choose writer mode
            is_complex = (
                len(evidence) > WRITER_COMPLEX_THRESHOLD_EVIDENCE
                or len(existing_content or "") > WRITER_COMPLEX_THRESHOLD_EXISTING_CHARS
            )

            # Build source context for the writer
            source_context = _build_source_context(full_text, evidence, model_id=llm.config.model_id)
            image_markers = _collect_relevant_image_markers(evidence, full_text)

            try:
                if is_complex:
                    content_md, summary, citations = await _write_page_complex(
                        llm, plan_item, evidence, existing_content, full_text, session, source,
                        all_plan_slugs=all_plan_slugs,
                    )
                else:
                    content_md, summary, citations = await _write_page_simple(
                        llm, plan_item, evidence, existing_content,
                        all_plan_slugs=all_plan_slugs,
                        source_context=source_context,
                        image_markers=image_markers,
                    )
            except Exception as e:
                err_msg = f"{type(e).__name__}: {str(e)}"
                logger.error(f"MRP REFINE writer failed for '{slug}': {err_msg}")
                # Return minimal stub so COMMIT can still proceed
                content_md = f"# {title}\n\n(Page generation failed: {err_msg[:200]})"
                summary = title
                citations = []

            return PageWriteResult(
                slug=slug,
                title=title,
                page_type=page_type,
                action=action,
                content_md=content_md,
                summary=summary,
                citations=citations,
                entity_names=plan_item.get("entity_names", []),
                related_kb_pages=related_kb_pages,
            )

    results = await asyncio.gather(*[_write_one(p) for p in pages_spec])
    page_results = [r for r in results if r is not None]

    # Persist drafts into plan_json so VERIFY/COMMIT can resume without re-running REFINE.
    try:
        plan_json = dict(plan.plan_json or {})
        plan_json["_page_drafts"] = [pr.to_dict() for pr in page_results]
        plan.plan_json = plan_json
        await session.commit()
    except Exception as exc:
        logger.warning(f"MRP REFINE failed to persist page drafts: {exc}")

    logger.info(f"MRP REFINE complete: {len(page_results)} pages written for source={source.id}")
    return page_results
