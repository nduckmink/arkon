# Design: Arkon UI V2 - Spacious & Zen Knowledge Experience

This document details the refactoring plans for the Next.js Frontend UI/UX, aiming to eliminate clutter, maximize whitespace, and simplify the collaborative authoring flow.

---

## 1. The Zen Layout (Spacious Multi-Column)

To solve the "cramped" feel of the legacy interface, we decouple the page into collapsible, purpose-driven columns:

```
┌──────┬──────────────────┬───────────────────────────────────────────────────┬────────────────┐
│Space │ Wiki Navigation  │                   Zen Reader                      │Context Sidebar │
│Switch│ (Collapsible)    │                  (Centered Text)                  │(Collapsible)   │
│      │                  │                                                   │                │
│ 🌐   │  ├─ HR           │           # Title: Recruitment Guide              │ ├─ Table of     │
│ 👥   │  │  ├─ Manual     │                                                   │    Content     │
│ 💬   │  │  └─ FAQ        │           Lorem ipsum dolor sit amet...           │ ├─ Outlinks     │
│ 👤   │  └─ Marketing    │                                                   │ ├─ Backlinks    │
└──────┴──────────────────┴───────────────────────────────────────────────────┴────────────────┘
```

### Column Specifications:
1.  **Column 1: Space Switcher (Leftmost Rail):** A minimal icon-only bar (like Slack or Discord) to pivot between:
    *   🌐 `Global Space`
    *   👥 `Team Space`
    *   💬 `Group Space`
    *   👤 `Personal Space`
    *   *Effect:* Selecting a space completely filters and hot-swaps the active navigation tree, eliminating unrelated clutter.
2.  **Column 2: Wiki Navigation Tree:** Hierarchal page explorer. Fully collapsible via a shortcut (`Cmd/Ctrl + \`) or a toggle button to enter full-screen focus mode.
3.  **Column 3: Zen Reader & Editor (Centered Content):** Mid-screen container styled like Medium or Notion. Features spacious margins, clean typographic scale (e.g., `Outfit` for headings, `Inter` for body text), and hidden action buttons that only appear on hover.
4.  **Column 4: Context Sidebar (Right Rail):** Contains the Table of Contents (TOC), Outlinks, Backlinks, and associated Source documents. Toggles open automatically when the cursor approaches the right edge, or sits pinned if space permits.

---

## 2. The Selective Review Hub (Leader Dashboard)

Rather than forcing leaders to evaluate branches blindly or write complex code, we provide a unified **Review Hub**:

*   **Change-Set Lister:** Displays all proposed page drafts submitted for review in a clean, vertical checklist.
*   **Side-by-Side Diff Engine:** Clicking a draft opens a split view comparison:
    *   *Left Side:* The live published version (from `wiki_pages`).
    *   *Right Side:* The proposed draft version (with line additions highlighted in soft green and deletions in soft red).
    *   *Metadata Diff:* Clearly displays changes to Page Categories, Maturity Status, Outlinks, and Sources.
*   **Draft-Level Cherry-Picking:** Every draft in the list has an independent `[ ] Approve` checkbox. The leader can approve Pages 1-3, reject Page 4, and leave Pages 5-6 untouched. Clicking **"Publish Approved Changes"** merges only the checked items.

---

## 3. Contribution Dashboard (Employee Workspace)

Employees get a dedicated workspace called **"My Contributions"** to manage their drafts:
*   **Branches List:** Lists all personal branches currently in progress.
*   **Drafts Board (Kanban or List view):** Groups drafts inside a branch by lifecycle state:
    *   `Draft` (in_progress - only visible to the owner).
    *   `In Review` (ready_for_review - locked, waiting for approval).
    *   `Rejected` (needs revision with review notes).
    *   `Merged` (archive of published pages).
*   **Drag & Drop Promotion:** Lets users drag draft pages directly from their `Personal Space` onto a `Team Space` bucket to trigger the Cross-Space Promotion Request.

---

## 4. Pure Notion-Inspired Light Theme & Micro-Transitions

Following a clean, high-focus design language similar to Notion, we prioritize a highly polished **Light Theme** and discard dark mode default assumptions.

### 1. Color Palette:
*   **Editor Canvas & Reader (Workspace):** Pure porcelain white (`#ffffff`) to maximize focus and contrast.
*   **Sidebar & Switchers (Navigation):** Soft, muted gray-blue (`#f7f9fb` or `#f1f3f5`) to clearly separate hierarchy from content without visual noise.
*   **Text Hierarchy:** Deep charcoal/warm-dark gray (`#37352f` - identical to Notion's text color) instead of harsh pure black. This provides a warm, readable contrast that prevents eye strain during long reading sessions.
*   **Borders & Dividers:** Ultra-thin, soft borders (`#e2e8f0` or `#e8ecef`) to define boundaries elegantly.

### 2. Layout Aesthetics:
*   **Subtle Shadows:** Minimal card elevations using soft ambient shadows (e.g., `shadow-sm` or custom `0 1px 3px rgba(0,0,0,0.05)`) to keep the interface flat and modern.
*   **Active States:** Selected navigation items use a light background tint (`#eff6ff` or `#f3f4f6`) with matching subtle text color shifts.

### 3. Fluid Micro-Transitions:
*   **Hardware-Accelerated Springs:** Sidebars toggle open, space switchers pivot, and draft cards animate using smooth spring physics (e.g., Framer Motion or Radix transitions) to make the workspace feel reactive and premium.

