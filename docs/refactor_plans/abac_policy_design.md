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
