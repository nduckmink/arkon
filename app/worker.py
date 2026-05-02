"""
arq Worker — async Redis queue for document ingestion.

Start with:
    arq app.worker.WorkerSettings
"""

import uuid
from typing import Callable, Optional

from arq import cron
from arq.connections import RedisSettings
from loguru import logger

from app.config import settings


def _get_redis_settings() -> RedisSettings:
    """Build RedisSettings from config fields."""
    return RedisSettings(
        host=settings.redis_host,
        port=settings.redis_port,
        database=settings.redis_db,
        password=settings.redis_password or None,
    )


# ---------------------------------------------------------------------------
# Progress helper
# ---------------------------------------------------------------------------

class ProgressTracker:
    """Updates source.progress + source.progress_message in DB."""

    def __init__(self, source_id: uuid.UUID):
        self.source_id = source_id

    async def update(self, progress: int, message: str):
        from app.database import async_session_factory
        async with async_session_factory() as session:
            from app.database.models import Source
            source = await session.get(Source, self.source_id)
            if source:
                source.progress = progress
                source.progress_message = message
                await session.commit()
        logger.debug(f"[{self.source_id}] Progress: {progress}% — {message}")


# ---------------------------------------------------------------------------
# Ingestion task
# ---------------------------------------------------------------------------

