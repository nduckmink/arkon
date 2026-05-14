"""
Phase 0 (Triage) + Phase 1 (MAP) of the MRP pipeline.

Phase 0: classify_strategy() — decides single_pass / standard / hierarchical
         based on full_text length.

Phase 1: build_chunks() — splits document into ~20k-char chunks along section
         boundaries from outline_json. Each chunk is then sent to extract_chunk()
         in parallel (up to MAX_MAP_CONCURRENCY concurrent LLM calls).
         Results are persisted to SourceChunkExtract rows immediately so the
         pipeline can resume from a crash without re-doing completed chunks.
"""

import asyncio
import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers.base import LLMProvider
from app.utils.progress import ProgressTracker

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CHUNK_TARGET_CHARS = 20_000
OVERLAP_CHARS = 1_000
MAX_MAP_CONCURRENCY = 6
EXTRACT_TIMEOUT = 120  # seconds per extraction call
OVERLAP_SEPARATOR = "[…context from previous section…]\n"


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class DocumentChunk:
    index: int
    start_char: int       # absolute offset in full_text (does NOT include overlap prefix)
    end_char: int         # absolute offset in full_text
    section_path: str     # e.g. "Chapter 2 > Section 2.1"
    text: str             # chunk body (may be prefixed with OVERLAP_SEPARATOR + overlap text)
    overlap_prefix_len: int = field(default=0)
    # Length of the overlap prefix prepended to `text` (chars before the separator newline).
    # local_offset values from LLM output must be >= 0 relative to the body start
    # (after the separator). Conversion: absolute_offset = start_char + local_offset.


# ---------------------------------------------------------------------------
# Phase 0 — Triage
# ---------------------------------------------------------------------------

def classify_strategy(full_text: str, outline_json: Optional[list]) -> str:
    """Return 'single_pass', 'standard', or 'hierarchical' based on text length."""
    n = len(full_text)
    if n < 30_000:
        return "single_pass"
    elif n <= 200_000:
        return "standard"
    else:
        return "hierarchical"


# ---------------------------------------------------------------------------
# Phase 1a — Chunking
# ---------------------------------------------------------------------------

def _flatten_outline(nodes: list, depth: int = 0) -> list[dict]:
    """Recursively flatten an outline tree into a sorted list of nodes."""
    result = []
    for node in nodes:
        result.append({**node, "_depth": depth})
        if node.get("children"):
            result.extend(_flatten_outline(node["children"], depth + 1))
    return result


def build_chunks(full_text: str, outline_json: Optional[list], strategy: str) -> list[DocumentChunk]:
    """
    Split full_text into DocumentChunks for MAP extraction.

    Uses level-1 and level-2 outline headings as section boundaries. Groups
    sections until accumulated chars exceed CHUNK_TARGET_CHARS. If outline is
    absent or empty, falls back to a sliding-window split.

    The overlap prefix is prepended to each chunk's text for LLM context but
    is NOT part of the [start_char, end_char] range. local_offset values from
    LLM output should be relative to the body (i.e., after the separator).
    """
    flat = _flatten_outline(outline_json or [])
    top_nodes = [n for n in flat if n.get("level", 99) <= 2 and "char_start" in n and "char_end" in n]
    top_nodes.sort(key=lambda n: n["char_start"])

    if not top_nodes:
        return _sliding_window_chunks(full_text)

    chunks: list[DocumentChunk] = []
    current_start: Optional[int] = None
    current_end: int = 0
    current_sections: list[str] = []
    prev_body_end: int = 0  # tracks end of previous chunk body for overlap

    def _flush(idx: int, start: int, end: int, sections: list[str]) -> DocumentChunk:
        body = full_text[start:end]
        section_path = " > ".join(sections) if sections else f"chunk_{idx}"
        # Prepend overlap prefix from previous chunk
        if idx > 0 and prev_body_end > start:
            # overlap already included (sections can overlap); don't double-add
            prefix = ""
            overlap_len = 0
        elif idx > 0 and prev_body_end > 0:
            overlap_start = max(0, prev_body_end - OVERLAP_CHARS)
            overlap_text = full_text[overlap_start:prev_body_end]
            prefix = OVERLAP_SEPARATOR + overlap_text + "\n"
            overlap_len = len(prefix)
        else:
            prefix = ""
            overlap_len = 0
        return DocumentChunk(
            index=idx,
            start_char=start,
            end_char=end,
            section_path=section_path,
            text=prefix + body,
            overlap_prefix_len=overlap_len,
        )

    idx = 0
    for node in top_nodes:
        ns = node["char_start"]
        ne = min(node["char_end"], len(full_text))
        title = node.get("title", "")

        if current_start is None:
            current_start = ns
            current_end = ne
            current_sections = [title]
            continue

        accumulated = current_end - current_start
        section_size = ne - ns

        if accumulated + section_size > CHUNK_TARGET_CHARS and accumulated > 0:
            chunk = _flush(idx, current_start, current_end, current_sections)
            chunks.append(chunk)
            prev_body_end = current_end
            idx += 1
            current_start = ns
            current_end = ne
            current_sections = [title]
        else:
            current_end = max(current_end, ne)
            current_sections.append(title)

    if current_start is not None:
        # Cover any trailing text after last outline node
        trailing_end = len(full_text)
        chunk = _flush(idx, current_start, trailing_end, current_sections)
        chunks.append(chunk)

    # Edge case: if outline only covers part of the document, add a final chunk for the rest
    if chunks:
        last_covered = max(c.end_char for c in chunks)
        if last_covered < len(full_text) - 100:
            idx = len(chunks)
            remainder_start = last_covered
            prev_body_end = last_covered
            tail_chunk = _flush(idx, remainder_start, len(full_text), [f"tail_{idx}"])
            chunks.append(tail_chunk)

    return chunks if chunks else _sliding_window_chunks(full_text)


