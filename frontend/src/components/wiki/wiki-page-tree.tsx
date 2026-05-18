"use client";

import React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import { WikiPageSummary } from "@/types/wiki";
import { wikiTypeIcon, wikiTypeColor, wikiTypeGroupLabel } from "./wiki-type-badge";

const GROUP_ORDER = ["entity", "concept", "topic", "source"];

// Scope type ordering for grouped view: global → department → project.
const SCOPE_TYPE_ORDER: Record<string, number> = {
  global: 0,
  department: 1,
  project: 2,
};

const SCOPE_ICONS: Record<string, string> = {
  global: "public",
  department: "corporate_fare",
  project: "folder_special",
};

function scopeGroupKey(p: WikiPageSummary): string {
  const st = p.scope_type || "global";
  return p.scope_id ? `${st}:${p.scope_id}` : st;
}

function scopeGroupLabel(p: WikiPageSummary): string {
  const st = p.scope_type || "global";
  if (st === "global") return "Global";
  return p.scope_name || (st === "department" ? "Department" : "Workspace");
}

function pageKey(p: WikiPageSummary): string {
  return `${p.slug}-${p.scope_type || "global"}-${p.scope_id ?? "none"}`;
}

function useDebounce<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = React.useState(value);
  React.useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(t);
  }, [value, delay]);
  return debounced;
}

