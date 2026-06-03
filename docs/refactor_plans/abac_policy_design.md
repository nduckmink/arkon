# Design: ABAC & The 4 Spaces in Arkon V2

This document details the dynamic Attribute-Based Access Control (ABAC) and the unified Namespace system for the next generation of Arkon.

---

## 1. The 4 Namespaces (Spaces)

Rather than maintaining disjointed department tables, we unify all access boundaries into a single `namespaces` table.

```sql
CREATE TYPE namespace_type AS ENUM ('global', 'team', 'group', 'personal');

CREATE TABLE namespaces (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(200) NOT NULL,
    type namespace_type NOT NULL,
    parent_id UUID REFERENCES namespaces(id) ON DELETE SET NULL,
    allow_inheritance BOOLEAN DEFAULT TRUE NOT NULL
);
```

### Logical Rules of the Spaces:
1.  **Global Space (`global`):**
    *   *Purpose:* Organization-wide guidelines, static standards, core policies.
    *   *Permission:* Default `read` to everyone. Only system `admin` or `knowledge_manager` can modify.
2.  **Team Space (`team`):**
    *   *Purpose:* Departmental spaces (Tech, HR, Sales).
    *   *Structure:* Supports hierarchal trees via `parent_id`.
    *   *Permission:* Auto-inherits read permissions down the tree (unless `allow_inheritance = False` is set on sensitive nodes like recruitment or compensation).
3.  **Group Space (`group`):**
    *   *Purpose:* Cross-functional projects, temporary teams.
    *   *Permission:* Static membership only. No parent-child relationships, no inheritance.
4.  **Personal Space (`personal`):**
    *   *Purpose:* Individual scratchpads and draft areas.
    *   *Permission:* Exclusively accessible by the owner.

---

## 2. Minimalist Action Permissions

We define exactly 5 standardized actions to control the entire KMS:
*   `read`: View a wiki page or source document.
*   `edit`: Propose modifications, draft content, or edit existing drafts.
*   `review`: Evaluate other users' drafts.
*   `merge`: Approve drafts, publish changes to the main pages, or alter directory schemas (slugs/hierarchy).
*   `delete`: Remove resources permanently.

---

## 3. Dynamic ABAC Policy Rules Matrix

The `PolicyEngine` executes rules dynamically by verifying attributes on both the `Subject` (User) and the `Resource` (Wiki/Source):

| Action | Global Space | Team Space | Group Space | Personal Space |
| :--- | :--- | :--- | :--- | :--- |
| **`read`** | Everyone | Team Members + Parent Teams (if inheritable) | Group Members | Owner Only |
| **`edit`** | Admin / KM | Team Members | Group Members | Owner Only |
| **`review`** | Admin / KM | Team Lead / Admin | Group Lead / Admin | Owner Only |
| **`merge`** | Admin / KM | Team Lead / Admin | Group Lead / Admin | Owner Only |
| **`delete`** | Admin / KM | Team Lead / Admin | Group Lead / Admin | Owner Only |

### JWT Token Context Payload:
To avoid querying Postgres on every single API request, the JWT access token encapsulates:
*   `user_id` (Subject ID)
*   `global_role` (System-wide role)
*   `department_ids` (Direct Team Spaces the user belongs to)

This payload is injected directly into `UserIdentity` at the middleware layer. Any hierarchal verification (checking if one team is a parent of another) is resolved dynamically in the database only when entering a `Team Space`.

---

## 4. Categories as Pure Classification Metadata (Dynamic Page Types)

Categories (formerly hardcoded `page_types` like `index`, `concept`) act strictly as **classification labels (tags)**. They dictate how content is displayed (UI organization) and searched (AI RAG filtering), but play **no role** in authorization (which is managed exclusively by the Namespace and ABAC Rules).

### 1. Purpose & Application:
*   **UI Dynamic Views (Notion-like Tabs):** Within a single Space (e.g., `Team: Tech`), pages can be grouped and filtered by category tabs on the frontend (e.g., a "Runbooks" tab showing 5 pages, an "API Specs" tab showing 6 pages).
*   **RAG Precision (AI Filters):** When an AI Agent searches for knowledge, it can restrict searches via metadata filtering (e.g., calling `search_wiki(query="...", category="Runbook")`), preventing outdated discussion drafts or policy documents from polluting the context window.
*   **Custom Review Rules:** While categories don't block read/write access, they can trigger workflow rules. For example, a page tagged as `Official Policy` might require a formal review pipeline, whereas a page tagged as `Meeting Notes` can be merged directly without review.

### 2. Database Schema:
```sql
CREATE TABLE categories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL,
    color VARCHAR(7) DEFAULT '#3b82f6' NOT NULL,
    namespace_id UUID REFERENCES namespaces(id) ON DELETE CASCADE -- Nullable for Global Categories
);
```

### 3. Category Administration:
*   **Global Categories (`namespace_id IS NULL`):** Global tags managed by System Admins, applicable to all spaces (e.g., `Template`, `Announcement`).
*   **Space-Scoped Categories (`namespace_id` defined):** Custom tags created by Space Owners, only visible and selectable within that specific Space (e.g., Tech Space has `API Spec`, HR Space has `Onboarding`).

### 4. Bulk & Hierarchical Assignment:
*   **Hierarchical Inheritance:** If a parent page `/tech/runbooks` is tagged with category `Runbook`, all child pages under it (`/tech/runbooks/deploy`, `/tech/runbooks/backup`) automatically inherit the `Runbook` category unless explicitly overridden by a specific child-level tag.
*   **Bulk Assignment API:** Provides a path to update multiple page categories simultaneously by passing a list of page IDs or a slug wildcard prefix (e.g., apply category `HR Policy` to `/hr/policies/*`).

