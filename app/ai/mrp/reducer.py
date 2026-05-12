"""
Phase 2 (REDUCE) of the MRP pipeline.

Steps:
  2.1  Collect all entities/concepts from chunk extracts
  2.2  Exact deduplication by normalized name
  2.3  Embedding-based deduplication (cosine similarity)
  2.4  LLM batch resolution for ambiguous entity pairs
  2.5  KB reconciliation: search existing wiki pages per entity
  2.6  LLM batch confirmation for MAYBE matches
  2.7  Planning call: 1 LLM call → Compilation Plan JSON
  2.8  Persist SourceCompilationPlan to DB
"""

import asyncio
import json
import re
import string
import uuid
from typing import TYPE_CHECKING, Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers.base import EmbeddingProvider, LLMProvider
from app.utils.progress import ProgressTracker

if TYPE_CHECKING:
    from app.database.models import SourceCompilationPlan

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MERGE_THRESHOLD = 0.90      # cosine sim → auto-merge entities
AMBIGUOUS_LOW = 0.75        # cosine sim → send to LLM for disambiguation
KB_UPDATE_THRESHOLD = 0.85  # sim → UPDATE existing wiki page
KB_MAYBE_THRESHOLD = 0.60   # sim → MAYBE update (LLM confirms)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PUNCT_TABLE = str.maketrans("", "", string.punctuation)


def _normalize(name: str) -> str:
    return name.lower().strip().translate(_PUNCT_TABLE)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# ---------------------------------------------------------------------------
# Step 2.1 — Collect entities and concepts from chunk extracts
# ---------------------------------------------------------------------------

def collect_raw_items(chunk_extracts) -> tuple[list[dict], list[dict], list[dict]]:
    """
    Flatten entities, concepts, and claims from all SourceChunkExtract rows.

    Returns (entities, concepts, claims) where each item carries its
    source chunk_index and absolute_offset fields.
    """
    entities: list[dict] = []
    concepts: list[dict] = []
    claims: list[dict] = []

    for row in chunk_extracts:
        extract = row.extract_json or {}
        chunk_idx = row.chunk_index

        for e in extract.get("entities", []):
            entities.append({**e, "_chunk_index": chunk_idx})

        for c in extract.get("concepts", []):
            concepts.append({**c, "_chunk_index": chunk_idx})

        for cl in extract.get("claims", []):
            claims.append({**cl, "_chunk_index": chunk_idx})

    return entities, concepts, claims


# ---------------------------------------------------------------------------
# Step 2.2 — Exact deduplication
# ---------------------------------------------------------------------------

def exact_dedup_entities(raw_entities: list[dict]) -> list[dict]:
    """
    Group entities by (normalized_name, type). Keep most common name as
    canonical. Accumulate all aliases and absolute_offsets.
    """
    groups: dict[tuple, list[dict]] = {}
    for e in raw_entities:
        key = (_normalize(e.get("name", "")), e.get("type", "other"))
        groups.setdefault(key, []).append(e)

    canonical: list[dict] = []
    for (norm_name, etype), group in groups.items():
        # Pick the name that appears most frequently across the group
        name_counts: dict[str, int] = {}
        for e in group:
            n = e.get("name", "")
            name_counts[n] = name_counts.get(n, 0) + 1
        best_name = max(name_counts, key=lambda x: name_counts[x])

        # Merge aliases
        aliases: set[str] = set()
        for e in group:
            aliases.add(e.get("name", ""))
            aliases.update(e.get("aliases", []))
        aliases.discard(best_name)

        # Collect all offsets
        offsets = [e.get("absolute_offset", 0) for e in group if e.get("absolute_offset") is not None]

        canonical.append({
            "name": best_name,
            "type": etype,
            "aliases": sorted(aliases),
            "mention_count": len(group),
            "absolute_offsets": offsets,
            "_norm": norm_name,
        })

    return canonical


def exact_dedup_concepts(raw_concepts: list[dict]) -> list[dict]:
    """Group concepts by normalized term."""
    groups: dict[str, list[dict]] = {}
    for c in raw_concepts:
        key = _normalize(c.get("term", ""))
        groups.setdefault(key, []).append(c)

    canonical: list[dict] = []
    for norm_term, group in groups.items():
        term_counts: dict[str, int] = {}
        for c in group:
            t = c.get("term", "")
            term_counts[t] = term_counts.get(t, 0) + 1
        best_term = max(term_counts, key=lambda x: term_counts[x])

        # Keep longest/best definition_excerpt
        best_def = max(
            (c.get("definition_excerpt", "") for c in group),
            key=len,
            default="",
        )
        offsets = [c.get("absolute_offset", 0) for c in group if c.get("absolute_offset") is not None]

        canonical.append({
            "term": best_term,
            "definition_excerpt": best_def,
            "mention_count": len(group),
            "absolute_offsets": offsets,
            "_norm": norm_term,
        })

    return canonical


