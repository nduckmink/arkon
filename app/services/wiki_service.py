"""
Wiki Service — CRUD, semantic search, and wikilink graph for WikiPage.

The wiki is the LLM-compiled knowledge layer. It replaces chunk-based RAG.
Each page is markdown that may contain `[[slug]]` wikilinks; after every
upsert, refresh_links() re-parses the content and rewrites the wiki_links
edge table so 1-2 hop graph queries (backlinks, neighborhood) stay fast in
PostgreSQL — no separate graph DB needed.

Scope support: every page belongs to a scope (global or workspace). Query
functions accept scope_type/scope_id to isolate results. Default is global.
"""

import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from loguru import logger
from sqlalchemy import and_, delete, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import WikiLink, WikiPage


# Reserved page slugs — these are regular WikiPage rows but treated specially.
INDEX_SLUG = "_index"
LOG_SLUG = "_log"

# Recognized page types — used for filtering and prompt hints to the compiler.
PAGE_TYPES = {"entity", "concept", "source", "topic", "index", "log"}

# `[[slug]]` or `[[slug|display text]]` — captures the slug only.
_WIKILINK_RE = re.compile(r"\[\[([^\]\|]+)(?:\|[^\]]*)?]]")


# ---------------------------------------------------------------------------
# Scope filter helper
# ---------------------------------------------------------------------------

def _scope_filter(scope_type: str = "global", scope_id: Optional[uuid.UUID] = None):
    """Return SQLAlchemy WHERE clauses for scope filtering."""
    if scope_id:
        return and_(WikiPage.scope_type == scope_type, WikiPage.scope_id == scope_id)
    return and_(WikiPage.scope_type == scope_type, WikiPage.scope_id.is_(None))


# ---------------------------------------------------------------------------
# Wikilink parsing & graph maintenance
# ---------------------------------------------------------------------------

def extract_wikilinks(content_md: str) -> list[str]:
    """Return the list of slugs referenced by `[[slug]]` patterns, deduped."""
    if not content_md:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for match in _WIKILINK_RE.finditer(content_md):
        slug = match.group(1).strip()
        if slug and slug not in seen:
            seen.add(slug)
            out.append(slug)
    return out


async def refresh_links(
    session: AsyncSession,
    from_slug: str,
    content_md: str,
) -> None:
    """
    Replace all outgoing edges from `from_slug` with the wikilinks parsed from
    its current `content_md`. Self-links and links pointing to the page itself
    are dropped to keep the graph sane.
    """
    await session.execute(
        delete(WikiLink).where(WikiLink.from_slug == from_slug)
    )
    targets = [s for s in extract_wikilinks(content_md) if s != from_slug]
    if not targets:
        return
    await session.execute(
        pg_insert(WikiLink)
        .values([{"from_slug": from_slug, "to_slug": t} for t in targets])
        .on_conflict_do_nothing()
    )


async def get_backlinks(session: AsyncSession, slug: str) -> list[str]:
    """Slugs of pages that link to `slug`."""
    result = await session.execute(
        select(WikiLink.from_slug).where(WikiLink.to_slug == slug)
    )
    return [row[0] for row in result.all()]


async def get_outlinks(session: AsyncSession, slug: str) -> list[str]:
    """Slugs that `slug` links to."""
    result = await session.execute(
        select(WikiLink.to_slug).where(WikiLink.from_slug == slug)
    )
    return [row[0] for row in result.all()]


async def get_neighborhood(
    session: AsyncSession,
    slug: str,
    depth: int = 1,
) -> dict:
    """
    Return nodes (slug, title, page_type) and edges within `depth` hops of `slug`.
    Uses an undirected recursive CTE — useful for Obsidian-style graph view.
    """
    depth = max(1, min(depth, 3))  # cap at 3 hops to keep queries cheap
    # Recursive CTE walking both directions; stop at depth.
    cte_sql = text(
        """
        WITH RECURSIVE walk(slug, dist) AS (
            SELECT CAST(:start AS varchar), 0
          UNION
            SELECT
              CASE WHEN l.from_slug = w.slug THEN l.to_slug ELSE l.from_slug END,
              w.dist + 1
            FROM walk w
            JOIN wiki_links l
              ON l.from_slug = w.slug OR l.to_slug = w.slug
            WHERE w.dist < :depth
        )
        SELECT DISTINCT slug FROM walk
        """
    )
    rows = await session.execute(cte_sql, {"start": slug, "depth": depth})
    slugs = [r[0] for r in rows.all()]
    if not slugs:
        return {"nodes": [], "edges": []}

    pages_result = await session.execute(
        select(WikiPage.slug, WikiPage.title, WikiPage.page_type)
        .where(WikiPage.slug.in_(slugs))
    )
    nodes = [
        {"slug": r.slug, "title": r.title, "page_type": r.page_type}
        for r in pages_result.all()
    ]
    edges_result = await session.execute(
        select(WikiLink.from_slug, WikiLink.to_slug)
        .where(and_(WikiLink.from_slug.in_(slugs), WikiLink.to_slug.in_(slugs)))
    )
    edges = [{"from": r.from_slug, "to": r.to_slug} for r in edges_result.all()]
    return {"nodes": nodes, "edges": edges}


