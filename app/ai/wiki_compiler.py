"""
Wiki Compiler — turns a raw source document into wiki page operations via LLM.

The compiler reads the full source text plus a compact view of the existing
wiki (slug + summary index, plus the top-K most-relevant existing pages by
embedding similarity) and asks the LLM to emit a JSON list of operations:

    [
      {"op": "create", "slug": "...", "title": "...", "page_type": "...", "content_md": "...", "summary": "..."},
      {"op": "update", "slug": "...", "new_content_md": "...", "summary": "..."},
      {"op": "log",    "entry": "..."}
    ]

Operations are applied transactionally via wiki_service. Every created or
updated page is then re-embedded and the wikilink graph is refreshed. The
compiler is provider-agnostic: it goes through ProviderRegistry which resolves
the configured LLM and embedding providers from app_config at runtime.
"""

import json
import re
import uuid
from typing import Any, Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.registry import ProviderRegistry
from app.database.models import Source, WikiPage
from app.services import wiki_service


# Slug must be a-z 0-9 and `/_-` only — kept narrow so they're URL-safe and stable.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_/-]*[a-z0-9]$")

MAX_DOCUMENT_CHARS = 60_000   # truncate very long sources before sending to LLM
MAX_INDEX_PAGES_LISTED = 200  # how many existing pages to enumerate in the prompt
TOP_K_RELEVANT = 8            # how many semantically-relevant pages to show in full