def _sliding_window_chunks(full_text: str) -> list[DocumentChunk]:
    """Fallback: fixed-size windows with overlap when no outline is available."""
    chunks = []
    n = len(full_text)
    idx = 0
    pos = 0
    prev_end = 0
    while pos < n:
        end = min(pos + CHUNK_TARGET_CHARS, n)
        body = full_text[pos:end]
        if idx > 0 and prev_end > 0:
            overlap_start = max(0, prev_end - OVERLAP_CHARS)
            overlap_text = full_text[overlap_start:prev_end]
            prefix = OVERLAP_SEPARATOR + overlap_text + "\n"
            overlap_len = len(prefix)
        else:
            prefix = ""
            overlap_len = 0
        chunks.append(DocumentChunk(
            index=idx,
            start_char=pos,
            end_char=end,
            section_path=f"chunk_{idx}",
            text=prefix + body,
            overlap_prefix_len=overlap_len,
        ))
        prev_end = end
        pos = end
        idx += 1
    return chunks


# ---------------------------------------------------------------------------
# Phase 1b — Extraction prompt
# ---------------------------------------------------------------------------

EXTRACTION_SYSTEM = """\
You are a knowledge extraction engine. Extract structured knowledge from the
provided document section. Return ONLY valid JSON matching the schema exactly.
Never include any text outside the JSON object. If a category has no items, use [].
"""

EXTRACTION_PROMPT_TEMPLATE = """\
## Document section
Section path: {section_path}
Character range in full document: {start_char}–{end_char}
{context_note}

## Text
{chunk_text}

---

Extract all knowledge from this section and return a JSON object with this exact schema:

{{
  "entities": [
    {{
      "name": "string — entity canonical name as it appears in text",
      "type": "string — one of: person|org|product|regulation|location|system|equipment|other",
      "aliases": ["string"],
      "local_offset": 0
    }}
  ],
  "concepts": [
    {{
      "term": "string — concept name",
      "definition_excerpt": "string — verbatim or near-verbatim defining phrase from text",
      "local_offset": 0
    }}
  ],
  "claims": [
    {{
      "statement": "string — complete factual claim stated in source",
      "subject": "string — entity/concept this claim is about",
      "local_offset": 0,
      "evidence_length": 200,
      "confidence": "explicit"
    }}
  ],
  "relations": [
    {{
      "from": "string — source entity/concept name",
      "to": "string — target entity/concept name",
      "type": "string — e.g. owns|part_of|caused_by|regulates|uses|located_in|other"
    }}
  ],
  "topics": ["string"]
}}

Rules:
- local_offset is the character position of the entity/concept/claim WITHIN the chunk
  text body (AFTER the context separator line if present). Start counting from 0 at the
  first character of the actual document section content.
- Absolute offset in full document = {start_char} + local_offset.
- confidence must be "explicit" (directly stated) or "inferred" (implied by the text).
- Be exhaustive — include all named entities, defined terms, and factual claims.
- Return empty arrays [] for categories with no findings.
- Return ONLY the JSON object, no other text.
"""


def _build_extraction_prompt(chunk: DocumentChunk) -> str:
    context_note = (
        f"Note: the first {chunk.overlap_prefix_len} chars are context from the previous "
        "section (before the separator line). local_offset values must start from 0 at "
        "the first character AFTER the separator."
        if chunk.overlap_prefix_len > 0
        else ""
    )
    return EXTRACTION_PROMPT_TEMPLATE.format(
        section_path=chunk.section_path,
        start_char=chunk.start_char,
        end_char=chunk.end_char,
        context_note=context_note,
        chunk_text=chunk.text,
    )


# ---------------------------------------------------------------------------
# Phase 1c — Single chunk extraction
# ---------------------------------------------------------------------------

def _parse_extract_json(raw: str) -> dict:
    """Parse LLM response to extraction dict. Raises ValueError on failure."""
    from app.utils.text import parse_json_loose
    return parse_json_loose(raw)


def _convert_offsets(extract: dict, chunk: DocumentChunk) -> dict:
    """Convert local_offset fields to absolute offsets in full_text."""
    base = chunk.start_char

    for item in extract.get("entities", []):
        item["absolute_offset"] = base + max(0, item.get("local_offset", 0))
        item.pop("local_offset", None)

    for item in extract.get("concepts", []):
        item["absolute_offset"] = base + max(0, item.get("local_offset", 0))
        item.pop("local_offset", None)

    for item in extract.get("claims", []):
        item["absolute_offset"] = base + max(0, item.get("local_offset", 0))
        item.pop("local_offset", None)

    return extract


