"""
Tool catalog for the wiki mini-agent.

Each tool wraps wiki_service functions. build_tool_handlers() returns a
name→async_callable mapping with all context bound via closure. Errors are
returned as {"error": "..."} so the agent loop can recover without crashing.
"""

import re
import uuid
from typing import Any, Callable, Optional

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Source
from app.services import wiki_service


# ---------------------------------------------------------------------------
# JSON Schema definitions (OpenAI function-calling format)
# ---------------------------------------------------------------------------

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "read_wiki_index",
            "description": (
                "Get a catalog of all existing wiki pages (slug, page_type, summary). "
                "Call this first to understand what already exists before creating or updating pages."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_wiki_page",
            "description": (
                "Read the full markdown content of an existing wiki page. "
                "Always read a page before calling update_page on it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "slug": {
                        "type": "string",
                        "description": "Page slug, e.g. 'concept/fire-safety' or 'entity/acme-corp'",
                    },
                },
                "required": ["slug"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_wiki",
            "description": (
                "Semantic search over existing wiki pages. "
                "Use this to find pages related to a topic in the source document."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural language search query"},
                    "top_k": {
                        "type": "integer",
                        "description": "Number of results to return (1-10, default 5)",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_source_excerpt",
            "description": (
                "Read a portion of the source document by character offset. "
                "The initial message already includes the first ~30k chars. "
                "Use this to read deeper into a long document."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "start_char": {
                        "type": "integer",
                        "description": "Character offset to start from (0-based)",
                    },
                    "length": {
                        "type": "integer",
                        "description": "Characters to read (default 10000, max 15000)",
                    },
                },
                "required": ["start_char"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_page",
            "description": (
                "Create a new wiki page. "
                "Returns an error if the slug already exists — use update_page instead. "
                "Content must be a complete encyclopedic article, not a bullet list."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "slug": {
                        "type": "string",
                        "description": "URL-safe, lowercase, hyphenated, type-prefixed slug. E.g. 'concept/fire-safety'",
                    },
                    "title": {"type": "string", "description": "Human-readable page title"},
                    "page_type": {
                        "type": "string",
                        "enum": ["entity", "concept", "topic", "source"],
                        "description": "entity=named thing, concept=process/rule/methodology, topic=broad subject, source=this document",
                    },
                    "content_md": {
                        "type": "string",
                        "description": "Full markdown content. Must be substantive prose, not a flat bullet list.",
                    },
                    "summary": {
                        "type": "string",
                        "description": "One-sentence summary for the wiki index",
                    },
                },
                "required": ["slug", "title", "page_type", "content_md", "summary"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_page",
            "description": (
                "Update an existing wiki page with complete new content. "
                "Pass the full new content_md (not a diff). "
                "Read the page first to understand existing content."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "slug": {"type": "string", "description": "Existing page slug"},
                    "new_content_md": {
                        "type": "string",
                        "description": "Complete replacement markdown content",
                    },
                    "summary": {
                        "type": "string",
                        "description": "Updated one-sentence summary (optional)",
                    },
                    "title": {"type": "string", "description": "Updated title (optional)"},
                },
                "required": ["slug", "new_content_md"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "append_log",
            "description": "Append a one-line entry to the wiki activity log. Call once at the end.",
            "parameters": {
                "type": "object",
                "properties": {
                    "entry": {
                        "type": "string",
                        "description": "Log entry, e.g. 'ingested PCCC guide: +12 pages, ~3 updated'",
                    },
                },
                "required": ["entry"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "Signal that wiki compilation is complete. Must be the final tool call.",
            "parameters": {
                "type": "object",
                "properties": {
                    "report": {
                        "type": "string",
                        "description": "Brief summary of what was compiled",
                    },
                },
                "required": ["report"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Agent state
# ---------------------------------------------------------------------------

class AgentState:
    """Tracks pages created/updated and signals when the agent is done."""

    def __init__(self, source: Source, full_text: str):
        self.source = source
        self.full_text = full_text
        self.pages_created: list[str] = []
        self.pages_updated: list[str] = []
        self.read_pages: set[str] = set()
        self.tool_call_count: int = 0
        self.finished: bool = False
        self.report: str = ""

    def record(self, call_name: str) -> None:
        self.tool_call_count += 1

    def mark_done(self, report: str) -> None:
        self.finished = True
        self.report = report

    def summary(self) -> dict:
        doc_name = self.source.title or self.source.file_name or str(self.source.id)
        return {
            "pages_created": len(self.pages_created),
            "pages_updated": len(self.pages_updated),
            "log_entry": self.report or (
                f"ingested {doc_name}: +{len(self.pages_created)} created, "
                f"~{len(self.pages_updated)} updated"
            ),
            "tool_calls": self.tool_call_count,
        }


# ---------------------------------------------------------------------------
# Slug validation
# ---------------------------------------------------------------------------

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_/-]*[a-z0-9]$")


def _check_slug(slug: str) -> Optional[str]:
    """Return cleaned slug or None if invalid/reserved."""
    s = slug.strip().lower()
    if not s or s in (wiki_service.INDEX_SLUG, wiki_service.LOG_SLUG):
        return None
    if not _SLUG_RE.match(s):
        return None
    return s


# ---------------------------------------------------------------------------
# Tool handler factory
# ---------------------------------------------------------------------------

def build_tool_handlers(
    session: AsyncSession,
    source: Source,
    kt_slug: Optional[str],
    embedding_provider: Any,
    state: AgentState,
) -> dict[str, Callable]:
    """Return name→async_handler mapping with all context bound via closure."""
    # Scope from source — workspace-scoped sources create scoped wiki pages
    _scope_type = source.scope_type or "global"
    _scope_id = source.scope_id

    async def _embed(text: str) -> Optional[list[float]]:
        if embedding_provider is None:
            return None
        try:
            return await embedding_provider.embed(text[:8000])
        except Exception as e:
            logger.debug(f"WikiAgent: embed failed: {e}")
            return None

    # --- read_wiki_index ---

    async def read_wiki_index() -> dict:
        pages = await wiki_service.list_pages(
            session, limit=300, scope_type=_scope_type, scope_id=_scope_id,
        )
        return {
            "count": len(pages),
            "pages": [
                {"slug": p.slug, "page_type": p.page_type, "title": p.title, "summary": p.summary or ""}
                for p in pages
            ],
        }

    # --- read_wiki_page ---

    async def read_wiki_page(slug: str) -> dict:
        slug = slug.strip().lower()
        page = await wiki_service.get_page_by_slug(
            session, slug, scope_type=_scope_type, scope_id=_scope_id,
        )
        if page is None:
            return {"error": f"Page not found: '{slug}'"}
        state.read_pages.add(slug)
        return {
            "slug": page.slug,
            "title": page.title,
            "page_type": page.page_type,
            "version": page.version,
            "summary": page.summary or "",
            "content_md": page.content_md or "",
        }

    # --- search_wiki ---

    async def search_wiki(query: str, top_k: int = 5) -> dict:
        top_k = max(1, min(int(top_k), 10))
        if embedding_provider is None:
            return {"error": "No embedding provider configured"}
        try:
            query_emb = await embedding_provider.embed(query[:4000])
        except Exception as e:
            return {"error": f"Embedding failed: {e}"}
        hits = await wiki_service.search_pages_semantic(
            session, query_emb, top_k=top_k,
            scope_type=_scope_type, scope_id=_scope_id,
        )
        return {
            "results": [
                {
                    "slug": p.slug,
                    "page_type": p.page_type,
                    "title": p.title,
                    "similarity": round(sim, 3),
                    "summary": p.summary or "",
                }
                for p, sim in hits
            ]
        }

    # --- read_source_excerpt ---

    async def read_source_excerpt(start_char: int, length: int = 10000) -> dict:
        length = max(1, min(int(length), 15000))
        start_char = max(0, int(start_char))
        excerpt = state.full_text[start_char : start_char + length]
        return {
            "start_char": start_char,
            "end_char": start_char + len(excerpt),
            "total_chars": len(state.full_text),
            "excerpt": excerpt,
        }

    # --- create_page ---

    async def create_page(
        slug: str,
        title: str,
        page_type: str,
        content_md: str,
        summary: str,
    ) -> dict:
        clean = _check_slug(slug)
        if not clean:
            return {"error": f"Invalid slug: '{slug}'. Use 'type/kebab-case-name' format."}

        existing = await wiki_service.get_page_by_slug(
            session, clean, scope_type=_scope_type, scope_id=_scope_id,
        )
        if existing is not None:
            return {"error": f"Slug '{clean}' already exists. Use update_page instead."}

        if page_type not in wiki_service.PAGE_TYPES:
            page_type = "concept"

        embedding = await _embed(f"{title}\n\n{summary}\n\n{content_md}")

        await wiki_service.apply_create(
            session,
            slug=clean,
            title=title,
            page_type=page_type,
            content_md=content_md,
            summary=summary,
            knowledge_type_slugs=[kt_slug] if kt_slug else [],
            source_ids=[source.id],
            embedding=embedding,
            scope_type=_scope_type,
            scope_id=_scope_id,
        )
        await session.flush()
        state.pages_created.append(clean)
        logger.debug(f"WikiAgent: created '{clean}' ({page_type})")
        return {"created": clean, "title": title, "page_type": page_type}

    # --- update_page ---

    async def update_page(
        slug: str,
        new_content_md: str,
        summary: Optional[str] = None,
        title: Optional[str] = None,
    ) -> dict:
        clean = slug.strip().lower()
        existing = await wiki_service.get_page_by_slug(
            session, clean, scope_type=_scope_type, scope_id=_scope_id,
        )
        if existing is None:
            return {"error": f"Page '{clean}' not found. Use create_page to create it."}

        embed_title = title or existing.title or ""
        embed_summary = summary or existing.summary or ""
        embedding = await _embed(f"{embed_title}\n\n{embed_summary}\n\n{new_content_md}")

        await wiki_service.apply_update(
            session,
            slug=clean,
            new_content_md=new_content_md,
            summary=summary,
            title=title,
            add_knowledge_type_slug=kt_slug,
            add_source_id=source.id,
            embedding=embedding,
            scope_type=_scope_type,
            scope_id=_scope_id,
        )
        await session.flush()
        state.pages_updated.append(clean)
        logger.debug(f"WikiAgent: updated '{clean}' → v{(existing.version or 1) + 1}")
        return {"updated": clean, "new_version": (existing.version or 1) + 1}

    # --- append_log ---

    async def append_log(entry: str) -> dict:
        await wiki_service.append_log(
            session, entry, scope_type=_scope_type, scope_id=_scope_id,
        )
        return {"logged": entry[:120]}

    # --- finish ---

    async def finish(report: str) -> dict:
        state.mark_done(report)
        return {"done": True, "report": report}

    return {
        "read_wiki_index": read_wiki_index,
        "read_wiki_page": read_wiki_page,
        "search_wiki": search_wiki,
        "read_source_excerpt": read_source_excerpt,
        "create_page": create_page,
        "update_page": update_page,
        "append_log": append_log,
        "finish": finish,
    }