export function WikiPageTree({
  activeSlug,
  onDeleted,
  pagesUrl,
  linkQueryParams,
  onPageSelect,
  groupByScope = false,
  activeScope,
}: {
  activeSlug?: string;
  onDeleted?: () => void;
  /** Override the API URL to load pages from (default: /api/wiki/pages) */
  pagesUrl?: string;
  /** Query params to append to page links (e.g. "?scopeType=project&scopeId=xxx") */
  linkQueryParams?: string;
  /** If provided, clicks call this instead of navigating via Link */
  onPageSelect?: (slug: string) => void;
  /** When true, render a 2-level tree: scope → page_type → pages (used in /wiki). */
  groupByScope?: boolean;
  /** Auto-expand the bucket matching this scope (used together with groupByScope). */
  activeScope?: { scope_type: string; scope_id: string | null };
}) {
  const pathname = usePathname();
  const [pages, setPages] = React.useState<WikiPageSummary[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [search, setSearch] = React.useState("");
  const [collapsed, setCollapsed] = React.useState(false);
  const [expandedGroups, setExpandedGroups] = React.useState<Set<string>>(
    new Set(GROUP_ORDER)
  );
  // Two-stage delete
  const [armedSlug, setArmedSlug] = React.useState<string | null>(null);
  const [deletingSlug, setDeletingSlug] = React.useState<string | null>(null);

  const debouncedSearch = useDebounce(search, 150);

  const loadPages = React.useCallback(() => {
    const url = pagesUrl || "/api/wiki/pages?limit=200";
    api<WikiPageSummary[]>(url)
      .then((data) => setPages(Array.isArray(data) ? data : []))
      .catch(() => setPages([]))
      .finally(() => setLoading(false));
  }, [pagesUrl]);

  React.useEffect(() => {
    loadPages();
  }, [loadPages]);

  const handleDelete = async (page: WikiPageSummary) => {
    const slug = page.slug;
    // First click: arm; second click: execute
    if (armedSlug !== slug) {
      setArmedSlug(slug);
      return;
    }
    setArmedSlug(null);
    setDeletingSlug(slug);
    try {
      // Pass scope so the backend deletes the right copy when the same slug
      // exists in multiple scopes (e.g. global + project).
      const scopeQs =
        page.scope_type && page.scope_type !== "global" && page.scope_id
          ? `?scope_type=${page.scope_type}&scope_id=${page.scope_id}`
          : "";
      await api(`/api/wiki/pages/${encodeURIComponent(slug)}${scopeQs}`, {
        method: "DELETE",
      });
      loadPages();
      onDeleted?.();
    } catch (err) {
      console.error("Delete failed:", err);
    } finally {
      setDeletingSlug(null);
    }
  };

  // Click outside armed row → disarm
  React.useEffect(() => {
    if (!armedSlug) return;
    const handler = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      if (!target.closest(`[data-slug="${armedSlug}"]`)) {
        setArmedSlug(null);
      }
    };
    document.addEventListener("click", handler, true);
    return () => document.removeEventListener("click", handler, true);
  }, [armedSlug]);

  const filtered = React.useMemo(() => {
    if (!debouncedSearch) return pages;
    const q = debouncedSearch.toLowerCase();
    return pages.filter(
      (p) =>
        p.title.toLowerCase().includes(q) ||
        p.slug.toLowerCase().includes(q) ||
        p.summary.toLowerCase().includes(q)
    );
  }, [pages, debouncedSearch]);

  const grouped = React.useMemo(() => {
    const map = new Map<string, WikiPageSummary[]>();
    for (const p of filtered) {
      const t = p.page_type;
      if (t === "index" || t === "log") continue;
      if (!map.has(t)) map.set(t, []);
      map.get(t)!.push(p);
    }
    return map;
  }, [filtered]);

  // Scope-then-type grouping (used when groupByScope=true).
  // Outer map keyed by scopeGroupKey(); value is { label, scope_type, scope_id, byType }.
  const scopeGrouped = React.useMemo(() => {
    type ScopeBucket = {
      key: string;
      label: string;
      scope_type: string;
      scope_id: string | null;
      byType: Map<string, WikiPageSummary[]>;
      total: number;
    };
    const map = new Map<string, ScopeBucket>();
    for (const p of filtered) {
      if (p.page_type === "index" || p.page_type === "log") continue;
      // Workspaces (project scope) are reached via /workspaces — keep the
      // /wiki tree focused on enterprise-wide knowledge (global + departments).
      if ((p.scope_type || "global") === "project") continue;
      const k = scopeGroupKey(p);
      let bucket = map.get(k);
      if (!bucket) {
        bucket = {
          key: k,
          label: scopeGroupLabel(p),
          scope_type: p.scope_type || "global",
          scope_id: p.scope_id ?? null,
          byType: new Map(),
          total: 0,
        };
        map.set(k, bucket);
      }
      const t = p.page_type;
      if (!bucket.byType.has(t)) bucket.byType.set(t, []);
      bucket.byType.get(t)!.push(p);
      bucket.total += 1;
    }
    // Sort: scope type order first, then label alphabetical.
    return Array.from(map.values()).sort((a, b) => {
      const ao = SCOPE_TYPE_ORDER[a.scope_type] ?? 99;
      const bo = SCOPE_TYPE_ORDER[b.scope_type] ?? 99;
      if (ao !== bo) return ao - bo;
      return a.label.localeCompare(b.label);
    });
  }, [filtered]);

  const totalCount = filtered.filter(
    (p) => p.page_type !== "index" && p.page_type !== "log"
  ).length;

  // Expanded state for scope-level headers in groupByScope mode.
  // Default: expand global; collapse the rest to keep the tree compact.
  const [expandedScopes, setExpandedScopes] = React.useState<Set<string>>(
    new Set(["global"]),
  );
  const activeScopeKey = React.useMemo(() => {
    if (!activeScope) return null;
    return activeScope.scope_id
      ? `${activeScope.scope_type}:${activeScope.scope_id}`
      : activeScope.scope_type;
  }, [activeScope]);
  React.useEffect(() => {
    // When scope buckets first arrive, ensure global stays expanded and any
    // bucket containing the active page (or matching activeScope) is expanded.
    // Also pre-expand the type subgroups *inside* every expanded bucket so the
    // tree looks "open" by default — otherwise users navigating from /wiki to a
    // detail page see all the scope+type subgroups collapsed.
    if (!groupByScope || scopeGrouped.length === 0) return;

    const scopesToExpand = new Set<string>(["global"]);
    if (activeScopeKey) scopesToExpand.add(activeScopeKey);
    if (activeSlug) {
      for (const b of scopeGrouped) {
        for (const ps of b.byType.values()) {
          if (ps.some((p) => p.slug === activeSlug)) scopesToExpand.add(b.key);
        }
      }
    }

    setExpandedScopes((prev) => {
      const next = new Set(prev);
      for (const k of scopesToExpand) next.add(k);
      return next;
    });
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      for (const b of scopeGrouped) {
        if (!scopesToExpand.has(b.key)) continue;
        for (const type of b.byType.keys()) next.add(`${b.key}::${type}`);
      }
      return next;
    });
  }, [groupByScope, scopeGrouped, activeSlug, activeScopeKey]);

  const toggleGroup = (type: string) =>
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      next.has(type) ? next.delete(type) : next.add(type);
      return next;
    });

  const toggleScope = (key: string) =>
    setExpandedScopes((prev) => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });

  const currentSlug = activeSlug ?? pathname.replace(/^\/wiki\//, "");

  // Renders one leaf row (page link + delete button). Used in both flat and
  // group-by-scope tree modes so the row markup stays in one place.
  const renderPageItem = (page: WikiPageSummary) => {
    const isActive = page.slug === currentSlug;
    const isArmed = armedSlug === page.slug;
    const isDeleting = deletingSlug === page.slug;
    const linkSuffix = linkQueryParams
      ? linkQueryParams
      : page.scope_type && page.scope_type !== "global" && page.scope_id
        ? `?scopeType=${page.scope_type}&scopeId=${page.scope_id}`
        : "";
    return (
      <div
        key={pageKey(page)}
        data-slug={page.slug}
        className={cn(
          "group flex items-center gap-1 rounded-lg mx-1 transition-all",
          isActive ? "bg-primary/10" : "hover:bg-accent/50",
        )}
      >
        {onPageSelect ? (
          <button
            onClick={() => onPageSelect(page.slug)}
            className={cn(
              "flex-1 flex items-center gap-2 px-2 py-1.5 text-xs min-w-0 transition-all text-left",
              isActive ? "text-primary font-medium" : "text-muted-foreground hover:text-foreground",
            )}
            title={page.summary || page.title}
          >
            <span className="truncate">{page.title}</span>
          </button>
        ) : (
          <Link
            href={`/wiki/${page.slug}${linkSuffix}`}
            className={cn(
              "flex-1 flex items-center gap-2 px-2 py-1.5 text-xs min-w-0 transition-all",
              isActive ? "text-primary font-medium" : "text-muted-foreground hover:text-foreground",
            )}
            title={page.summary || page.title}
          >
            <span className="truncate">{page.title}</span>
          </Link>
        )}

        {isDeleting ? (
          <span className="material-symbols-outlined text-xs text-destructive animate-pulse mr-1.5">
            progress_activity
          </span>
        ) : isArmed ? (
          <button
            onClick={(e) => {
              e.stopPropagation();
              handleDelete(page);
            }}
            className="shrink-0 mr-1 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-destructive text-destructive-foreground hover:bg-destructive/90 animate-pulse transition-colors"
            title={`Click again to confirm delete "${page.title}"`}
          >
            Confirm
          </button>
        ) : (
          <button
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              handleDelete(page);
            }}
            className="shrink-0 mr-1 opacity-0 group-hover:opacity-100 text-muted-foreground hover:text-destructive transition-all"
            title={`Delete "${page.title}"`}
          >
            <span className="material-symbols-outlined" style={{ fontSize: 14 }}>delete</span>
          </button>
        )}
      </div>
    );
  };

  if (collapsed) {
    return (
      <div className="w-10 border-r border-border bg-card/30 flex flex-col items-center pt-4 gap-3 shrink-0">
        <button
          onClick={() => setCollapsed(false)}
          className="text-muted-foreground hover:text-foreground transition-colors"
          title="Expand page tree"
        >
          <span className="material-symbols-outlined text-base">left_panel_open</span>
        </button>
      </div>
    );
  }

  return (
    <div className="w-64 shrink-0 border-r border-border bg-card/30 flex flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-3 border-b border-border">
        <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wider flex-1">
          Pages
        </span>
        <span className="text-xs text-muted-foreground tabular-nums bg-muted rounded-md px-1.5 py-0.5">
          {totalCount}
        </span>
        <button
          onClick={() => setCollapsed(true)}
          className="text-muted-foreground hover:text-foreground transition-colors"
          title="Collapse"
        >
          <span className="material-symbols-outlined text-base">left_panel_close</span>
        </button>
      </div>

      {/* Search */}
      <div className="px-3 py-2 border-b border-border">
        <div className="flex items-center gap-2 bg-background border border-border rounded-lg px-2.5 py-1.5">
          <span className="material-symbols-outlined text-sm text-muted-foreground">
            search
          </span>
          <input
            type="text"
            placeholder="Filter pages..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="flex-1 text-xs bg-transparent outline-none text-foreground placeholder:text-muted-foreground"
          />
          {search && (
            <button
              onClick={() => setSearch("")}
              className="text-muted-foreground hover:text-foreground"
            >
              <span className="material-symbols-outlined text-sm">close</span>
            </button>
          )}
        </div>
      </div>

      {/* Tree */}
      <div className="flex-1 overflow-y-auto py-2">
        {loading ? (
          <div className="px-3 space-y-2 mt-1">
            {Array.from({ length: 6 }).map((_, i) => (
              <div
                key={i}
                className="h-7 rounded-md bg-muted animate-pulse"
                style={{ opacity: 1 - i * 0.12 }}
              />
            ))}
          </div>
        ) : groupByScope ? (
          scopeGrouped.length === 0 ? (
            <p className="text-xs text-muted-foreground px-4 py-3">No pages found.</p>
          ) : (
            scopeGrouped.map((bucket) => {
              const scopeExpanded = expandedScopes.has(bucket.key);
              const typeOrder = GROUP_ORDER.filter((t) => bucket.byType.has(t));
              const isActive = activeScopeKey === bucket.key;
              const scopeHref =
                bucket.scope_type === "global"
                  ? "/wiki"
                  : bucket.scope_id
                    ? `/wiki?scope_type=${bucket.scope_type}&scope_id=${bucket.scope_id}`
                    : `/wiki?scope_type=${bucket.scope_type}`;
              return (
                <div key={bucket.key} className="mb-2">
                  <div
                    className={cn(
                      "flex items-center gap-1 px-1 transition-colors rounded-md",
                      isActive ? "bg-accent/50" : "hover:bg-accent/30",
                    )}
                  >
                    <button
                      onClick={() => toggleScope(bucket.key)}
                      className="shrink-0 p-1 text-muted-foreground hover:text-foreground transition-colors"
                      title={scopeExpanded ? "Collapse" : "Expand"}
                    >
                      <span className="material-symbols-outlined text-xs">
                        {scopeExpanded ? "expand_more" : "chevron_right"}
                      </span>
                    </button>
                    <Link
                      href={scopeHref}
                      className="flex-1 flex items-center gap-2 py-1.5 min-w-0 text-left"
                      title={`Open ${bucket.label} wiki`}
                    >
                      <span
                        className="material-symbols-outlined text-xs text-muted-foreground"
                        style={{ fontSize: 13 }}
                      >
                        {SCOPE_ICONS[bucket.scope_type] ?? "tune"}
                      </span>
                      <span
                        className={cn(
                          "text-xs font-semibold uppercase tracking-wide flex-1 truncate",
                          isActive ? "text-primary" : "text-foreground",
                        )}
                      >
                        {bucket.label}
                      </span>
                      <span className="text-xs text-muted-foreground tabular-nums pr-2">
                        {bucket.total}
                      </span>
                    </Link>
                  </div>
                  {scopeExpanded && (
                    <div className="ml-3">
                      {typeOrder.map((type) => {
                        const items = bucket.byType.get(type)!;
                        const typeKey = `${bucket.key}::${type}`;
                        const isExpanded = expandedGroups.has(typeKey);
                        return (
                          <div key={typeKey} className="mb-1">
                            <button
                              onClick={() => toggleGroup(typeKey)}
                              className="w-full flex items-center gap-2 px-3 py-1.5 hover:bg-accent/40 transition-colors"
                            >
                              <span className="material-symbols-outlined text-xs text-muted-foreground">
                                {isExpanded ? "expand_more" : "chevron_right"}
                              </span>
                              <span
                                className="material-symbols-outlined text-xs"
                                style={{ color: wikiTypeColor(type), fontSize: 13 }}
                              >
                                {wikiTypeIcon(type)}
                              </span>
                              <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wide flex-1 text-left">
                                {wikiTypeGroupLabel(type)}
                              </span>
                              <span className="text-xs text-muted-foreground tabular-nums">
                                {items.length}
                              </span>
                            </button>
                            {isExpanded && (
                              <div className="ml-3">
                                {items.map((page) => renderPageItem(page))}
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })
          )
        ) : grouped.size === 0 ? (
          <p className="text-xs text-muted-foreground px-4 py-3">No pages found.</p>
        ) : (
          GROUP_ORDER.filter((t) => grouped.has(t)).map((type) => {
            const items = grouped.get(type)!;
            const isExpanded = expandedGroups.has(type);
            return (
              <div key={type} className="mb-1">
                <button
                  onClick={() => toggleGroup(type)}
                  className="w-full flex items-center gap-2 px-3 py-1.5 hover:bg-accent/40 transition-colors"
                >
                  <span className="material-symbols-outlined text-xs text-muted-foreground">
                    {isExpanded ? "expand_more" : "chevron_right"}
                  </span>
                  <span
                    className="material-symbols-outlined text-xs"
                    style={{ color: wikiTypeColor(type), fontSize: 13 }}
                  >
                    {wikiTypeIcon(type)}
                  </span>
                  <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wide flex-1 text-left">
                    {wikiTypeGroupLabel(type)}
                  </span>
                  <span className="text-xs text-muted-foreground tabular-nums">
                    {items.length}
                  </span>
                </button>
                {isExpanded && (
                  <div className="ml-3">
                    {items.map((page) => renderPageItem(page))}
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
