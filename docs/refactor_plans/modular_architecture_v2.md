# Design: Arkon V2 Modular Architecture & Selective Branch Merging

This document defines the overall system refactoring phases, modular components, and the new selective merging protocol for Wiki Branching.

---

## 1. Stage-Based Ingestion Pipeline

The ingestion pipeline (currently tightly coupled inside `kb_service.py` and `worker.py`) is refactored into a **Pipes & Filters** pattern:

```
[Upload Request] ──> [Pipeline Orchestrator]
                            │
                            ├──> [1. FileExtractionStage] (via Parsers Registry)
                            ├──> [2. SemanticTriageStage] (AI classification/triage)
                            ├──> [3. Chunker & TOC Stage] (semantic pagination)
                            └──> [4. VectorizationStage] (embeddings generation)
```

### Stage Modularity:
*   **FileExtractionStage:** Employs the `ParserRegistry` to convert binary formats to Markdown.
*   **SemanticTriageStage:** Uses a small SLM/rule-engine to detect if the document is Junk or Transactional. Transactional/Junk files bypass the Wiki pipeline and are routed directly to keyword-only search, preventing Wiki index bloat.
*   **Tuning - Source Category Removal:** The `knowledge_type_id` (Category) is removed from `sources` database table. Only finalized `wiki_pages` are categorized for navigation.

---

## 2. Pluggable Parsers & MCP Plugins

### File Parsers Registry:
All file parsers are separated from the core pipeline logic and registered dynamically:
```python
class BaseParser(ABC):
    @abstractmethod
    def parse(self, file_bytes: bytes) -> str:
        pass

class ParserRegistry:
    _parsers = {}
    
    @classmethod
    def register(cls, mime_type: str, parser: BaseParser):
        cls._parsers[mime_type] = parser
```
Allows adding advanced parsers (e.g., LlamaParse, Azure Layout) simply by dropping a new parser class into the codebase.

### MCP Plugins Auto-Discovery:
Instead of declaring all tools in `app/mcp/tools.py`, a scanner automatically loads custom business tools at startup:
1.  Core Arkon tools reside in `app/mcp/tools.py`.
2.  Enterprise-specific integration tools are dropped into `app/mcp/custom_tools/`.
3.  The FastMCP server boots up, scans the directory, and registers all `@mcp.tool()` decorators dynamically, preventing git merge conflicts during upgrade cycles.

---

## 3. Wiki Branching & Contribution Lifecycle

To allow collaborative authoring while keeping the main Wiki index stable, we implement a Git-like branching strategy but allow **Selective Submissions** and **Selective Merging** (Cherry-pick approvals) at the page/draft level.

### Concept:
*   Instead of treating a branch as a monolithic block, a branch acts as a **Container of Proposed Changes (Drafts)**.
*   Each page modification inside a branch is saved as a `WikiPageDraft` with its own independent lifecycle:
    `status` $\in$ (`in_progress`, `ready_for_review`, `approved`, `rejected`)

### 1. Selective Submit Flow:
A contributor can modify multiple pages (e.g., Pages 1 to 6) in a single branch but only submit a subset (e.g., Pages 1 to 3) for leader review.

*   **Logic:**
    1.  The contributor edits Pages 1-6. All drafts are created with `status = 'in_progress'`.
    2.  The contributor selects Pages 1-3 and clicks **"Submit Selection for Review"**.
    3.  The system updates only Drafts 1-3 to `status = 'ready_for_review'`.
    4.  Drafts 4-6 remain `in_progress`, invisible to the reviewer.

### 2. Selective Merging Flow:

```mermaid
sequenceDiagram
    autonumber
    Contributor ->> Branch: Edit Page A (Draft A: in_progress)
    Contributor ->> Branch: Edit Page B (Draft B: in_progress)
    Contributor ->> Branch: Submit Page A only (Draft A: ready_for_review)
    Lead ->> Branch: Reviews Draft A (Approve)
    Lead ->> Branch: Clicks "Publish Approved Changes"
    Branch ->> Wiki: Merges ONLY Draft A into main wiki_pages
    Branch ->> Branch: Keeps Draft B in "in_progress" status for future edits
```

### Database Implementation of Selective Merge:
When performing the publish/merge action, the `WikiService` performs a selective query:
```python
# app/services/wiki_service.py

async def publish_approved_drafts_from_branch(db: AsyncSession, branch_id: uuid.UUID):
    # 1. Fetch only approved drafts from the branch
    approved_drafts = await db.execute(
        select(WikiPageDraft).where(
            WikiPageDraft.branch_id == branch_id,
            WikiPageDraft.status == "approved"
        )
    )
    
    for draft in approved_drafts.scalars().all():
        # 2. Upsert each approved draft into the live wiki_pages table
        await merge_draft_to_page(db, draft)
        
        # 3. Clean up the merged draft
        await db.delete(draft)
        
    # 4. Commit transaction
    await db.commit()
```

---

## 4. Cross-Space Knowledge Promotion

When a project concludes within a **Group Space** (Cross-functional virtual space), the lessons learned or finalized policies need to be "promoted" to a **Team Space** (Department) or the **Global Space** (Company core).

### Promotion Protocol:
Since spaces have different security boundaries, data cannot simply be copied without audit. We implement **Cross-Space Promotion Requests**:

1.  **Request Initiation:** The Group Owner selects pages in `Group Space A` and selects **"Promote to Space"**, specifying the target space (e.g., `Global Space` or `Team: Tech`).
2.  **Cross-Space Draft Generation:** The system duplicates the selected pages as `WikiPageDraft` records, but pointing to the `target_space_id`.
3.  **Governance Gate:** A notification is dispatched to the Admin/Knowledge Manager of the target space. 
4.  **Publishing:** Once approved, the page is merged into the target space's live index and automatically inherits the target space's ABAC permissions.