# ---------------------------------------------------------------------------
# Step 2.3 — Embedding-based deduplication
# ---------------------------------------------------------------------------

async def embedding_dedup_entities(
    entities: list[dict],
    embedding_provider: EmbeddingProvider,
) -> list[dict]:
    """
    Merge entities whose name embeddings are very similar (> MERGE_THRESHOLD)
    and have the same type. Returns a reduced list of canonical entities.
    """
    if len(entities) <= 1:
        return entities

    names = [e["name"] for e in entities]
    try:
        vectors = await embedding_provider.embed_batch(names)
    except Exception as exc:
        logger.warning(f"MRP REDUCE embedding dedup failed: {exc}. Skipping.")
        return entities

    n = len(entities)
    merged_into: dict[int, int] = {}  # index → canonical index

    def _root(i: int) -> int:
        while i in merged_into:
            i = merged_into[i]
        return i

    auto_merge_pairs: list[tuple[int, int]] = []
    ambiguous_pairs: list[tuple[int, int]] = []

    for i in range(n):
        for j in range(i + 1, n):
            if entities[i]["type"] != entities[j]["type"]:
                continue
            sim = _cosine(vectors[i], vectors[j])
            if sim >= MERGE_THRESHOLD:
                auto_merge_pairs.append((i, j))
            elif sim >= AMBIGUOUS_LOW:
                ambiguous_pairs.append((i, j))

    # Apply auto-merges
    for i, j in auto_merge_pairs:
        ri, rj = _root(i), _root(j)
        if ri != rj:
            # Merge lower-mention into higher-mention
            if entities[ri]["mention_count"] >= entities[rj]["mention_count"]:
                merged_into[rj] = ri
            else:
                merged_into[ri] = rj

    # Collect ambiguous pairs not already merged
    still_ambiguous = [
        (i, j) for i, j in ambiguous_pairs if _root(i) != _root(j)
    ]

    return merged_into, still_ambiguous, vectors, entities


async def resolve_ambiguous_entities(
    llm: LLMProvider,
    entities: list[dict],
    ambiguous_pairs: list[tuple[int, int]],
    merged_into: dict[int, int],
) -> dict[int, int]:
    """
    Send ambiguous entity pairs to LLM for batch disambiguation.
    Returns updated merged_into dict.
    """
    if not ambiguous_pairs:
        return merged_into

    def _root(i):
        while i in merged_into:
            i = merged_into[i]
        return i

    lines = []
    for k, (i, j) in enumerate(ambiguous_pairs):
        lines.append(
            f"{k + 1}. \"{entities[i]['name']}\" ({entities[i]['type']}) vs "
            f"\"{entities[j]['name']}\" ({entities[j]['type']})"
        )

    prompt = (
        "For each pair below, determine if they refer to the same real-world entity.\n"
        "Return a JSON array of exactly " + str(len(ambiguous_pairs)) + " booleans "
        "(true = same entity, false = different).\n"
        "Return ONLY the JSON array.\n\n" + "\n".join(lines)
    )

    try:
        raw = await asyncio.wait_for(
            llm.generate(prompt, system="You are a named-entity resolution assistant. Return only JSON.", temperature=0.0),
            timeout=60,
        )
        cleaned = raw.strip().strip("```json").strip("```").strip()
        decisions: list[bool] = json.loads(cleaned)
        for k, (i, j) in enumerate(ambiguous_pairs):
            if k < len(decisions) and decisions[k]:
                ri, rj = _root(i), _root(j)
                if ri != rj:
                    if entities[ri]["mention_count"] >= entities[rj]["mention_count"]:
                        merged_into[rj] = ri
                    else:
                        merged_into[ri] = rj
    except Exception as exc:
        logger.warning(f"MRP REDUCE ambiguous resolution failed: {exc}. Skipping.")

    return merged_into


