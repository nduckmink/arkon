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

## 4. Modern Glassmorphism & Micro-Animations

*   **Deep Dark Palette:** Implements HSL slate-dark tones (e.g., `#080c14` for background, `#0f172a` for cards) combined with deep borders and zero pure-blacks.
*   **Glassmorphism Overlays:** Header rails, dropdowns, and modal dialogs use background blur (`backdrop-blur-md`) with semi-transparent borders for a premium, premium feel.
*   **Fluid Transitions:** Menu collapsals, drag-and-drop actions, and page transitions use hardware-accelerated spring animations (e.g., via Framer Motion) to feel responsive and alive.
