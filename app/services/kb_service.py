"""
Knowledge Base service — document ingestion via the LLM Wiki pipeline.

Pipeline: Upload → Extract text → Extract & caption images → Build outline →
Compile into wiki (LLM). No chunking, no per-chunk embeddings — embeddings now
live on WikiPage rows. Search is handled by app/services/wiki_service.py.

Provider-agnostic: uses ProviderRegistry to resolve embedding/LLM/vision
providers from app_config at runtime.
"""

import uuid
from typing import Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.registry import ProviderRegistry
from app.ai.wiki_compiler import compile_source_into_wiki
from app.database.models import Contact, KnowledgeType, Source
from app.services.image_service import ImageInfo, extract_images
from app.services.source_outline import assemble_full_text, build_outline
from app.services.storage_service import storage_service


# ---------------------------------------------------------------------------
# Ingestion pipeline
# ---------------------------------------------------------------------------

async def ingest_source(
    session: AsyncSession,
    source_id: uuid.UUID,
    file_data: Optional[bytes] = None,
    file_name: Optional[str] = None,
) -> Source:
    """
    Ingest a Source into the wiki:
      1. Upload original file to MinIO (if file)
      2. Extract text per page
      3. Extract images, caption with vision provider, inline captions
      4. Build heading-based outline → Source.outline_json
      5. Compile into wiki via LLM (creates/updates WikiPage rows)
    """
    source = await session.get(Source, source_id)
    if not source:
        raise ValueError(f"Source {source_id} not found")

    try:
        registry = ProviderRegistry(session)
        vision_provider = await registry.get_vision()

        source.status = "processing"
        await session.flush()

        # --- Step 1: Upload original file ---
        if file_data and file_name:
            minio_key = f"sources/{source_id}/original/{file_name}"
            storage_service.upload_file(
                object_name=minio_key,
                data=file_data,
                content_type=_guess_content_type(file_name),
            )
            source.minio_key = minio_key
            source.file_name = file_name
            source.file_size = len(file_data)

        # --- Step 2: Extract text per page ---
        if file_data and file_name:
            pages_data = await _extract_text_from_file(file_data, file_name)
        elif source.url:
            pages_data = await _extract_text_from_url(source.url)
        else:
            pages_data = []

        if not pages_data or not any((p.get("content") or "").strip() for p in pages_data):
            source.status = "error"
            source.error_message = "Could not extract text content from source"
            await session.flush()
            return source

        # --- Step 3: Extract & caption images, inline captions into pages ---
        images: list[ImageInfo] = []
        if file_data and file_name:
            images = extract_images(file_data, file_name, str(source_id))
            if vision_provider and images:
                for idx, img in enumerate(images, 1):
                    try:
                        if idx % 5 == 0 or idx == 1 or idx == len(images):
                            logger.info(f"Vision AI analyzing image {idx}/{len(images)}...")
                        img_bytes = storage_service.download_file(img.minio_key)
                        mime_type = "image/png" if img.minio_key.lower().endswith(".png") else "image/jpeg"
                        img.caption = await vision_provider.analyze_image(img_bytes, mime_type)
                    except Exception as e:
                        logger.warning(f"Failed to analyze image {img.minio_key}: {e}")

        _inline_image_captions(pages_data, images)

        # --- Step 4: Build outline + assemble full_text ---
        source.outline_json = build_outline(pages_data)
        full_text, page_offsets = assemble_full_text(pages_data)
        source.full_text = full_text
        source.page_offsets = page_offsets

        # --- Step 5: Resolve KnowledgeType context ---
        kt_slug = kt_name = kt_desc = None
        if source.knowledge_type_id:
            kt = await session.get(KnowledgeType, source.knowledge_type_id)
            if kt:
                kt_slug = kt.slug
                kt_name = kt.name
                kt_desc = kt.description

        # --- Step 6: Compile into wiki ---
        result = await compile_source_into_wiki(
            session=session,
            source=source,
            full_text=full_text,
            knowledge_type_slug=kt_slug,
            knowledge_type_name=kt_name,
            knowledge_type_description=kt_desc,
        )

        source.status = "ready"
        source.error_message = None
        await session.flush()
        logger.success(
            f"Source {source_id} ingested into wiki: "
            f"+{result['pages_created']} pages, ~{result['pages_updated']} updated"
        )
        return source

    except Exception as e:
        logger.error(f"Ingestion failed for source {source_id}: {e}")
        source.status = "error"
        source.error_message = str(e)[:500]
        await session.flush()
        raise