def _apply_merges(entities: list[dict], merged_into: dict[int, int]) -> list[dict]:
    """Apply merge map to produce final deduplicated entity list."""
    def _root(i):
        while i in merged_into:
            i = merged_into[i]
        return i

    roots = set(_root(i) for i in range(len(entities)))
    result = []
    for ri in roots:
        canonical = dict(entities[ri])
        # Merge all aliases and mention_counts from merged-in entities
        for i, e in enumerate(entities):
            if i != ri and _root(i) == ri:
                canonical["mention_count"] = canonical.get("mention_count", 0) + e.get("mention_count", 0)
                canonical["aliases"] = list(set(canonical.get("aliases", [])) | set(e.get("aliases", [])) | {e["name"]})
                canonical["absolute_offsets"] = canonical.get("absolute_offsets", []) + e.get("absolute_offsets", [])
        result.append(canonical)
    return result


# ---------------------------------------------------------------------------
# Step 2.5 — KB reconciliation
# ---------------------------------------------------------------------------

async def reconcile_with_kb(
    session: AsyncSession,
    entities: list[dict],
    concepts: list[dict],
    embedding_provider: EmbeddingProvider,
    source,
) -> dict[str, dict]:
    """
    For each canonical entity/concept, search existing wiki pages.
    Returns {item_name: {"action": "CREATE"|"UPDATE"|"MAYBE", "page_slug": str|None, "similarity": float}}
    """
    from app.services import wiki_service

    scope_type = source.scope_type or "global"
    scope_id = source.scope_id

    all_items = [("entity", e["name"], e) for e in entities] + \
                [("concept", c["term"], c) for c in concepts]

    reconciliation: dict[str, dict] = {}

    if not all_items:
        return reconciliation

    # Batch-embed all query texts in a single API call, then search DB sequentially.
    # Sequential DB access avoids concurrent AsyncSession errors.
    query_texts = [
        (f"{name}: {item['definition_excerpt'][:200]}" if itype == "concept" and item.get("definition_excerpt") else name)[:4000]
        for itype, name, item in all_items
    ]
    try:
        vectors = await embedding_provider.embed_batch(query_texts)
    except Exception as exc:
        logger.warning(f"MRP REDUCE kb reconcile embed_batch failed: {exc}. All items → CREATE.")
        return {name: {"action": "CREATE", "page_slug": None, "similarity": 0.0} for _, name, _ in all_items}

    for (_, name, _), vec in zip(all_items, vectors):
        try:
            hits = await wiki_service.search_pages_semantic(
                session, vec, top_k=3, scope_type=scope_type, scope_id=scope_id,
            )
        except Exception as exc:
            logger.debug(f"MRP REDUCE kb reconcile failed for '{name}': {exc}")
            reconciliation[name] = {"action": "CREATE", "page_slug": None, "similarity": 0.0}
            continue

        if not hits:
            reconciliation[name] = {"action": "CREATE", "page_slug": None, "similarity": 0.0}
            continue

        top_page, top_sim = hits[0]
        if top_sim >= KB_UPDATE_THRESHOLD:
            reconciliation[name] = {"action": "UPDATE", "page_slug": top_page.slug, "similarity": top_sim}
        elif top_sim >= KB_MAYBE_THRESHOLD:
            reconciliation[name] = {"action": "MAYBE", "page_slug": top_page.slug, "similarity": top_sim,
                                    "_page_title": top_page.title}
        else:
            reconciliation[name] = {"action": "CREATE", "page_slug": None, "similarity": top_sim}

    # Batch-resolve MAYBE items with LLM
    maybe_items = [(name, rec) for name, rec in reconciliation.items() if rec["action"] == "MAYBE"]
    if maybe_items:
        await _resolve_maybe_items(reconciliation, maybe_items, embedding_provider)

    return reconciliation


async def _resolve_maybe_items(
    reconciliation: dict,
    maybe_items: list[tuple[str, dict]],
    embedding_provider,  # not used but kept for future re-ranking
):
    """Downgrade unresolved MAYBE items to CREATE (conservative default)."""
    for name, _ in maybe_items:
        reconciliation[name]["action"] = "CREATE"


# ---------------------------------------------------------------------------
# Step 2.7 — Planning call
# ---------------------------------------------------------------------------

PLANNING_SYSTEM = """\
You are a wiki compilation planner. Given extracted entities and their relationship
to an existing knowledge base, produce a compilation plan. Return ONLY valid JSON.
"""