# ---------------------------------------------------------------------------
# Page CRUD
# ---------------------------------------------------------------------------

async def get_page_by_slug(
    session: AsyncSession,
    slug: str,
    allowed_kt_slugs: Optional[list[str]] = None,
    scope_type: str = "global",
    scope_id: Optional[uuid.UUID] = None,
) -> Optional[WikiPage]:
    """
    Fetch a page by slug within a specific scope. If `allowed_kt_slugs` is
    given (RBAC), only return the page when it overlaps the allowed set or is
    a reserved slug.
    """
    stmt = select(WikiPage).where(
        WikiPage.slug == slug,
        _scope_filter(scope_type, scope_id),
    )
    result = await session.execute(stmt)
    page = result.scalar_one_or_none()
    if page is None:
        return None
    if allowed_kt_slugs is None or slug in (INDEX_SLUG, LOG_SLUG):
        return page
    if not page.knowledge_type_slugs:
        return page
    if any(s in allowed_kt_slugs for s in page.knowledge_type_slugs):
        return page
    return None


async def list_pages(
    session: AsyncSession,
    page_type: Optional[str] = None,
    knowledge_type_slug: Optional[str] = None,
    allowed_kt_slugs: Optional[list[str]] = None,
    limit: int = 50,
    offset: int = 0,
    scope_type: str = "global",
    scope_id: Optional[uuid.UUID] = None,
) -> list[WikiPage]:
    """List pages with filtering within a specific scope. Reserved slugs excluded."""
    stmt = (
        select(WikiPage)
        .where(
            WikiPage.slug.notin_([INDEX_SLUG, LOG_SLUG]),
            _scope_filter(scope_type, scope_id),
        )
        .order_by(WikiPage.updated_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if page_type:
        stmt = stmt.where(WikiPage.page_type == page_type)
    if knowledge_type_slug:
        stmt = stmt.where(WikiPage.knowledge_type_slugs.any(knowledge_type_slug))
    if allowed_kt_slugs:
        stmt = stmt.where(
            or_(
                WikiPage.knowledge_type_slugs.overlap(allowed_kt_slugs),
                func.cardinality(WikiPage.knowledge_type_slugs) == 0,
            )
        )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def search_pages_semantic(
    session: AsyncSession,
    query_embedding: list[float],
    top_k: int = 10,
    allowed_kt_slugs: Optional[list[str]] = None,
    scope_type: str = "global",
    scope_id: Optional[uuid.UUID] = None,
) -> list[tuple[WikiPage, float]]:
    """
    Cosine-similarity search over wiki page embeddings within a scope.
    Returns (page, similarity) pairs sorted by similarity descending.
    """
    stmt = (
        select(
            WikiPage,
            (1 - WikiPage.embedding.cosine_distance(query_embedding)).label("similarity"),
        )
        .where(
            and_(
                WikiPage.embedding.is_not(None),
                WikiPage.slug.notin_([INDEX_SLUG, LOG_SLUG]),
                _scope_filter(scope_type, scope_id),
            )
        )
        .order_by(WikiPage.embedding.cosine_distance(query_embedding))
        .limit(top_k)
    )
    if allowed_kt_slugs:
        stmt = stmt.where(
            or_(
                WikiPage.knowledge_type_slugs.overlap(allowed_kt_slugs),
                func.cardinality(WikiPage.knowledge_type_slugs) == 0,
            )
        )
    result = await session.execute(stmt)
    return [(row[0], float(row[1])) for row in result.all()]


# ---------------------------------------------------------------------------
# Compiler ops application
# ---------------------------------------------------------------------------

async def apply_create(
    session: AsyncSession,
    slug: str,
    title: str,
    page_type: str,
    content_md: str,
    summary: str,
    knowledge_type_slugs: list[str],
    source_ids: list[uuid.UUID],
    embedding: Optional[list[float]] = None,
    scope_type: str = "global",
    scope_id: Optional[uuid.UUID] = None,
) -> WikiPage:
    """Insert a new page in the given scope. Conflicts raise — caller should use update."""
    page = WikiPage(
        slug=slug,
        title=title,
        page_type=page_type if page_type in PAGE_TYPES else "concept",
        content_md=content_md,
        summary=summary,
        knowledge_type_slugs=list(knowledge_type_slugs or []),
        source_ids=list(source_ids or []),
        embedding=embedding,
        scope_type=scope_type,
        scope_id=scope_id,
        version=1,
    )
    session.add(page)
    await session.flush()
    await refresh_links(session, slug, content_md)
    return page


async def apply_update(
    session: AsyncSession,
    slug: str,
    new_content_md: str,
    summary: Optional[str] = None,
    title: Optional[str] = None,
    add_knowledge_type_slug: Optional[str] = None,
    add_source_id: Optional[uuid.UUID] = None,
    embedding: Optional[list[float]] = None,
    scope_type: str = "global",
    scope_id: Optional[uuid.UUID] = None,
) -> Optional[WikiPage]:
    """
    Update an existing page atomically within the given scope:
      - Replace content_md with new_content_md.
      - Optionally update title/summary.
      - Union add_knowledge_type_slug into knowledge_type_slugs.
      - Append add_source_id to source_ids if not present.
      - Bump version, refresh updated_at, refresh embedding if supplied.
    Returns None if the page does not exist.
    """
    page = await get_page_by_slug(session, slug, scope_type=scope_type, scope_id=scope_id)
    if page is None:
        return None

    page.content_md = new_content_md
    if title is not None:
        page.title = title
    if summary is not None:
        page.summary = summary
    if add_knowledge_type_slug and add_knowledge_type_slug not in (page.knowledge_type_slugs or []):
        page.knowledge_type_slugs = [*(page.knowledge_type_slugs or []), add_knowledge_type_slug]
    if add_source_id and add_source_id not in (page.source_ids or []):
        page.source_ids = [*(page.source_ids or []), add_source_id]
    if embedding is not None:
        page.embedding = embedding
    page.version = (page.version or 1) + 1
    await session.flush()
    await refresh_links(session, slug, new_content_md)
    return page


async def upsert_page(
    session: AsyncSession,
    slug: str,
    title: str,
    page_type: str,
    content_md: str,
    summary: str,
    knowledge_type_slugs: list[str],
    source_ids: list[uuid.UUID],
    embedding: Optional[list[float]] = None,
    scope_type: str = "global",
    scope_id: Optional[uuid.UUID] = None,
) -> WikiPage:
    """Create-or-update by slug within a scope."""
    existing = await get_page_by_slug(session, slug, scope_type=scope_type, scope_id=scope_id)
    if existing is None:
        return await apply_create(
            session, slug, title, page_type, content_md, summary,
            knowledge_type_slugs, source_ids, embedding,
            scope_type=scope_type, scope_id=scope_id,
        )
    return await apply_update(
        session,
        slug=slug,
        new_content_md=content_md,
        summary=summary,
        title=title,
        add_knowledge_type_slug=knowledge_type_slugs[0] if knowledge_type_slugs else None,
        add_source_id=source_ids[0] if source_ids else None,
        embedding=embedding,
        scope_type=scope_type, scope_id=scope_id,
    ) or existing


# ---------------------------------------------------------------------------
# Reserved pages: _index and _log
# ---------------------------------------------------------------------------

async def regenerate_index(
    session: AsyncSession,
    scope_type: str = "global",
    scope_id: Optional[uuid.UUID] = None,
) -> WikiPage:
    """
    Rebuild the `_index` page within the given scope.
    Grouped by page_type, alphabetical within group. Excludes reserved slugs.
    """
    stmt = (
        select(WikiPage.slug, WikiPage.title, WikiPage.page_type, WikiPage.summary)
        .where(
            WikiPage.slug.notin_([INDEX_SLUG, LOG_SLUG]),
            _scope_filter(scope_type, scope_id),
        )
        .order_by(WikiPage.page_type, WikiPage.title)
    )
    rows = (await session.execute(stmt)).all()

    by_type: dict[str, list[tuple[str, str, str]]] = {}
    for r in rows:
        by_type.setdefault(r.page_type, []).append((r.slug, r.title, r.summary or ""))

    lines = ["# Wiki Index", ""]
    if not by_type:
        lines.append("_(empty — no pages yet)_")
    else:
        for ptype in sorted(by_type.keys()):
            lines.append(f"## {ptype.capitalize()}")
            lines.append("")
            for slug, title, summary in by_type[ptype]:
                summary_part = f" — {summary}" if summary else ""
                lines.append(f"- [[{slug}|{title}]]{summary_part}")
            lines.append("")

    new_md = "\n".join(lines).rstrip() + "\n"
    page = await get_page_by_slug(session, INDEX_SLUG, scope_type=scope_type, scope_id=scope_id)
    if page is None:
        page = WikiPage(
            slug=INDEX_SLUG,
            title="Wiki Index",
            page_type="index",
            content_md=new_md,
            summary="Catalog of all wiki pages",
            knowledge_type_slugs=[],
            source_ids=[],
            scope_type=scope_type,
            scope_id=scope_id,
        )
        session.add(page)
    else:
        page.content_md = new_md
        page.version = (page.version or 1) + 1
    await session.flush()
    return page


async def append_log(
    session: AsyncSession,
    entry: str,
    scope_type: str = "global",
    scope_id: Optional[uuid.UUID] = None,
) -> WikiPage:
    """
    Append a timestamped line to the `_log` page within the given scope.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    line = f"## [{ts}] {entry.strip()}"
    page = await get_page_by_slug(session, LOG_SLUG, scope_type=scope_type, scope_id=scope_id)
    if page is None:
        page = WikiPage(
            slug=LOG_SLUG,
            title="Wiki Log",
            page_type="log",
            content_md=f"# Wiki Log\n\n{line}\n",
            summary="Chronological activity log",
            knowledge_type_slugs=[],
            source_ids=[],
            scope_type=scope_type,
            scope_id=scope_id,
        )
        session.add(page)
    else:
        existing = page.content_md or "# Wiki Log\n"
        if "_(empty" in existing:
            existing = "# Wiki Log\n"
        page.content_md = existing.rstrip() + f"\n\n{line}\n"
        page.version = (page.version or 1) + 1
    await session.flush()
    return page


# ---------------------------------------------------------------------------
# Page deletion — cascade cleanup
# ---------------------------------------------------------------------------

async def delete_page_cascade(
    session: AsyncSession,
    slug: str,
) -> None:
    """
    Delete a wiki page and cascade-cleanup all references:
    1. Delete all outgoing links from this page
    2. Delete all incoming links pointing to this page
    3. Remove [[slug]] and [[slug|text]] wikilinks from pages that reference this one
    4. Delete the page itself
    """
    # 1+2: Remove all wikilink edges
    await session.execute(
        delete(WikiLink).where(
            (WikiLink.from_slug == slug) | (WikiLink.to_slug == slug)
        )
    )

    # 3: Find pages that reference this slug in their content and clean up
    # Look for [[slug]] or [[slug|display text]] patterns
    referring_pages = (await session.execute(
        select(WikiPage).where(
            WikiPage.content_md.contains(f"[[{slug}]]")
            | WikiPage.content_md.contains(f"[[{slug}|")
        )
    )).scalars().all()

    for ref_page in referring_pages:
        if ref_page.slug == slug:
            continue
        cleaned = ref_page.content_md or ""
        # Replace [[slug|display]] with just display text
        cleaned = re.sub(
            rf"\[\[{re.escape(slug)}\|([^\]]+)]]",
            r"\1",
            cleaned,
        )
        # Replace [[slug]] with slug text
        cleaned = cleaned.replace(f"[[{slug}]]", slug.split("/")[-1])
        ref_page.content_md = cleaned

    # 4: Delete the page
    page = await get_page_by_slug(session, slug)
    if page:
        await session.delete(page)

    await session.flush()
    logger.info(f"delete_page_cascade({slug}): deleted page + cleaned {len(referring_pages)} references")


# ---------------------------------------------------------------------------
# Source removal — for force-recompile
# ---------------------------------------------------------------------------

async def detach_source_from_wiki(
    session: AsyncSession,
    source_id: uuid.UUID,
) -> int:
    """
    Remove `source_id` from every WikiPage.source_ids. Pages whose source_ids
    becomes empty are deleted (orphan pages). Used by force-recompile flow.

    Returns the number of pages deleted.
    """
    stmt = select(WikiPage).where(WikiPage.source_ids.any(source_id))
    pages = list((await session.execute(stmt)).scalars().all())
    deleted = 0
    for page in pages:
        remaining = [sid for sid in (page.source_ids or []) if sid != source_id]
        if not remaining:
            # Orphan: also clear out its outgoing links
            await session.execute(
                delete(WikiLink).where(WikiLink.from_slug == page.slug)
            )
            await session.delete(page)
            deleted += 1
        else:
            page.source_ids = remaining
    await session.flush()
    if deleted:
        logger.info(f"detach_source_from_wiki({source_id}): deleted {deleted} orphan pages")
    return deleted