# ---------------------------------------------------------------------------
# Contact suggestion (used by chat flows)
# ---------------------------------------------------------------------------

async def suggest_contacts(
    session: AsyncSession,
    query: str,
    limit: int = 3,
) -> list[dict]:
    """Find relevant contacts whose topics overlap the query keywords."""
    stmt = select(Contact).where(Contact.topics.isnot(None))
    result = await session.execute(stmt)
    contacts = result.scalars().all()

    query_lower = query.lower()
    scored = []
    for c in contacts:
        if not c.topics:
            continue
        score = sum(1 for t in c.topics if t.lower() in query_lower or query_lower in t.lower())
        if score > 0:
            scored.append((score, c))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [
        {"name": c.name, "role": c.role, "phone": c.phone, "email": c.email}
        for _, c in scored[:limit]
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _guess_content_type(file_name: str) -> str:
    ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    return {
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "doc": "application/msword",
        "txt": "text/plain",
        "md": "text/markdown",
        "csv": "text/csv",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }.get(ext, "application/octet-stream")


def _inline_image_captions(pages_data: list[dict], images: list[ImageInfo]) -> None:
    """
    Append captioned image descriptions to the page text where they originated.
    Mutates pages_data in place. Format keeps the captions visible to the LLM
    compiler so it can incorporate them into the wiki without separate image
    handling downstream.
    """
    if not images:
        return
    by_page: dict[int, list[str]] = {}
    for img in images:
        if not img.caption:
            continue
        page_num = img.page_number or 1
        by_page.setdefault(page_num, []).append(img.caption.strip())

    if not by_page:
        return

    for page in pages_data:
        pnum = page.get("page_number") or 1
        captions = by_page.get(pnum)
        if not captions:
            continue
        joined = "\n".join(f"- {c}" for c in captions)
        page["content"] = (page.get("content") or "") + f"\n\n[Image descriptions on page {pnum}]\n{joined}\n"


async def _extract_text_from_file(file_data: bytes, file_name: str) -> list[dict]:
    """Extract text from a binary file, returning per-page records."""
    ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    pages_data: list[dict] = []

    if ext == "pdf":
        import fitz
        doc = fitz.open(stream=file_data, filetype="pdf")
        for i, page in enumerate(doc):
            pages_data.append({"content": (page.get_text() or "").strip(), "page_number": i + 1})
        doc.close()
        return pages_data

    if ext == "docx":
        import io
        from docx import Document
        doc = Document(io.BytesIO(file_data))
        text = "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())
        return [{"content": text, "page_number": 1}]

    if ext in ("txt", "md", "csv"):
        return [{"content": file_data.decode("utf-8", errors="ignore"), "page_number": 1}]

    # Other formats: fall back to content-core (markdown output preserves headings)
    try:
        from content_core import extract_content
        result = await extract_content({
            "file_path": None,
            "content": file_data,
            "output_format": "markdown",
        })
        return [{"content": result.content or "", "page_number": 1}]
    except Exception as e:
        logger.warning(f"content-core extraction failed: {e}")
        return [{"content": file_data.decode("utf-8", errors="ignore"), "page_number": 1}]


async def _extract_text_from_url(url: str) -> list[dict]:
    """Extract text from a URL — markdown output preferred."""
    try:
        from content_core import extract_content
        result = await extract_content({"url": url, "output_format": "markdown"})
        return [{"content": result.content or "", "page_number": 1}]
    except Exception as e:
        logger.warning(f"URL extraction failed for {url}: {e}")
        import httpx
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, follow_redirects=True, timeout=30)
            return [{"content": resp.text, "page_number": 1}]
