"""
Arkon MCP Resources — static/semi-static data exposed to Claude.

Resources provide context Claude can read at session start without calling a tool.
"""

from fastmcp import FastMCP
from loguru import logger


def register_resources(mcp: FastMCP):
    """Register MCP resources on the server."""

    @mcp.resource("arkon://about")
    async def about_arkon() -> str:
        """About this Arkon instance — capabilities and instructions."""
        return (
            "# Arkon Knowledge Base\n\n"
            "You are connected to an Arkon enterprise LLM Wiki. Knowledge is organized "
            "as interlinked markdown wiki pages, compiled from source documents by an "
            "LLM and kept up to date over time. Wiki pages contain the synthesis; raw "
            "sources are available for precise citations.\n\n"
            "## Wiki tools (use first)\n\n"
            "- **search_wiki**: Semantic search over wiki pages\n"
            "- **read_wiki_index**: Read the catalog of all pages\n"
            "- **read_wiki_page**: Read one wiki page by slug + its backlinks\n"
            "- **list_wiki_pages**: Browse pages with filters\n\n"
            "## Raw source drill-down (PageIndex-style fallback)\n\n"
            "- **get_source**: Source metadata (title, type, page count, contributor)\n"
            "- **get_source_outline**: Heading-based table of contents\n"
            "- **get_source_pages**: Raw text of specific page ranges\n\n"
            "## Browsing & directory\n\n"
            "- **list_sources**: Browse all available source documents\n"
            "- **list_knowledge_types**: See classification scheme\n"
            "- **get_knowledge_type_docs**: Browse documents by knowledge type\n\n"
            "## Guidelines\n\n"
            "1. Always search the wiki before saying you don't know\n"
            "2. Follow `[[wikilinks]]` between pages to discover context\n"
            "3. Cite slugs for wiki facts, source IDs (and page numbers) for raw quotes\n"
        )

    @mcp.resource("arkon://wiki-index")
    async def wiki_index_resource() -> str:
        """Current wiki catalog — same content as the `read_wiki_index` tool."""
        from app.database import async_session_factory
        from app.services import wiki_service

        try:
            async with async_session_factory() as session:
                page = await wiki_service.get_page_by_slug(session, wiki_service.INDEX_SLUG)
            return page.content_md if page else "_(wiki index not initialized yet)_"
        except Exception as e:
            logger.warning(f"Failed to load wiki index resource: {e}")
            return "Wiki index: failed to load."
