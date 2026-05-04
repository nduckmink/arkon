"use client";

import React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import { WikiPageSummary } from "@/types/wiki";
import { wikiTypeIcon, wikiTypeColor, wikiTypeGroupLabel } from "./wiki-type-badge";

const GROUP_ORDER = ["entity", "concept", "topic", "source"];

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
}: {
  activeSlug?: string;
  onDeleted?: () => void;
  /** Override the API URL to load pages from (default: /api/wiki/pages) */
  pagesUrl?: string;
  /** Query params to append to page links (e.g. "?scopeType=project&scopeId=xxx") */
  linkQueryParams?: string;
  /** If provided, clicks call this instead of navigating via Link */
  onPageSelect?: (slug: string) => void;
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

  const handleDelete = async (slug: string) => {
    // First click: arm; second click: execute
    if (armedSlug !== slug) {
      setArmedSlug(slug);
      return;
    }
    setArmedSlug(null);
    setDeletingSlug(slug);
    try {
      await api(`/api/wiki/pages/${encodeURIComponent(slug)}`, { method: "DELETE" });
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

  const totalCount = filtered.filter(
    (p) => p.page_type !== "index" && p.page_type !== "log"
  ).length;

  const toggleGroup = (type: string) =>
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      next.has(type) ? next.delete(type) : next.add(type);
      return next;
    });

  const currentSlug = activeSlug ?? pathname.replace(/^\/wiki\//, "");

  if (collapsed) {
    return (
      <div className="w-10 border-r border-border bg-card/30 flex flex-col items-center pt-4 gap-3 shrink-0">
        <button
          onClick={() => setCollapsed(false)}
          className="text-muted-foreground hover:text-foreground transition-colors"
          title="Expand page tree"
        >
          <span className="material-symbols-outlined text-base">chevron_right</span>
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
          <span className="material-symbols-outlined text-base">chevron_left</span>
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
                    {items.map((page) => {
                      const isActive = page.slug === currentSlug;
                      const isArmed = armedSlug === page.slug;
                      const isDeleting = deletingSlug === page.slug;
                      return (
                        <div
                          key={page.slug}
                          data-slug={page.slug}
                          className={cn(
                            "group flex items-center gap-1 rounded-lg mx-1 transition-all",
                            isActive
                              ? "bg-primary/10"
                              : "hover:bg-accent/50"
                          )}
                        >
                          {onPageSelect ? (
                            <button
                              onClick={() => onPageSelect(page.slug)}
                              className={cn(
                                "flex-1 flex items-center gap-2 px-2 py-1.5 text-xs min-w-0 transition-all text-left",
                                isActive
                                  ? "text-primary font-medium"
                                  : "text-muted-foreground hover:text-foreground"
                              )}
                              title={page.summary || page.title}
                            >
                              <span className="truncate">{page.title}</span>
                            </button>
                          ) : (
                            <Link
                              href={`/wiki/${page.slug}${linkQueryParams || ""}`}
                              className={cn(
                                "flex-1 flex items-center gap-2 px-2 py-1.5 text-xs min-w-0 transition-all",
                                isActive
                                  ? "text-primary font-medium"
                                  : "text-muted-foreground hover:text-foreground"
                              )}
                              title={page.summary || page.title}
                            >
                              <span className="truncate">{page.title}</span>
                            </Link>
                          )}

                          {/* Delete button — 2-stage */}
                          {isDeleting ? (
                            <span className="material-symbols-outlined text-xs text-destructive animate-pulse mr-1.5">
                              progress_activity
                            </span>
                          ) : isArmed ? (
                            <button
                              onClick={(e) => { e.stopPropagation(); handleDelete(page.slug); }}
                              className="shrink-0 mr-1 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-destructive text-destructive-foreground hover:bg-destructive/90 animate-pulse transition-colors"
                              title={`Click again to confirm delete "${page.title}"`}
                            >
                              Confirm
                            </button>
                          ) : (
                            <button
                              onClick={(e) => { e.preventDefault(); e.stopPropagation(); handleDelete(page.slug); }}
                              className="shrink-0 mr-1 opacity-0 group-hover:opacity-100 text-muted-foreground hover:text-destructive transition-all"
                              title={`Delete "${page.title}"`}
                            >
                              <span className="material-symbols-outlined" style={{ fontSize: 14 }}>delete</span>
                            </button>
                          )}
                        </div>
                      );
                    })}
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
