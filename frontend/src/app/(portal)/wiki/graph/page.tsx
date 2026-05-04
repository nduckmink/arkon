"use client";

import React from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import { WikiGraphData, WikiPageDetail } from "@/types/wiki";
import { WikiGraph } from "@/components/wiki/wiki-graph";
import { wikiTypeColor, wikiTypeGroupLabel, wikiTypeIcon } from "@/components/wiki/wiki-type-badge";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetClose } from "@/components/ui/sheet";
import { WikiContent } from "@/components/wiki/wiki-content";
import { Button } from "@/components/ui/button";

const PAGE_TYPES = ["entity", "concept", "topic", "source"];

export default function WikiGraphPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const projectId = searchParams.get("projectId");
  const isScoped = !!projectId;
  const [graphData, setGraphData] = React.useState<WikiGraphData | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [activeTypes, setActiveTypes] = React.useState<Set<string>>(new Set(PAGE_TYPES));
  const [searchQuery, setSearchQuery] = React.useState("");
  const [highlightSlug, setHighlightSlug] = React.useState<string | null>(null);

  // Preview panel states
  const [previewSlug, setPreviewSlug] = React.useState<string | null>(null);
  const [previewData, setPreviewData] = React.useState<WikiPageDetail | null>(null);
  const [previewLoading, setPreviewLoading] = React.useState(false);

  React.useEffect(() => {
    const url = projectId
      ? `/api/projects/${projectId}/wiki/graph`
      : "/api/wiki/graph";
    api<WikiGraphData>(url)
      .then((d) => setGraphData(d))
      .catch(() => setGraphData(null))
      .finally(() => setLoading(false));
  }, [projectId]);

  // Fetch preview data when a node is clicked
  React.useEffect(() => {
    if (!previewSlug) {
      setPreviewData(null);
      return;
    }
    setPreviewLoading(true);
    api<WikiPageDetail>(`/api/wiki/pages/${previewSlug}`)
      .then(setPreviewData)
      .catch(() => setPreviewData(null))
      .finally(() => setPreviewLoading(false));
  }, [previewSlug]);

  const filteredData = React.useMemo(() => {
    if (!graphData) return null;
    const nodes = graphData.nodes.filter((n) => activeTypes.has(n.page_type));
    const slugSet = new Set(nodes.map((n) => n.slug));
    const edges = graphData.edges.filter(
      (e) => slugSet.has(e.from) && slugSet.has(e.to)
    );
    return { nodes, edges };
  }, [graphData, activeTypes]);

  const searchMatches = React.useMemo(() => {
    if (!searchQuery || !graphData) return [];
    const q = searchQuery.toLowerCase();
    return graphData.nodes.filter(
      (n) => n.title.toLowerCase().includes(q) || n.slug.includes(q)
    );
  }, [searchQuery, graphData]);

  const toggleType = (type: string) => {
    setActiveTypes((prev) => {
      const next = new Set(prev);
      next.has(type) ? next.delete(type) : next.add(type);
      return next;
    });
  };

  return (
    <>
      <div 
        className="relative flex flex-col -mx-6 md:-mx-8 lg:-mx-10 !-mt-4 -mb-6 md:-mb-8 lg:-mb-10 bg-background" 
        style={{ height: "100vh" }}
      >
      {/* Header Bar */}
      <div className="flex items-center justify-between px-5 py-2.5 border-b border-border bg-card/80 backdrop-blur-sm shrink-0">
        <div className="flex items-center gap-3">
          <button
            onClick={() => router.push(isScoped ? `/workspaces` : "/wiki")}
            className="text-muted-foreground hover:text-foreground transition-colors p-1 rounded-md hover:bg-accent/50"
            title={isScoped ? "Back to Workspace" : "Back to Wiki"}
          >
            <span className="material-symbols-outlined text-base">arrow_back</span>
          </button>
          <div className="flex items-center gap-2">
            <span className="material-symbols-outlined text-base text-muted-foreground">hub</span>
            <span className="text-sm font-semibold text-foreground">
              {isScoped ? "Workspace Graph" : "Knowledge Graph"}
            </span>
          </div>
          {graphData && (
            <div className="flex items-center gap-1.5 text-xs text-muted-foreground ml-1">
              <span className="rounded-md bg-muted px-2 py-0.5 tabular-nums font-medium">
                {filteredData?.nodes.length ?? 0} pages
              </span>
              <span className="rounded-md bg-muted px-2 py-0.5 tabular-nums font-medium">
                {filteredData?.edges.length ?? 0} links
              </span>
            </div>
          )}
        </div>

        <div className="flex items-center gap-2">
          {/* Search */}
          <div className="flex items-center gap-2 bg-background border border-border rounded-lg px-2.5 py-1.5">
            <span className="material-symbols-outlined text-sm text-muted-foreground">search</span>
            <input
              type="text"
              placeholder="Find node..."
              value={searchQuery}
              onChange={(e) => {
                setSearchQuery(e.target.value);
                const match = graphData?.nodes.find((n) =>
                  n.title.toLowerCase().includes(e.target.value.toLowerCase())
                );
                setHighlightSlug(match?.slug ?? null);
              }}
              className="w-36 text-xs bg-transparent outline-none text-foreground placeholder:text-muted-foreground"
            />
            {searchQuery && (
              <button
                onClick={() => { setSearchQuery(""); setHighlightSlug(null); }}
                className="text-muted-foreground hover:text-foreground"
              >
                <span className="material-symbols-outlined text-sm">close</span>
              </button>
            )}
          </div>

          {/* Type filter chips */}
          <div className="flex items-center gap-1 border-l border-border pl-2 ml-1">
            {PAGE_TYPES.map((type) => {
              const active = activeTypes.has(type);
              const color = wikiTypeColor(type);
              return (
                <button
                  key={type}
                  onClick={() => toggleType(type)}
                  className="flex items-center gap-1.5 rounded-lg px-2 py-1 text-xs transition-all border"
                  style={{
                    background: active ? `${color}14` : "transparent",
                    color: active ? color : "var(--color-muted-foreground, #78706a)",
                    borderColor: active ? `${color}30` : "transparent",
                  }}
                  title={wikiTypeGroupLabel(type)}
                >
                  <span
                    className="w-2 h-2 rounded-full shrink-0 transition-colors"
                    style={{ background: active ? color : "var(--color-muted-foreground, #78706a)" }}
                  />
                  <span className="material-symbols-outlined" style={{ fontSize: 12 }}>
                    {wikiTypeIcon(type)}
                  </span>
                  <span className="hidden sm:inline">{wikiTypeGroupLabel(type)}</span>
                </button>
              );
            })}
          </div>
        </div>
      </div>

      {/* Search results dropdown */}
      {searchQuery && searchMatches.length > 0 && (
        <div className="absolute top-[52px] right-5 z-20 bg-card border border-border rounded-xl shadow-lg py-1 max-h-48 overflow-y-auto w-64">
          {searchMatches.slice(0, 8).map((n) => (
            <button
              key={n.slug}
              onClick={() => {
                setHighlightSlug(n.slug);
                setSearchQuery("");
              }}
              className="w-full flex items-center gap-2 px-3 py-2 text-left text-xs hover:bg-accent/50 transition-colors"
            >
              <span
                className="w-2 h-2 rounded-full shrink-0"
                style={{ background: wikiTypeColor(n.page_type) }}
              />
              <span className="truncate font-medium text-foreground">{n.title}</span>
              <span className="text-muted-foreground ml-auto capitalize text-[10px]">{n.page_type}</span>
            </button>
          ))}
        </div>
      )}

      {/* Graph canvas */}
      <div className="flex-1 min-h-0">
        {loading ? (
          <div className="w-full h-full flex items-center justify-center bg-background">
            <div className="flex flex-col items-center gap-3">
              <span className="material-symbols-outlined text-4xl animate-spin text-primary">
                progress_activity
              </span>
              <p className="text-sm text-muted-foreground">Building graph...</p>
            </div>
          </div>
        ) : !filteredData || filteredData.nodes.length === 0 ? (
          <div className="w-full h-full flex flex-col items-center justify-center gap-3 bg-background">
            <span className="material-symbols-outlined text-5xl text-muted-foreground/30">hub</span>
            <p className="text-sm text-muted-foreground font-medium">No wiki pages yet</p>
            <p className="text-xs text-muted-foreground">
              Upload and compile documents to start building the knowledge graph.
            </p>
          </div>
        ) : (
          <WikiGraph
            nodes={filteredData.nodes}
            edges={filteredData.edges}
            centerSlug={highlightSlug ?? undefined}
            height={undefined}
            onNodeClick={(slug) => setPreviewSlug(slug)}
          />
        )}
      </div>
    </div>

    {/* Preview Panel (Sheet) */}
      <Sheet open={!!previewSlug} onOpenChange={(open) => !open && setPreviewSlug(null)}>
        <SheetContent showCloseButton={false} className="w-[400px] sm:w-[540px] p-0 flex flex-col border-l border-border gap-0">
          <SheetHeader className="px-6 py-4 border-b border-border bg-card shrink-0 flex flex-row items-center justify-between space-y-0">
            <SheetTitle className="text-lg font-heading flex-1 truncate pr-4 text-left">
              {previewLoading ? "Loading..." : previewData?.title ?? previewSlug}
            </SheetTitle>
            <div className="flex items-center gap-2 shrink-0">
              <Button
                variant="outline"
                size="sm"
                onClick={() => router.push(`/wiki/${previewSlug}`)}
                className="h-8 text-xs font-medium"
              >
                <span className="material-symbols-outlined text-[16px] mr-1">open_in_new</span>
                Open Full
              </Button>
              <SheetClose 
                render={
                  <Button variant="ghost" size="icon-sm" className="h-8 w-8 rounded-full text-muted-foreground hover:text-foreground" />
                }
              >
                <span className="material-symbols-outlined text-[18px]">close</span>
              </SheetClose>
            </div>
          </SheetHeader>
          
          <div className="flex-1 overflow-y-auto p-6 bg-background">
            {previewLoading ? (
              <div className="flex flex-col items-center justify-center py-16 gap-3 text-muted-foreground">
                <span className="material-symbols-outlined text-3xl animate-spin text-primary">progress_activity</span>
                <span className="text-xs font-medium">Loading page...</span>
              </div>
            ) : previewData ? (
              <WikiContent markdown={previewData.content_md} />
            ) : (
              <p className="text-sm text-muted-foreground py-16 text-center">Failed to load content.</p>
            )}
          </div>
        </SheetContent>
      </Sheet>
    </>
  );
}