PLANNING_PROMPT_TEMPLATE = """\
## Source document
Title: {source_title}
Knowledge type: {kt_context}
Strategy: {strategy}

## Extracted entities (with mention counts)
{entities_summary}

## Extracted concepts (with mention counts)
{concepts_summary}

## KB reconciliation results
{kb_reconciliation}

Produce a JSON compilation plan:

{{
  "pages": [
    {{
      "action": "CREATE",
      "slug": "concept/example-name",
      "title": "Example Page Title",
      "page_type": "entity | concept | topic | source",
      "entity_names": ["entity or concept name covered by this page"],
      "related_kb_pages": ["existing-slug-1"],
      "priority": 1
    }}
  ],
  "source_page_slug": "source/short-doc-slug",
  "estimated_page_count": 5,
  "compilation_notes": "any important notes for the compiler"
}}

Rules:
- action must be "CREATE" or "UPDATE"
- For UPDATE, slug must be an existing wiki page slug (from KB reconciliation above)
- For CREATE, slug must be new (type-prefixed, lowercase, hyphenated)
- Always include exactly one page with page_type "source" for the document itself
- Group closely related small entities onto the same page (max 3-4 per page)
- priority 1 = highest importance (process first)
- entity_names must match the names in the entities/concepts lists above
- Target approximately {target_page_count} total pages (feel free to create more if the document is dense and contains many distinct concepts).
- Return ONLY the JSON object
"""


