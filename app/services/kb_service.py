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
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.registry import ProviderRegistry
from app.ai.wiki_compiler import compile_source_into_wiki
from app.database.models import KnowledgeType, Source, SourceImage
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

        # --- Step 3: Extract & caption images, persist, inline markers ---
        images: list[ImageInfo] = []
        if file_data and file_name:
            images = extract_images(file_data, file_name, str(source_id))
            if vision_provider and images:
                for idx, img in enumerate(images, 1):
                    try:
                        if idx % 5 == 0 or idx == 1 or idx == len(images):
                            logger.info(f"Vision AI analyzing image {idx}/{len(images)}...")
                        img_bytes = storage_service.download_file(img.minio_key)
                        img.caption = await vision_provider.analyze_image(
                            img_bytes, img.content_type
                        )
                    except Exception as e:
                        logger.warning(f"Failed to analyze image {img.minio_key}: {e}")

            # Persist source_images rows so wiki content_md can reference by uuid.
            for img in images:
                row = SourceImage(
                    source_id=source_id,
                    minio_key=img.minio_key,
                    page_number=img.page_number,
                    image_index=img.image_index,
                    caption=img.caption,
                    content_type=img.content_type,
                    size_bytes=img.size_bytes,
                )
                session.add(row)
                await session.flush()  # populate row.id
                img.image_id = str(row.id)

        _inline_image_markers(pages_data, images)

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
        source.auto_recover_count = 0
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


def _sanitize_caption_for_alt(caption: str) -> str:
    """Make a caption safe to use inside markdown image alt text."""
    # Strip newlines + characters that would break `![alt](url)` parsing.
    cleaned = caption.replace("\n", " ").replace("\r", " ")
    cleaned = cleaned.replace("[", "(").replace("]", ")")
    return cleaned.strip()


def _inline_image_markers(pages_data: list[dict], images: list[ImageInfo]) -> None:
    """Inject markdown image markers into per-page text.

    Each image becomes `![caption](image://<uuid>)` appended at the end of the
    page it came from. The wiki compiler is instructed to preserve these
    markers in the most contextually-relevant wiki page, drop irrelevant ones,
    and never invent UUIDs. Mutates pages_data in place.
    """
    if not images:
        return

    by_page: dict[int, list[str]] = {}
    for img in images:
        if not img.image_id:
            continue
        alt = _sanitize_caption_for_alt(img.caption or "")
        marker = f"![{alt}](image://{img.image_id})"
        page_num = img.page_number or 1
        by_page.setdefault(page_num, []).append(marker)

    if not by_page:
        return

    for page in pages_data:
        pnum = page.get("page_number") or 1
        markers = by_page.get(pnum)
        if not markers:
            continue
        joined = "\n\n".join(markers)
        page["content"] = (page.get("content") or "") + f"\n\n{joined}\n"


async def _extract_text_from_file(
    file_data: bytes,
    file_name: str,
    vision_provider=None,
) -> list[dict]:
    """Extract text from a binary file, returning per-page records.

    When ``vision_provider`` is supplied (a VisionProvider instance) and a PDF
    page yields no text via PyMuPDF's native extraction, the page is rendered
    to an image and sent to the vision model for OCR.  This handles scanned /
    image-only PDFs that previously produced empty text → MAP phase failure.
    """
    ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    pages_data: list[dict] = []

    if ext == "pdf":
        import fitz
        doc = fitz.open(stream=file_data, filetype="pdf")
        empty_pages: list[tuple[int, int]] = []  # (index, page_number)

        for i, page in enumerate(doc):  # type: ignore[arg-type]
            text = (page.get_text() or "").strip()
            pages_data.append({"content": text, "page_number": i + 1})
            if not text:
                empty_pages.append((i, i + 1))

        # --- Gemini Vision OCR fallback for empty pages ---
        if empty_pages and vision_provider:
            logger.info(
                f"OCR fallback: {len(empty_pages)}/{len(pages_data)} empty pages "
                f"in '{file_name}', using vision provider"
            )
            ocr_prompt = (
                "Extract ALL text from this document page exactly as written. "
                "Preserve the original layout, headings, tables, and formatting "
                "as closely as possible using markdown. If the page contains a "
                "table, reproduce it as a markdown table. If there is no text "
                "at all, respond with an empty string."
            )
            for idx, page_num in empty_pages:
                try:
                    page = doc[idx]
                    # Render at 2x for better OCR quality
                    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                    img_bytes = pix.tobytes("png")
                    ocr_text = await vision_provider.analyze_image(
                        img_bytes, mime_type="image/png", prompt=ocr_prompt,
                    )
                    if ocr_text and ocr_text.strip():
                        pages_data[idx]["content"] = ocr_text.strip()
                        logger.debug(f"OCR page {page_num}: {len(ocr_text)} chars")
                except Exception as e:
                    logger.warning(f"OCR failed for page {page_num} of '{file_name}': {e}")

        doc.close()
        return pages_data

    # --- Excel / Spreadsheet extraction ---
    if ext in ("xlsx", "xls", "csv"):
        try:
            import io
            import pandas as pd

            pages_data = []
            if ext == "csv":
                df = pd.read_csv(io.BytesIO(file_data))
                md = df.to_markdown(index=False)
                pages_data.append({"content": md or "", "page_number": 1})
            else:
                # Read all sheets
                xls = pd.ExcelFile(io.BytesIO(file_data))
                for sheet_idx, sheet_name in enumerate(xls.sheet_names):
                    try:
                        df = pd.read_excel(xls, sheet_name=sheet_name)
                        if df.empty:
                            continue
                        header = f"## Sheet: {sheet_name}\n\n"
                        md = df.to_markdown(index=False)
                        pages_data.append({
                            "content": header + (md or ""),
                            "page_number": sheet_idx + 1,
                        })
                    except Exception as e:
                        logger.warning(f"Failed to read sheet '{sheet_name}': {e}")
            if pages_data:
                return pages_data
            # Fall through if all sheets empty
        except Exception as e:
            logger.warning(f"Spreadsheet extraction failed for '{file_name}': {e}")
            # Fall through to content_core

    if ext == "docx":
        import io

        import mammoth
        try:
            result = mammoth.extract_raw_text(io.BytesIO(file_data))
            return [{"content": result.value or "", "page_number": 1}]
        except Exception:
            pass  # fall through to content_core

    if ext in ("txt", "md"):
        return [{"content": file_data.decode("utf-8", errors="ignore"), "page_number": 1}]

    # Other formats (doc, xlsx, pptx, ...): write to a temp file and let
    # content-core extract via file path. Passing raw bytes as "content"
    # doesn't work for binary formats — content-core expects a string there.
    import os
    import tempfile

    try:
        from content_core.content.extraction import extract_content
        suffix = f".{ext}" if ext else ""
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(file_data)
            tmp_path = tmp.name
        try:
            result = await extract_content({
                "file_path": tmp_path,
                "output_format": "markdown",
            })
            return [{"content": result.content or "", "page_number": 1}]
        finally:
            os.unlink(tmp_path)
    except Exception as e:
        logger.warning(f"content-core extraction failed for .{ext}: {e}")
        # Binary formats must not be decoded as UTF-8 — that produces garbage
        # with null bytes that PostgreSQL rejects. Return empty so the caller
        # can surface a clear "no text content" error instead of crashing.
        return [{"content": "", "page_number": 1}]