async def extract_chunk(llm: LLMProvider, chunk: DocumentChunk) -> dict:
    """
    Single LLM call to extract structured knowledge from one chunk.
    Returns extract dict with absolute_offset fields. Raises on failure.
    """
    prompt = _build_extraction_prompt(chunk)
    raw = await asyncio.wait_for(
        llm.generate(prompt, system=EXTRACTION_SYSTEM, temperature=0.1),
        timeout=EXTRACT_TIMEOUT,
    )
    extract = _parse_extract_json(raw)
    extract = _convert_offsets(extract, chunk)
    return extract


# ---------------------------------------------------------------------------
# Phase 1d — MAP phase orchestrator
# ---------------------------------------------------------------------------

async def run_map_phase(
    session: AsyncSession,
    source_id: uuid.UUID,
    full_text: str,
    outline_json: Optional[list],
    tracker: ProgressTracker,
    llm: LLMProvider,
) -> tuple[str, list]:
    """
    Run Phase 0 (triage) + Phase 1 (MAP).

    Returns (strategy, chunk_extract_rows) where chunk_extract_rows is the list
    of SourceChunkExtract ORM objects with status='done'.

    Persists each chunk result to DB immediately for resume capability.
    Retries failed chunks once sequentially before continuing.
    """
    from app.database.models import Source, SourceChunkExtract

    strategy = classify_strategy(full_text, outline_json)
    logger.info(f"MRP: source={source_id} strategy={strategy} len={len(full_text)}")

    chunks = build_chunks(full_text, outline_json, strategy)
    logger.info(f"MRP MAP: {len(chunks)} chunks for source={source_id}")

    # Update source pipeline state
    source = await session.get(Source, source_id)
    if source:
        source.pipeline_strategy = strategy
        source.pipeline_phase = "map"
        await session.commit()

    # Load existing chunk rows (for resume)
    existing_rows = (await session.execute(
        select(SourceChunkExtract).where(SourceChunkExtract.source_id == source_id)
    )).scalars().all()
    existing_by_idx = {r.chunk_index: r for r in existing_rows}

    # Ensure a DB row exists for every chunk
    for chunk in chunks:
        if chunk.index not in existing_by_idx:
            row = SourceChunkExtract(
                source_id=source_id,
                chunk_index=chunk.index,
                start_char=chunk.start_char,
                end_char=chunk.end_char,
                section_path=chunk.section_path,
                status="pending",
            )
            session.add(row)
            existing_by_idx[chunk.index] = row
    await session.commit()

    # Reload after flush so IDs are populated
    existing_rows = (await session.execute(
        select(SourceChunkExtract).where(SourceChunkExtract.source_id == source_id)
    )).scalars().all()
    existing_by_idx = {r.chunk_index: r for r in existing_rows}

    pending_chunks = [c for c in chunks if existing_by_idx[c.index].status != "done"]
    done_count = len(chunks) - len(pending_chunks)
    logger.info(f"MRP MAP: {done_count} already done, {len(pending_chunks)} pending for source={source_id}")

    semaphore = asyncio.Semaphore(MAX_MAP_CONCURRENCY)
    commit_lock = asyncio.Lock()

    async def _extract_with_sem(chunk: DocumentChunk):
        async with semaphore:
            row = existing_by_idx[chunk.index]
            try:
                extract = await extract_chunk(llm, chunk)
                # Serialize mutations and commits — AsyncSession can't handle concurrent state changes
                async with commit_lock:
                    row.extract_json = extract
                    row.status = "done"
                    row.error_message = None
                    await session.commit()
            except Exception as e:
                logger.warning(f"MRP MAP chunk {chunk.index} failed: {e}")
                async with commit_lock:
                    row.status = "error"
                    row.error_message = str(e)[:500]
                    await session.commit()
            pct = 10 + int(40 * (done_count + chunk.index + 1) / max(len(chunks), 1))
            await tracker.update(pct, f"Extracting chunk {chunk.index + 1}/{len(chunks)}...")

    await asyncio.gather(*[_extract_with_sem(c) for c in pending_chunks])

    # Sequential retry for failed chunks
    error_chunks = [c for c in chunks if existing_by_idx[c.index].status == "error"]
    if error_chunks:
        logger.info(f"MRP MAP: retrying {len(error_chunks)} failed chunks for source={source_id}")
        for chunk in error_chunks:
            row = existing_by_idx[chunk.index]
            try:
                extract = await extract_chunk(llm, chunk)
                row.extract_json = extract
                row.status = "done"
                row.error_message = None
                await session.commit()
            except Exception as e:
                logger.warning(f"MRP MAP chunk {chunk.index} retry failed: {e}")

    # Return all done rows
    done_rows = [existing_by_idx[c.index] for c in chunks if existing_by_idx[c.index].status == "done"]
    logger.info(f"MRP MAP complete: {len(done_rows)}/{len(chunks)} chunks done for source={source_id}")
    return strategy, done_rows