async def run_planning_call(
    llm: LLMProvider,
    source,
    strategy: str,
    canonical_entities: list[dict],
    canonical_concepts: list[dict],
    reconciliation: dict[str, dict],
    kt_name: Optional[str],
    kt_desc: Optional[str],
) -> dict:
    """Single LLM call to produce the Compilation Plan JSON."""
    n_chars = len(source.full_text or "")
    # Calculate target based on the actual number of extracted concepts rather than just document length
    total_extracted_items = len(canonical_entities) + len(canonical_concepts)
    
    if strategy == "single_pass":
        # Usually 1 page per 2-3 items, minimum 3, maximum 15
        target_pages = max(3, min(15, total_extracted_items // 2))
    elif strategy == "standard":
        target_pages = max(8, min(30, total_extracted_items // 3))
    else:
        target_pages = max(15, min(60, total_extracted_items // 3))

    kt_context = kt_name or "(no specific knowledge type)"
    if kt_desc:
        kt_context += f" — {kt_desc}"

    def _fmt_entity(e: dict) -> str:
        aliases = ", ".join(e["aliases"][:3]) if e.get("aliases") else ""
        kb = reconciliation.get(e["name"], {})
        kb_info = f"→ {kb['action']} {kb.get('page_slug', '')}" if kb else "→ CREATE"
        return (
            f"  - {e['name']} ({e['type']}, {e['mention_count']} mentions"
            + (f", aliases: {aliases}" if aliases else "")
            + f") {kb_info}"
        )

    def _fmt_concept(c: dict) -> str:
        kb = reconciliation.get(c["term"], {})
        kb_info = f"→ {kb['action']} {kb.get('page_slug', '')}" if kb else "→ CREATE"
        return f"  - {c['term']} ({c['mention_count']} mentions) {kb_info}"

    # Sort by mention count descending to ensure the planner sees the most important items
    sorted_entities = sorted(canonical_entities, key=lambda x: x.get("mention_count", 0), reverse=True)
    sorted_concepts = sorted(canonical_concepts, key=lambda x: x.get("mention_count", 0), reverse=True)

    entities_summary = "\n".join(_fmt_entity(e) for e in sorted_entities[:100]) or "  (none)"
    concepts_summary = "\n".join(_fmt_concept(c) for c in sorted_concepts[:100]) or "  (none)"

    kb_lines = []
    for name, rec in reconciliation.items():
        if rec["action"] == "UPDATE":
            kb_lines.append(f"  - UPDATE: {name} → {rec['page_slug']} (sim={rec['similarity']:.2f})")
    kb_reconciliation = "\n".join(kb_lines) if kb_lines else "  (all items are new)"

    prompt = PLANNING_PROMPT_TEMPLATE.format(
        source_title=source.title or source.file_name or str(source.id),
        kt_context=kt_context,
        strategy=strategy,
        entities_summary=entities_summary,
        concepts_summary=concepts_summary,
        kb_reconciliation=kb_reconciliation,
        target_page_count=target_pages,
    )

    raw = await asyncio.wait_for(
        llm.generate(prompt, system=PLANNING_SYSTEM, temperature=0.1),
        timeout=120,
    )

    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        plan = json.loads(cleaned)
    except json.JSONDecodeError:
        last_brace = cleaned.rfind("}")
        if last_brace != -1:
            plan = json.loads(cleaned[: last_brace + 1])
        else:
            raise

    return plan


# ---------------------------------------------------------------------------
# Phase 2 orchestrator
# ---------------------------------------------------------------------------

async def run_reduce_phase(
    session: AsyncSession,
    source,
    chunk_extracts: list,
    llm: LLMProvider,
    embedding_provider: EmbeddingProvider,
    kt_name: Optional[str],
    kt_desc: Optional[str],
    tracker: ProgressTracker,
) -> "SourceCompilationPlan":
    """
    Run full Phase 2 (REDUCE).

    Returns a SourceCompilationPlan ORM object with status='pending_review'.
    Uses INSERT ... ON CONFLICT DO UPDATE so re-runs safely overwrite old plans.
    """
    from app.database.models import Source, SourceCompilationPlan

    await tracker.update(66, "Collecting extractions...")

    # 2.1 Collect raw items
    raw_entities, raw_concepts, raw_claims = collect_raw_items(chunk_extracts)
    logger.info(f"MRP REDUCE: {len(raw_entities)} raw entities, {len(raw_concepts)} concepts, {len(raw_claims)} claims")

    # 2.2 Exact dedup
    canonical_entities = exact_dedup_entities(raw_entities)
    canonical_concepts = exact_dedup_concepts(raw_concepts)
    logger.info(f"MRP REDUCE after exact-dedup: {len(canonical_entities)} entities, {len(canonical_concepts)} concepts")

    await tracker.update(68, "Deduplicating entities...")

    # 2.3 Embedding dedup for entities
    if len(canonical_entities) > 1 and embedding_provider is not None:
        try:
            result = await embedding_dedup_entities(canonical_entities, embedding_provider)
            if isinstance(result, tuple):
                merged_into, ambiguous_pairs, vectors, canonical_entities = result
                # 2.4 LLM resolution for ambiguous pairs
                merged_into = await resolve_ambiguous_entities(llm, canonical_entities, ambiguous_pairs, merged_into)
                canonical_entities = _apply_merges(canonical_entities, merged_into)
                logger.info(f"MRP REDUCE after embedding-dedup: {len(canonical_entities)} entities")
        except Exception as exc:
            logger.warning(f"MRP REDUCE embedding dedup error: {exc}. Continuing with exact-dedup result.")

    await tracker.update(72, "Reconciling with knowledge base...")

    # 2.5 KB reconciliation
    reconciliation: dict[str, dict] = {}
    if embedding_provider is not None:
        try:
            reconciliation = await reconcile_with_kb(
                session, canonical_entities, canonical_concepts, embedding_provider, source,
            )
        except Exception as exc:
            logger.warning(f"MRP REDUCE KB reconciliation failed: {exc}. All items will be CREATE.")

    await tracker.update(76, "Generating compilation plan...")

    # 2.7 Planning call
    strategy = source.pipeline_strategy or "standard"
    plan_dict = await run_planning_call(
        llm=llm,
        source=source,
        strategy=strategy,
        canonical_entities=canonical_entities,
        canonical_concepts=canonical_concepts,
        reconciliation=reconciliation,
        kt_name=kt_name,
        kt_desc=kt_desc,
    )

    # Attach claim evidence to plan (so REFINE can access claims per entity)
    plan_dict["_claims"] = raw_claims
    plan_dict["_entities"] = canonical_entities
    plan_dict["_concepts"] = canonical_concepts

    # 2.8 Persist plan (upsert: safe to re-run)
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    existing = (await session.execute(
        select(SourceCompilationPlan).where(SourceCompilationPlan.source_id == source.id)
    )).scalar_one_or_none()

    if existing:
        existing.plan_json = plan_dict
        existing.status = "pending_review"
        existing.reviewed_by = None
        existing.review_note = None
        existing.reviewed_at = None
        plan_row = existing
    else:
        plan_row = SourceCompilationPlan(
            source_id=source.id,
            plan_json=plan_dict,
            status="pending_review",
        )
        session.add(plan_row)

    src = await session.get(Source, source.id)
    if src:
        src.pipeline_phase = "plan_review"

    await session.commit()
    logger.info(f"MRP REDUCE complete: plan with {len(plan_dict.get('pages', []))} pages for source={source.id}")

    return plan_row