async def auto_categorize_source(
    title: str,
    text_preview: str,
    llm_provider,
) -> str:
    """Classify a source into a knowledge type using LLM analysis.

    Returns one of: customer, product, sop, project, general
    """
    prompt = f"""Classify this document into exactly ONE category.

Categories:
- customer: Purchase orders, contracts, invoices, COAs, customer correspondence, pricing agreements
- product: Bag specifications, drawings, labels, fabric tests, model definitions, production specs
- sop: Procedures, work instructions, quality manuals, ISO documents, certifications, audits
- project: Order deployments (VIN-XXX), project plans, timelines, deployment summaries
- general: Company info, catalogs, website content, org charts, everything else

Document title: {title}
First 1500 chars:
{text_preview[:1500]}

Reply with ONLY the category name (one word, lowercase). Nothing else."""

    try:
        result = await llm_provider.generate(
            prompt=prompt,
            system="You are a document classifier. Reply with exactly one word.",
            max_tokens=10,
            temperature=0,
        )
        category = result.strip().lower().split()[0] if result else "general"
        valid = {"customer", "product", "sop", "project", "general"}
        return category if category in valid else "general"
    except Exception as e:
        logger.warning(f"Auto-categorize failed for '{title}': {e}")
        return "general"


async def enrich_with_translation(
    pages_data: list[dict],
    llm_provider,
) -> list[dict]:
    """Detect Vietnamese-only pages and append English translations.

    Modifies pages_data in place.  Only translates pages where >60% of
    characters are Vietnamese (non-ASCII Latin + Vietnamese diacritics) and
    the page has no significant English content.
    """
    import re

    # Quick heuristic: Vietnamese diacritics regex
    _VIET_RE = re.compile(r"[àáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵđ]", re.IGNORECASE)

    for page in pages_data:
        text = page.get("content", "").strip()
        if not text or len(text) < 50:
            continue

        # Count Vietnamese diacritical chars vs total alpha
        viet_chars = len(_VIET_RE.findall(text))
        alpha_chars = sum(1 for c in text if c.isalpha())
        if alpha_chars == 0:
            continue

        ratio = viet_chars / alpha_chars
        if ratio < 0.05:
            # Mostly English, skip
            continue

        # Translate via LLM
        try:
            translation = await llm_provider.generate(
                prompt=f"Translate the following Vietnamese text to English. "
                       f"Preserve all formatting, headings, tables, and structure. "
                       f"Output ONLY the translation, no commentary.\n\n{text[:8000]}",
                system="You are a professional Vietnamese-English translator.",
                max_tokens=4000,
                temperature=0.1,
            )
            if translation and translation.strip():
                page["content"] = (
                    text + "\n\n---\n\n**English Translation:**\n\n" + translation.strip()
                )
                logger.debug(
                    f"Translated page {page.get('page_number', '?')}: "
                    f"{len(text)}→{len(page['content'])} chars"
                )
        except Exception as e:
            logger.warning(f"Translation failed for page {page.get('page_number')}: {e}")

    return pages_data


async def _extract_text_from_url(url: str) -> list[dict]:
    """Extract text from a URL — markdown output preferred."""
    try:
        from content_core.content.extraction import extract_content
        result = await extract_content({"url": url, "output_format": "markdown"})
        return [{"content": result.content or "", "page_number": 1}]
    except Exception as e:
        logger.warning(f"URL extraction failed for {url}: {e}")
        import httpx
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, follow_redirects=True, timeout=30)
            return [{"content": resp.text, "page_number": 1}]
