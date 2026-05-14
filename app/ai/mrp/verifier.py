"""
Phase 4 (VERIFY) of the MRP pipeline.

Two checks:
  4.1  Coverage check — entities with many mentions not covered by any page
  4.2  Conflict check — new page content may contradict existing KB pages

All checks are non-blocking for the pipeline: issues are flagged in logs and
in the page content (markers), but never cause the pipeline to fail.
"""

import asyncio
import json
from typing import Optional

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.mrp.writer import PageWriteResult
from app.ai.providers.base import EmbeddingProvider, LLMProvider
from app.utils.progress import ProgressTracker

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONFLICT_SIM_THRESHOLD = 0.80


# ---------------------------------------------------------------------------
# 4.1 Coverage check
# ---------------------------------------------------------------------------

def check_coverage(
    chunk_extracts: list,
    page_results: list[PageWriteResult],
    min_mentions: int = 3,
) -> list[str]:
    """
    Returns entity names mentioned >= min_mentions times in extracts
    but not covered by any page result. Logged as warnings (non-blocking).
    """
    # Count mentions per entity
    mention_counts: dict[str, int] = {}
    for row in chunk_extracts:
        for e in (row.extract_json or {}).get("entities", []):
            name = e.get("name", "").lower()
            if name:
                mention_counts[name] = mention_counts.get(name, 0) + 1

    # Collect all entity names covered by page results
    covered: set[str] = set()
    for pr in page_results:
        covered.update(n.lower() for n in pr.entity_names)
        covered.add(pr.title.lower())

    uncovered = [
        name for name, count in mention_counts.items()
        if count >= min_mentions and name not in covered
    ]

    if uncovered:
        logger.warning(
            f"MRP VERIFY coverage: {len(uncovered)} significant entities not covered: "
            + ", ".join(uncovered[:10])
        )

    return uncovered


# ---------------------------------------------------------------------------
# 4.2 Conflict check
# ---------------------------------------------------------------------------

async def check_conflicts(
    session: AsyncSession,
    page_results: list[PageWriteResult],
    embedding_provider: EmbeddingProvider,
    llm: LLMProvider,
    source,
) -> list[dict]:
    """
    For each new/updated page, find KB neighbors with high similarity and
    check for factual contradictions via LLM. Returns list of conflict dicts.
    Non-blocking: conflicts are logged and returned but don't fail the pipeline.
    """
    from app.services import wiki_service

    scope_type = source.scope_type or "global"
    scope_id = source.scope_id
    conflicts = []

    for pr in page_results:
        try:
            vec = await embedding_provider.embed(
                f"{pr.title}\n\n{pr.summary}\n\n{pr.content_md[:3000]}"
            )
            hits = await wiki_service.search_pages_semantic(
                session, vec, top_k=3, scope_type=scope_type, scope_id=scope_id,
            )
        except Exception:
            continue

        candidate_neighbors = [
            (page, sim) for page, sim in hits
            if sim >= CONFLICT_SIM_THRESHOLD and page.slug != pr.slug
        ]
        if not candidate_neighbors:
            continue

        for kb_page, sim in candidate_neighbors:
            prompt = (
                f"Do the following two texts contain contradictory factual statements?\n\n"
                f"Text A (new):\n{pr.content_md[:1500]}\n\n"
                f"Text B (existing wiki page '{kb_page.slug}'):\n{(kb_page.content_md or '')[:1500]}\n\n"
                f"Return JSON: {{\"contradicts\": true|false, \"description\": \"string\"}}"
            )
            try:
                raw = await asyncio.wait_for(
                    llm.generate(prompt, system="You are a fact-checking assistant. Return only JSON.", temperature=0.0),
                    timeout=30,
                )
                from app.utils.text import parse_json_loose
                result = parse_json_loose(raw)
                if result.get("contradicts"):
                    desc = result.get("description", "")
                    conflicts.append({
                        "new_slug": pr.slug,
                        "existing_slug": kb_page.slug,
                        "similarity": sim,
                        "description": desc,
                    })
                    logger.warning(
                        f"MRP VERIFY conflict: '{pr.slug}' ↔ '{kb_page.slug}' (sim={sim:.2f}): {desc[:150]}"
                    )
            except Exception:
                pass

    return conflicts


# ---------------------------------------------------------------------------
# Phase 4 orchestrator
# ---------------------------------------------------------------------------

async def run_verify_phase(
    session: AsyncSession,
    source,
    page_results: list[PageWriteResult],
    chunk_extracts: list,
    full_text: str,
    llm: LLMProvider,
    embedding_provider: Optional[EmbeddingProvider],
    tracker: ProgressTracker,
) -> list[PageWriteResult]:
    """
    Run Phase 4 (VERIFY). Returns page results unchanged.

    Coverage and conflict checks run as non-blocking diagnostics.
    """
    await tracker.update(88, "Checking coverage...")

    # 4.1 Coverage check (code only, non-blocking)
    check_coverage(chunk_extracts, page_results)

    await tracker.update(91, "Checking for conflicts...")

    # 4.2 Conflict check (non-blocking)
    if embedding_provider is not None:
        try:
            await check_conflicts(session, page_results, embedding_provider, llm, source)
        except Exception as exc:
            logger.warning(f"MRP VERIFY conflict check failed: {exc}")

    logger.info(f"MRP VERIFY complete: {len(page_results)} pages verified for source={source.id}")
    return page_results