PROMPT_TEMPLATE = """\
You are a knowledge-base compiler for an enterprise wiki. Your job is to read
a single new source document and decide how it should be integrated into the
existing wiki — what new pages to create, which existing pages to update, and
what to record in the log.

The wiki is a collection of interlinked markdown pages. Pages are stable,
permanent, and may be updated repeatedly as new sources arrive. They are NOT
per-document summaries — they're synthesis artifacts that compound over time.

# Page types
- `entity`  — a specific named thing: a person, organization, system, product, place.
- `concept` — a process, policy, rule, methodology, or other reusable idea.
- `topic`   — a broader subject area that aggregates related entities/concepts.
- `source`  — a one-page summary of THIS document. Always create exactly one of these.

# Slug rules
- Slugs are URL-safe, lowercase, hyphenated, and PREFIXED by type:
  `entity/jane-doe`, `concept/expense-approval`, `topic/customer-onboarding`,
  `source/<short-doc-slug>`.
- Pick stable, generalizable slugs that future sources will naturally update,
  not slugs tied to this specific document.

# Wikilinks
- Use `[[slug]]` to link between pages. Example: `... approved by [[entity/cfo]] ...`
- Use `[[slug|display text]]` if the natural language differs from the slug.
- Always link entities/concepts mentioned on a page to their dedicated pages.
- It's fine to link to a page that doesn't exist yet — the next source might create it.

# Decision rules
- Prefer UPDATE over CREATE when the existing wiki already has a relevant page.
  Merge new facts into existing prose; don't just append. Resolve contradictions
  by preferring more recent / more specific data, but keep nuance.
- CREATE only when the entity/concept doesn't yet have its own page.
- Create one `source` page summarizing this document, with key facts and links
  out to the entity/concept pages it touches.
- Touch as many pages as the document warrants — typically 5-15 ops per source.
- If an existing page is irrelevant to this document, DO NOT touch it.

# Output format
Return ONLY a single JSON object, no markdown fences, no commentary:

{{
  "operations": [
    {{"op": "create", "slug": "entity/...", "title": "...", "page_type": "entity",
      "content_md": "# ...\\n\\n...", "summary": "one-line summary"}},
    {{"op": "update", "slug": "concept/...", "new_content_md": "# ...\\n\\n...",
      "summary": "one-line summary", "title": "..."}},
    {{"op": "log", "entry": "ingested <doc title>: created N pages, updated M"}}
  ]
}}

Always include exactly one log op summarizing what you did.

# Document context
{kt_context}
Document title: {doc_title}

# Existing wiki — index of all pages (slug — summary)
{wiki_index}

# Existing wiki — relevant pages in full (consider updating these)
{relevant_pages}

# Document content (truncated if very long)
{document_text}
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def compile_source_into_wiki(
    session: AsyncSession,
    source: Source,
    full_text: str,
    knowledge_type_slug: Optional[str],
    knowledge_type_name: Optional[str],
    knowledge_type_description: Optional[str],
) -> dict:
    """
    Run the wiki compiler for one source. Persists changes via `session`
    (caller is responsible for the surrounding transaction/commit).

    Returns: {"pages_created": int, "pages_updated": int, "log_entry": str}
    """
    registry = ProviderRegistry(session)

    embedding_provider = await registry.get_embedding(task="document")
    llm = await registry.get_llm()

    truncated_text = full_text[:MAX_DOCUMENT_CHARS]
    if len(full_text) > MAX_DOCUMENT_CHARS:
        truncated_text += "\n\n[…document truncated for compilation…]"

    # 1. Build context: index listing + top-K relevant pages by source embedding.
    wiki_index_md = await _render_wiki_index(session)
    relevant_md = await _render_relevant_pages(
        session, embedding_provider, full_text, knowledge_type_slug
    )
    kt_context = _format_kt_context(knowledge_type_name, knowledge_type_description)

    prompt = PROMPT_TEMPLATE.format(
        kt_context=kt_context,
        doc_title=source.title or source.file_name or str(source.id),
        wiki_index=wiki_index_md or "_(empty)_",
        relevant_pages=relevant_md or "_(none)_",
        document_text=truncated_text,
    )

    # 2. Call LLM. Low temperature for structured output reliability.
    try:
        raw = await llm.generate(prompt=prompt, temperature=0.1, max_tokens=8192)
    except Exception as e:
        logger.warning(f"Wiki compile LLM call failed for source {source.id}: {e}")
        return {"pages_created": 0, "pages_updated": 0, "log_entry": ""}

    operations = _parse_operations(raw)
    if not operations:
        logger.warning(f"Wiki compile produced no operations for source {source.id}")
        return {"pages_created": 0, "pages_updated": 0, "log_entry": ""}

    # 3. Apply operations.
    created = 0
    updated = 0
    log_entry = ""
    touched_slugs: list[str] = []

    for op in operations:
        kind = op.get("op")
        try:
            if kind == "create":
                slug = _validate_slug(op.get("slug"))
                if not slug:
                    continue
                if await wiki_service.get_page_by_slug(session, slug) is not None:
                    # Slug collision — fall through to update path.
                    await _apply_update(session, op, source, knowledge_type_slug)
                    updated += 1
                else:
                    await wiki_service.apply_create(
                        session,
                        slug=slug,
                        title=str(op.get("title") or slug.split("/")[-1]),
                        page_type=str(op.get("page_type") or "concept"),
                        content_md=str(op.get("content_md") or ""),
                        summary=str(op.get("summary") or ""),
                        knowledge_type_slugs=[knowledge_type_slug] if knowledge_type_slug else [],
                        source_ids=[source.id],
                    )
                    created += 1
                touched_slugs.append(slug)

            elif kind == "update":
                slug = _validate_slug(op.get("slug"))
                if not slug:
                    continue
                applied = await _apply_update(session, op, source, knowledge_type_slug)
                if applied:
                    updated += 1
                    touched_slugs.append(slug)

            elif kind == "log":
                log_entry = str(op.get("entry") or "").strip()

            else:
                logger.debug(f"Skipping unknown wiki op: {op!r}")

        except Exception as e:
            logger.warning(f"Failed to apply wiki op {op!r}: {e}")
            continue

    # 4. Re-embed touched pages (batch).
    if touched_slugs:
        await _reembed_pages(session, embedding_provider, touched_slugs)

    # 5. Regenerate the catalog and append a log line.
    if created or updated:
        await wiki_service.regenerate_index(session)
    final_log = log_entry or (
        f"ingested {source.title or source.file_name or source.id}: "
        f"+{created} pages, ~{updated} updated"
    )
    await wiki_service.append_log(session, final_log)

    logger.info(
        f"Wiki compile done for source {source.id}: "
        f"created={created} updated={updated}"
    )
    return {"pages_created": created, "pages_updated": updated, "log_entry": final_log}


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

async def _apply_update(
    session: AsyncSession,
    op: dict[str, Any],
    source: Source,
    knowledge_type_slug: Optional[str],
) -> Optional[WikiPage]:
    """Translate a single 'update' op into a wiki_service.apply_update call."""
    slug = _validate_slug(op.get("slug"))
    if not slug:
        return None
    new_content = op.get("new_content_md") or op.get("content_md") or ""
    return await wiki_service.apply_update(
        session,
        slug=slug,
        new_content_md=str(new_content),
        summary=str(op["summary"]) if op.get("summary") is not None else None,
        title=str(op["title"]) if op.get("title") is not None else None,
        add_knowledge_type_slug=knowledge_type_slug,
        add_source_id=source.id,
    )


def _validate_slug(slug: Any) -> Optional[str]:
    """Return a clean slug or None if invalid. Reserved slugs are rejected."""
    if not isinstance(slug, str):
        return None
    s = slug.strip().lower()
    if not s or s in (wiki_service.INDEX_SLUG, wiki_service.LOG_SLUG):
        return None
    if not _SLUG_RE.match(s):
        return None
    return s


def _format_kt_context(name: Optional[str], description: Optional[str]) -> str:
    if not name:
        return ""
    line = f'Document category: "{name}"'
    if description:
        line += f" — {description}"
    line += (
        "\nFavor entity/concept slugs and labels that fit this category. "
        "Reuse existing pages when the same entities appear under this category."
    )
    return line


async def _render_wiki_index(session: AsyncSession) -> str:
    """Render existing pages as `slug — summary` lines, capped."""
    stmt = (
        select(WikiPage.slug, WikiPage.page_type, WikiPage.summary)
        .where(WikiPage.slug.notin_([wiki_service.INDEX_SLUG, wiki_service.LOG_SLUG]))
        .order_by(WikiPage.page_type, WikiPage.slug)
        .limit(MAX_INDEX_PAGES_LISTED)
    )
    rows = (await session.execute(stmt)).all()
    if not rows:
        return ""
    return "\n".join(
        f"- {r.slug} ({r.page_type}) — {r.summary or ''}".rstrip(" —")
        for r in rows
    )


async def _render_relevant_pages(
    session: AsyncSession,
    embedding_provider,
    full_text: str,
    knowledge_type_slug: Optional[str],
) -> str:
    """Embed the source's leading text and pick top-K most-relevant existing pages."""
    sample = full_text[:6000]
    if not sample.strip():
        return ""
    try:
        query_emb = await embedding_provider.embed(sample)
    except Exception as e:
        logger.debug(f"Wiki compile: failed to embed source for context lookup: {e}")
        return ""

    allowed = [knowledge_type_slug] if knowledge_type_slug else None
    hits = await wiki_service.search_pages_semantic(
        session, query_emb, top_k=TOP_K_RELEVANT, allowed_kt_slugs=allowed,
    )
    if not hits:
        return ""

    parts: list[str] = []
    for page, sim in hits:
        body = page.content_md or ""
        if len(body) > 2000:
            body = body[:2000] + "\n\n[…page truncated…]"
        parts.append(
            f"### {page.slug} (similarity={sim:.2f})\n\n{body}"
        )
    return "\n\n---\n\n".join(parts)


