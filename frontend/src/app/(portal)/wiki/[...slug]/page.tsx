"use client";

import React from "react";
import { useParams, useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import { WikiPageDetail } from "@/types/wiki";
import { WikiPageTree } from "@/components/wiki/wiki-page-tree";
import { WikiContent } from "@/components/wiki/wiki-content";
import { WikiSidebarRight } from "@/components/wiki/wiki-backlinks";
import { wikiTypeGroupLabel } from "@/components/wiki/wiki-type-badge";
import { WikiSearchDialog } from "@/components/wiki/wiki-search-dialog";
import { EmptyState } from "@/components/shared/empty-state";
import { PageHeader } from "@/components/shared/page-header";
import { Button } from "@/components/ui/button";
import Link from "next/link";

export default function WikiPageViewer() {
  const params = useParams();
  const searchParams = useSearchParams();
  const slugParts = Array.isArray(params.slug) ? params.slug : [params.slug ?? ""];
  const fullSlug = slugParts.join("/");
  const scopeType = searchParams.get("scopeType") || undefined;
  const scopeId = searchParams.get("scopeId") || undefined;
  const isScoped = !!scopeType && scopeType !== "global";

  const [page, setPage] = React.useState<WikiPageDetail | null>(null);
  const [notFound, setNotFound] = React.useState(false);
  const [loading, setLoading] = React.useState(true);
  const [searchOpen, setSearchOpen] = React.useState(false);

  React.useEffect(() => {
    if (!fullSlug) return;
    setLoading(true);
    setNotFound(false);
    setPage(null);

    const scopeParams = isScoped
      ? `?scope_type=${scopeType}&scope_id=${scopeId}`
      : "";
    api<WikiPageDetail>(`/api/wiki/pages/${encodeURIComponent(fullSlug)}${scopeParams}`)
      .then((data) => setPage(data))
      .catch((err) => {
        if (err?.status === 404 || err?.message?.includes("404")) {
          setNotFound(true);
        }
      })
      .finally(() => setLoading(false));
  }, [fullSlug, scopeType, scopeId, isScoped]);

  React.useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        setSearchOpen(true);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  return (
    <>
      <PageHeader
        title="Knowledge Wiki"
        description="Compiled knowledge from your organization's documents."
        action={
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              onClick={() => setSearchOpen(true)}
              className="gap-2"
            >
              <span className="material-symbols-outlined text-base">search</span>
              Search
              <kbd className="hidden sm:inline-block ml-1 px-1.5 py-0.5 rounded border border-border text-xs font-mono text-muted-foreground">
                ⌘K
              </kbd>
            </Button>
            <Link
              href="/wiki/graph"
              className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
            >
              <span className="material-symbols-outlined" style={{ fontSize: 14 }}>hub</span>
              Graph View
            </Link>
          </div>
        }
      />

      <div
        className="flex-1 flex gap-0 -mx-6 md:-mx-8 lg:-mx-10 -mb-6 md:-mb-8 lg:-mb-10 min-h-0 border-t border-border overflow-hidden"
      >
        {/* Left: Page Tree */}
        <WikiPageTree
          activeSlug={fullSlug}
          pagesUrl={isScoped ? `/api/projects/${scopeId}/wiki?limit=200` : undefined}
          linkQueryParams={isScoped ? `?scopeType=${scopeType}&scopeId=${scopeId}` : undefined}
        />

        {/* Center: Content */}
        <div className="flex-1 overflow-y-auto min-w-0">
          {loading ? (
            /* Skeleton loading */
            <div className="max-w-3xl mx-auto px-8 py-8">
              <div className="flex items-center gap-2 mb-4">
                <div className="h-4 w-16 rounded bg-muted animate-pulse" />
                <div className="h-4 w-4 text-muted-foreground">/</div>
                <div className="h-4 w-24 rounded bg-muted animate-pulse" />
              </div>
              <div className="h-10 w-2/3 rounded-lg bg-muted animate-pulse mb-3" />
              <div className="h-4 w-full rounded bg-muted animate-pulse mb-2" />
              <div className="h-4 w-5/6 rounded bg-muted animate-pulse mb-8" />
              <div className="space-y-3">
                {Array.from({ length: 8 }).map((_, i) => (
                  <div
                    key={i}
                    className="h-4 rounded bg-muted animate-pulse"
                    style={{ width: `${85 - i * 5}%`, opacity: 1 - i * 0.08 }}
                  />
                ))}
              </div>
            </div>
          ) : notFound ? (
            <div className="px-8 py-12">
              <EmptyState
                icon="find_in_page"
                title="Page not found"
                description={`No wiki page found for "${fullSlug}". It may not have been compiled yet.`}
              />
            </div>
          ) : page ? (
            <div className="max-w-3xl mx-auto px-8 py-8">
              {/* Breadcrumb & Back Button */}
              <div className="flex items-center gap-3 mb-6">
                <Link
                  href={isScoped ? `/workspaces` : "/wiki"}
                  className="flex items-center justify-center w-8 h-8 rounded-full border border-border bg-background text-muted-foreground hover:bg-accent hover:text-foreground transition-colors shrink-0 shadow-sm"
                  title={isScoped ? "Back to Workspace" : "Back to Wiki Index"}
                >
                  <span className="material-symbols-outlined text-[18px]">arrow_back</span>
                </Link>

                <nav className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <Link
                    href={isScoped ? `/workspaces` : "/wiki"}
                    className="hover:text-foreground transition-colors font-medium"
                  >
                    {isScoped ? "Workspace" : "Wiki"}
                  </Link>
                  <span className="material-symbols-outlined text-muted-foreground/50" style={{ fontSize: 14 }}>chevron_right</span>
                  <span className="capitalize font-medium">
                    {wikiTypeGroupLabel(page.page_type)}
                  </span>
                  <span className="material-symbols-outlined text-muted-foreground/50" style={{ fontSize: 14 }}>chevron_right</span>
                  <span className="text-foreground font-semibold truncate max-w-[200px]">
                    {page.title}
                  </span>
                </nav>
              </div>

              {/* Page header */}
              <div className="mb-8">
                <h1 className="font-heading text-4xl font-normal leading-tight text-foreground">
                  {page.title}
                </h1>
                {page.summary && (
                  <p className="mt-2 text-muted-foreground text-sm leading-6">{page.summary}</p>
                )}
              </div>

              {/* Markdown body */}
              <WikiContent markdown={page.content_md} />
            </div>
          ) : null}
        </div>

        {/* Right: Sidebar (hidden on < lg) */}
        {page && (
          <div className="hidden lg:block h-full">
            <WikiSidebarRight slug={fullSlug} page={page} />
          </div>
        )}
      </div>

      <WikiSearchDialog open={searchOpen} onOpenChange={setSearchOpen} />
    </>
  );
}