async def ingest_file_task(ctx: dict, source_id: str, file_data: bytes, file_name: str):
    """
    arq task: full file ingestion with progress tracking.
    Steps: upload → extract → chunk → embed → store → summarize
    """
    from app.database import async_session_factory
    from app.database.models import Source, SourceChunk, SourceInsight, ChunkImage
    from app.ai.registry import ProviderRegistry
    from app.services.storage_service import storage_service
    from app.services.image_service import extract_images
    from app.services.kb_service import chunk_text_with_pages, map_images_to_chunks, _guess_content_type, _extract_text_from_file

    sid = uuid.UUID(source_id)
    tracker = ProgressTracker(sid)

    async with async_session_factory() as session:
        source = await session.get(Source, sid)
        if not source:
            raise ValueError(f"Source {source_id} not found")

        try:
            source.status = "processing"
            source.progress = 0
            source.progress_message = "Bắt đầu xử lý..."
            await session.commit()

            # --- Step 1: Upload to MinIO (10%) ---
            await tracker.update(5, "Đang tải lên...")
            minio_key = f"sources/{source_id}/original/{file_name}"
            storage_service.upload_file(
                object_name=minio_key,
                data=file_data,
                content_type=_guess_content_type(file_name),
            )
            source.minio_key = minio_key
            source.file_name = file_name
            source.file_size = len(file_data)
            await session.commit()
            await tracker.update(10, "Tải lên hoàn tất")

            # --- Step 2: Extract text (30%) ---
            await tracker.update(15, "Đang trích xuất văn bản (theo trang)...")
            pages_data = await _extract_text_from_file(file_data, file_name)

            full_text_content = "\n\n".join([p["content"] for p in pages_data])
            if not full_text_content or not full_text_content.strip():
                source.status = "error"
                source.error_message = "Không thể trích xuất nội dung văn bản"
                source.progress = 0
                await session.commit()
                return {"status": "error", "message": "No text content"}

            source.full_text = full_text_content
            await session.commit()
            await tracker.update(30, "Trích xuất văn bản hoàn tất")

            # --- Step 3: Extract images & Vision analysis (40%) ---
            await tracker.update(35, "Extracting and analyzing images...")
            images = extract_images(file_data, file_name, source_id)

            # Analyze each image with configured vision provider
            registry = ProviderRegistry(session)
            vision_provider = await registry.get_vision()
            if vision_provider:
                for idx, img in enumerate(images, 1):
                    try:
                        if idx % 5 == 0 or idx == 1 or idx == len(images):
                            logger.info(f"Vision AI analyzing image {idx}/{len(images)}...")

                        img_bytes = storage_service.download_file(img.minio_key)
                        mime_type = "image/jpeg"
                        if img.minio_key.lower().endswith(".png"):
                            mime_type = "image/png"

                        caption = await vision_provider.analyze_image(img_bytes, mime_type)
                        img.caption = caption
                    except Exception as e:
                        logger.warning(f"Failed to analyze image {img.minio_key}: {e}")
            else:
                logger.info("No vision provider configured, skipping image analysis")

            await tracker.update(40, f"Analyzed {len(images)} images")

            # --- Step 4: Chunk text (50%) ---
            await tracker.update(45, "Đang chia nhỏ văn bản...")
            chunks_data = chunk_text_with_pages(
                pages_data,
                chunk_size=settings.chunk_size,
                chunk_overlap=settings.chunk_overlap,
            )
            await tracker.update(50, f"Chia thành {len(chunks_data)} đoạn")

            # --- Step 4.5: Inject Image Captions into Text Chunks ---
            image_mapping = map_images_to_chunks(images, chunks_data)

            chunk_texts_to_embed = []
            for i, chunk in enumerate(chunks_data):
                chunk_text = chunk["content"]
                chunk_images = image_mapping.get(i, [])
                captions = [img.caption for img in chunk_images if img.caption]
                if captions:
                    chunk_text += "\n\n[IMAGE DESCRIPTIONS ON THIS PAGE:]\n" + "\n".join(captions)
                chunk_texts_to_embed.append(chunk_text)

            # --- Step 5: Embed chunks (70%) ---
            await tracker.update(55, f"Embedding {len(chunks_data)} chunks...")
            embedding_provider = await registry.get_embedding(task="document")
            embeddings = await embedding_provider.embed_batch(chunk_texts_to_embed)
            await tracker.update(70, "Embedding complete")

            # --- Step 6: Store chunks + images (85%) ---
            await tracker.update(75, "Đang lưu vào cơ sở dữ liệu...")

            for i, (chunk_dict, embedding, full_chunk_text) in enumerate(zip(chunks_data, embeddings, chunk_texts_to_embed)):
                chunk_obj = SourceChunk(
                    source_id=sid,
                    content=full_chunk_text,  # Store the injected text so it's readable
                    embedding=embedding,
                    chunk_index=i,
                    page_number=chunk_dict.get("page_number")
                )
                session.add(chunk_obj)
                await session.flush()

                # Store associated images
                chunk_images = image_mapping.get(i, [])
                for img in chunk_images:
                    session.add(ChunkImage(
                        chunk_id=chunk_obj.id,
                        source_id=sid,
                        minio_key=img.minio_key,
                        page_number=img.page_number,
                        image_index=img.image_index,
                        caption=img.caption,
                    ))

                # Update progress proportionally
                if len(chunks_data) > 1 and i % max(1, len(chunks_data) // 5) == 0:
                    pct = 75 + int(10 * (i + 1) / len(chunks_data))
                    await tracker.update(pct, f"Lưu đoạn {i + 1}/{len(chunks_data)}")

            # Unmapped images
            mapped_keys = {img.minio_key for imgs in image_mapping.values() for img in imgs}
            for img in images:
                if img.minio_key not in mapped_keys:
                    session.add(ChunkImage(
                        chunk_id=None, source_id=sid, minio_key=img.minio_key,
                        page_number=img.page_number, image_index=img.image_index,
                    ))

            await session.commit()
            await tracker.update(85, "Lưu dữ liệu hoàn tất")

            # --- Step 7: Generate summary (95%) ---
            await tracker.update(90, "Generating summary...")
            try:
                llm = await registry.get_llm()
                summary = await llm.generate(
                    f"Summarize this document concisely (max 500 words):\n\n{full_text_content[:10000]}",
                    temperature=0.3,
                    max_tokens=1024,
                )
                session.add(SourceInsight(
                    source_id=sid, insight_type="summary", content=summary,
                ))
            except Exception as e:
                logger.warning(f"Summary failed: {e}")

            # --- Step 8: Entity extraction (optional, 98%) ---
            if settings.enable_entity_extraction:
                await tracker.update(92, "Đang trích xuất thực thể...")
                try:
                    from app.ai.entity_extractor import extract_entities_from_chunks
                    from sqlalchemy import select
                    # Get stored chunks with IDs
                    chunk_rows = await session.execute(
                        select(SourceChunk.id, SourceChunk.content, SourceChunk.page_number)
                        .where(SourceChunk.source_id == sid)
                        .order_by(SourceChunk.chunk_index)
                    )
                    chunk_data = [
                        {"chunk_id": str(row.id), "content": row.content, "page_number": row.page_number}
                        for row in chunk_rows.all()
                    ]
                    entity_result = await extract_entities_from_chunks(
                        chunk_data, source_id, source.title or file_name,
                    )
                    await tracker.update(98, f"Trích xuất {entity_result['entities_count']} thực thể")
                except Exception as e:
                    logger.warning(f"Entity extraction failed: {e}")

            # --- Done (100%) ---
            source.status = "ready"
            source.progress = 100
            source.progress_message = "Hoàn tất"
            source.error_message = None
            await session.commit()

            logger.success(f"Source {source_id} ingested: {len(chunks_data)} chunks, {len(images)} images")
            return {"status": "ready", "chunks": len(chunks_data), "images": len(images)}

        except Exception as e:
            logger.error(f"Ingestion failed for {source_id}: {e}")
            source.status = "error"
            source.error_message = str(e)[:500]
            source.progress = 0
            source.progress_message = f"Lỗi: {str(e)[:200]}"
            await session.commit()
            raise


async def ingest_url_task(ctx: dict, source_id: str):
    """arq task: URL ingestion with progress tracking."""
    from app.database import async_session_factory
    from app.database.models import Source
    from app.services.kb_service import _extract_text_from_url

    sid = uuid.UUID(source_id)
    tracker = ProgressTracker(sid)

    async with async_session_factory() as session:
        source = await session.get(Source, sid)
        if not source:
            raise ValueError(f"Source {source_id} not found")

        try:
            source.status = "processing"
            source.progress = 0
            await session.commit()

            await tracker.update(10, "Đang tải nội dung từ URL...")
            pages_data = await _extract_text_from_url(source.url)
            
            full_text_content = "\n\n".join([p["content"] for p in pages_data])

            if not full_text_content or not full_text_content.strip():
                source.status = "error"
                source.error_message = "Không thể tải nội dung từ URL"
                await session.commit()
                return {"status": "error"}

            source.full_text = full_text_content
            await session.commit()

            # Re-use file ingestion logic for chunking + embedding
            from app.services.kb_service import chunk_text_with_pages
            from app.ai.registry import ProviderRegistry
            from app.database.models import SourceChunk, SourceInsight

            await tracker.update(30, "Chunking text...")
            chunks_data = chunk_text_with_pages(pages_data, chunk_size=settings.chunk_size, chunk_overlap=settings.chunk_overlap)

            chunk_texts_to_embed = [c["content"] for c in chunks_data]

            await tracker.update(50, f"Embedding {len(chunks_data)} chunks...")
            registry = ProviderRegistry(session)
            embedding_provider = await registry.get_embedding(task="document")
            embeddings = await embedding_provider.embed_batch(chunk_texts_to_embed)

            await tracker.update(70, "Saving to database...")
            for i, (chunk_dict, emb) in enumerate(zip(chunks_data, embeddings)):
                session.add(SourceChunk(
                    source_id=sid,
                    content=chunk_dict["content"],
                    embedding=emb,
                    chunk_index=i,
                    page_number=chunk_dict.get("page_number")
                ))

            await session.commit()
            await tracker.update(85, "Data saved")

            # Summary
            await tracker.update(90, "Generating summary...")
            try:
                llm = await registry.get_llm()
                summary = await llm.generate(
                    f"Summarize this document concisely (max 500 words):\n\n{full_text_content[:10000]}",
                    temperature=0.3,
                    max_tokens=1024,
                )
                session.add(SourceInsight(source_id=sid, insight_type="summary", content=summary))
            except Exception:
                pass

            # Entity extraction (optional)
            if settings.enable_entity_extraction:
                await tracker.update(92, "Đang trích xuất thực thể...")
                try:
                    from app.ai.entity_extractor import extract_entities_from_chunks
                    from sqlalchemy import select
                    chunk_rows = await session.execute(
                        select(SourceChunk.id, SourceChunk.content, SourceChunk.page_number)
                        .where(SourceChunk.source_id == sid)
                        .order_by(SourceChunk.chunk_index)
                    )
                    chunk_data = [
                        {"chunk_id": str(row.id), "content": row.content, "page_number": row.page_number}
                        for row in chunk_rows.all()
                    ]
                    await extract_entities_from_chunks(
                        chunk_data, source_id, source.title or source.url or "",
                    )
                except Exception as e:
                    logger.warning(f"Entity extraction failed: {e}")

            source.status = "ready"
            source.progress = 100
            source.progress_message = "Hoàn tất"
            source.error_message = None
            await session.commit()

            logger.success(f"URL source {source_id} ingested: {len(chunks_data)} chunks")
            return {"status": "ready", "chunks": len(chunks_data)}

        except Exception as e:
            logger.error(f"URL ingestion failed for {source_id}: {e}")
            source.status = "error"
            source.error_message = str(e)[:500]
            source.progress = 0
            await session.commit()
            raise


async def reingest_file_task(ctx: dict, source_id: str):
    """arq task: re-ingest a file already stored in MinIO."""
    from app.database import async_session_factory
    from app.database.models import Source
    from app.services.storage_service import storage_service

    sid = uuid.UUID(source_id)

    async with async_session_factory() as session:
        source = await session.get(Source, sid)
        if not source or not source.minio_key:
            raise ValueError(f"Source {source_id} not found or has no file")

        file_data = storage_service.download_file(source.minio_key)
        file_name = source.file_name or source.minio_key.split("/")[-1]

    await ingest_file_task(ctx, source_id, file_data, file_name)


# ---------------------------------------------------------------------------
# Worker configuration
# ---------------------------------------------------------------------------

class WorkerSettings:
    """arq worker configuration."""

    functions = [ingest_file_task, ingest_url_task, reingest_file_task]
    redis_settings = _get_redis_settings()
    max_jobs = settings.worker_max_jobs
    job_timeout = settings.worker_job_timeout
    max_tries = 3
    retry_delay = 10  # seconds
    health_check_interval = 30

    @staticmethod
    async def on_startup(ctx: dict):
        logger.info("arq worker started — listening for ingestion jobs...")
        # Initialize Neo4j for entity extraction
        try:
            from app.services.neo4j_service import neo4j_service
            await neo4j_service.connect()
        except Exception as e:
            logger.warning(f"Worker: Neo4j init skipped: {e}")

    @staticmethod
    async def on_shutdown(ctx: dict):
        logger.info("arq worker shutting down...")
        try:
            from app.services.neo4j_service import neo4j_service
            await neo4j_service.close()
        except Exception:
            pass