def _parse_operations(raw: str) -> list[dict[str, Any]]:
    """
    Tolerantly extract the operations array from an LLM response. Handles
    optional ```json fences and trailing prose.
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Fall back to the largest JSON object in the response.
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return []
        try:
            data = json.loads(text[start:end + 1])
        except json.JSONDecodeError as e:
            logger.warning(f"Wiki compile: could not parse JSON: {e}; head={text[:200]!r}")
            return []

    if isinstance(data, dict):
        ops = data.get("operations")
    elif isinstance(data, list):
        ops = data
    else:
        ops = None
    return [op for op in (ops or []) if isinstance(op, dict)]


async def _reembed_pages(
    session: AsyncSession,
    embedding_provider,
    slugs: list[str],
) -> None:
    """Re-embed all pages in `slugs` in one batch and persist."""
    unique = list(dict.fromkeys(slugs))
    if not unique:
        return
    rows = (await session.execute(
        select(WikiPage).where(WikiPage.slug.in_(unique))
    )).scalars().all()
    if not rows:
        return

    # Embed using `title + summary + content` so search hits both metadata and body.
    inputs = [
        f"{p.title}\n\n{p.summary or ''}\n\n{p.content_md or ''}"[:8000]
        for p in rows
    ]
    try:
        vectors = await embedding_provider.embed_batch(inputs)
    except Exception as e:
        logger.warning(f"Wiki compile: re-embed failed for {len(rows)} pages: {e}")
        return
    for page, vec in zip(rows, vectors):
        page.embedding = vec
    await session.flush()
