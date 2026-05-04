"use client";

import React, { useEffect, useState, useCallback, useMemo } from "react";
import { api, apiUpload } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { EmptyState } from "@/components/shared/empty-state";
import { WikiTypeBadge, wikiTypeGroupLabel, wikiTypeColor, wikiTypeIcon } from "@/components/wiki/wiki-type-badge";
import { WikiPageTree } from "@/components/wiki/wiki-page-tree";
import { WikiContent } from "@/components/wiki/wiki-content";
import { WikiSidebarRight } from "@/components/wiki/wiki-backlinks";
import { WikiGraph } from "@/components/wiki/wiki-graph";
import { ScopeBadge } from "@/components/shared/scope-badge";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetClose } from "@/components/ui/sheet";
import { WikiGraphData, WikiPageDetail, WikiPageSummary } from "@/types/wiki";

const WIKI_TYPE_TABS = ["all", "entity", "concept", "topic", "source"] as const;

type Project = {
  id: string;
  name: string;
  description?: string;
  workspace_type: string;
  status: string;
  member_count: number;
  source_count: number;
};

type Member = {
  employee_id: string;
  employee_name: string;
  employee_email: string;
  role: string;
};

type ProjectSource = {
  source_id: string;
  title?: string;
  source_type?: string;
  file_name?: string;
  status: string;
  progress?: number;
  progress_message?: string;
  knowledge_type_name?: string;
  added_at?: string;
};

type Employee = {
  id: string;
  name: string;
  email: string;
  role: string;
};

type Source = {
  id: string;
  title?: string;
  source_type?: string;
  status: string;
  knowledge_type_name?: string;
};

type Props = {
  project: Project;
  isAdmin: boolean;
  onBack: () => void;
};

const fileIcons: Record<string, string> = {
  pdf: "picture_as_pdf",
  docx: "description",
  xlsx: "table_chart",
  csv: "table_chart",
  txt: "article",
  md: "article",
  pptx: "slideshow",
};

function getFileExt(s: ProjectSource): string {
  const name = s.file_name || "";
  return name.split(".").pop()?.toLowerCase() || "";
}

export function ProjectDetail({ project, isAdmin, onBack }: Props) {
  const [members, setMembers] = useState<Member[]>([]);
  const [sources, setSources] = useState<ProjectSource[]>([]);
  const [allEmployees, setAllEmployees] = useState<Employee[]>([]);
  const [allSources, setAllSources] = useState<Source[]>([]);
  const [selectedEmpId, setSelectedEmpId] = useState("");
  const [selectedSourceId, setSelectedSourceId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<"wiki" | "sources" | "members">("wiki");
  const [wikiPages, setWikiPages] = useState<WikiPageSummary[]>([]);
  const [wikiLoading, setWikiLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [wikiTypeTab, setWikiTypeTab] = useState<string>("all");
  const [selectedWikiSlug, setSelectedWikiSlug] = useState<string | null>(null);
  const [selectedWikiPage, setSelectedWikiPage] = useState<WikiPageDetail | null>(null);
  const [showGraph, setShowGraph] = useState(false);

  const load = useCallback(async () => {
    try {
      const [m, s] = await Promise.all([
        api<Member[]>(`/api/projects/${project.id}/members`),
        api<ProjectSource[]>(`/api/projects/${project.id}/sources`),
      ]);
      setMembers(m);
      setSources(s);
    } catch {
      setMembers([]);
      setSources([]);
    }
  }, [project.id]);

  useEffect(() => {
    load();
    if (isAdmin) {
      Promise.all([
        api<Employee[]>("/api/employees"),
        api<Source[]>("/api/sources"),
      ]).then(([emps, srcs]) => {
        setAllEmployees(emps);
        setAllSources(srcs);
      }).catch(() => { });
    }
  }, [load, isAdmin]);

  // Load wiki pages from server-side scoped endpoint
  useEffect(() => {
    if (tab !== "wiki") return;
    setWikiLoading(true);
    api<WikiPageSummary[]>(`/api/projects/${project.id}/wiki?limit=100`)
      .then((pages) => setWikiPages(Array.isArray(pages) ? pages : []))
      .catch(() => setWikiPages([]))
      .finally(() => setWikiLoading(false));
  }, [tab, project.id]);

  // Wiki stats
  const wikiTypeCounts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const p of wikiPages) c[p.page_type] = (c[p.page_type] ?? 0) + 1;
    return c;
  }, [wikiPages]);

  const displayWikiPages = useMemo(() => {
    return wikiTypeTab === "all"
      ? wikiPages
      : wikiPages.filter((p) => p.page_type === wikiTypeTab);
  }, [wikiPages, wikiTypeTab]);

  // Polling: refresh sources while any are pending/processing
  const hasInProgress = sources.some((s) => s.status === "pending" || s.status === "processing");
  useEffect(() => {
    if (!hasInProgress) return;
    const timer = setInterval(async () => {
      try {
        const s = await api<ProjectSource[]>(`/api/projects/${project.id}/sources`);
        setSources(s);
      } catch { /* ignore */ }
    }, 4000);
    return () => clearInterval(timer);
  }, [hasInProgress, project.id]);

  const handleAddMember = async () => {
    if (!selectedEmpId) return;
    setError(null);
    try {
      await api(`/api/projects/${project.id}/members`, {
        method: "POST",
        body: { employee_id: selectedEmpId, role: "member" },
      });
      setSelectedEmpId("");
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add member");
    }
  };

  const handleRemoveMember = async (empId: string) => {
    if (!confirm("Remove this member from the project?")) return;
    setError(null);
    try {
      await api(`/api/projects/${project.id}/members/${empId}`, { method: "DELETE" });
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to remove member");
    }
  };

  const handleAddSource = async () => {
    if (!selectedSourceId) return;
    setError(null);
    try {
      await api(`/api/projects/${project.id}/sources`, {
        method: "POST",
        body: { source_id: selectedSourceId },
      });
      setSelectedSourceId("");
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add document");
    }
  };

  const handleRemoveSource = async (sourceId: string) => {
    if (!confirm("Remove this document from the workspace?")) return;
    setError(null);
    try {
      await api(`/api/projects/${project.id}/sources/${sourceId}`, { method: "DELETE" });
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to remove document");
    }
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setError(null);
    setUploading(true);
    try {
      const formData = new FormData();
      formData.append("file", file);
      formData.append("title", file.name);
      await apiUpload(`/api/projects/${project.id}/sources/upload`, formData);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
      e.target.value = "";
    }
  };

  const memberIds = new Set(members.map((m) => m.employee_id));
  const sourceIds = new Set(sources.map((s) => s.source_id));
  const availableEmployees = allEmployees.filter((e) => !memberIds.has(e.id));
  const availableSources = allSources.filter((s) => !sourceIds.has(s.id));

  const tabConfig = [
    { key: "wiki" as const, label: "Wiki", count: wikiPages.length, icon: "auto_stories" },
    { key: "sources" as const, label: "Documents", count: sources.length, icon: "description" },
    { key: "members" as const, label: "Members", count: members.length, icon: "group" },
  ];

  return (
    <div className="flex-1 flex flex-col min-h-0">
      {/* 2-col header: left = back + title, right = tabs */}
      <div className="flex items-end gap-4 pb-4">
        {/* Left: back button + project identity */}
        <div className="flex items-center gap-3 pb-3 shrink-0">
          <Button variant="ghost" size="sm" onClick={onBack} className="h-8 px-2">
            <span className="material-symbols-outlined text-base">arrow_back</span>
            <span className="ml-1 text-sm">Back</span>
          </Button>
          <div className="w-px h-5 bg-border" />
          <span className="material-symbols-outlined text-primary text-lg">
            {project.workspace_type === "customer" ? "domain" : "folder_special"}
          </span>
          <div className="flex flex-col min-w-0">
            <div className="flex items-center gap-2">
              <h1 className="text-lg font-semibold font-serif leading-tight truncate max-w-[260px]">
                {project.name}
              </h1>
              <Badge
                variant="outline"
                className={project.status === "active" ? "text-green-600 border-green-300 text-xs" : "text-muted-foreground text-xs"}
              >
                {project.status}
              </Badge>
            </div>
            {project.description && (
              <p className="text-xs text-muted-foreground truncate max-w-[260px]">{project.description}</p>
            )}
          </div>
        </div>

        {/* Right: tabs flush to bottom of header row */}
        <div className="flex items-end gap-1 flex-1 justify-end">
          {tabConfig.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
                tab === t.key
                  ? "border-primary text-primary"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              <span className="material-symbols-outlined text-base">{t.icon}</span>
              {t.label}
              <span className="ml-1 tabular-nums text-xs text-muted-foreground">{t.count}</span>
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div className="mt-4 text-sm text-destructive bg-destructive/10 px-4 py-2 rounded-lg flex items-center gap-2">
          <span className="material-symbols-outlined text-base">error</span>
          {error}
        </div>
      )}

      {/* ================================================================ */}
      {/* Members tab                                                      */}
      {/* ================================================================ */}
      {tab === "members" && (
        <div className="flex flex-col gap-4">
          {isAdmin && (
            <div className="bg-card rounded-xl border border-border shadow-sahara p-4 flex gap-2">
              <Select value={selectedEmpId} onValueChange={(v) => setSelectedEmpId(v ?? "")}>
                <SelectTrigger className="bg-background flex-1">
                  {selectedEmpId ? (
                    <span className="truncate">
                      {(() => {
                        const emp = availableEmployees.find((e) => e.id === selectedEmpId);
                        return emp ? `${emp.name} — ${emp.email}` : selectedEmpId;
                      })()}
                    </span>
                  ) : (
                    <SelectValue placeholder="Select employee to add..." />
                  )}
                </SelectTrigger>
                <SelectContent>
                  {availableEmployees.map((e) => (
                    <SelectItem key={e.id} value={e.id}>
                      {e.name} — {e.email}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Button
                disabled={!selectedEmpId}
                onClick={handleAddMember}
                className="bg-primary text-primary-foreground hover:bg-primary/90 shrink-0"
              >
                Add
              </Button>
            </div>
          )}

          {members.length === 0 ? (
            <div className="bg-card rounded-xl border border-border shadow-sahara">
              <EmptyState icon="group" title="No members yet" description="Add employees to give them access to this workspace's knowledge." />
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
              {members.map((m) => (
                <div
                  key={m.employee_id}
                  className="bg-card rounded-xl border border-border shadow-sahara p-4 flex items-start gap-3 group hover:border-primary/20 transition-all"
                >
                  <div className="w-9 h-9 rounded-full bg-primary/10 flex items-center justify-center shrink-0">
                    <span className="material-symbols-outlined text-primary text-sm">person</span>
                  </div>
                  <div className="flex flex-col flex-1 min-w-0">
                    <span className="text-sm font-medium truncate">{m.employee_name}</span>
                    <span className="text-xs text-muted-foreground truncate">{m.employee_email}</span>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <Badge variant="outline" className="text-xs capitalize">{m.role}</Badge>
                    {isAdmin && (
                      <button
                        onClick={() => handleRemoveMember(m.employee_id)}
                        className="text-muted-foreground hover:text-destructive transition-colors opacity-0 group-hover:opacity-100"
                      >
                        <span className="material-symbols-outlined text-base">close</span>
                      </button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ================================================================ */}
      {/* Documents tab — matching Knowledge Base table style              */}
      {/* ================================================================ */}
      {tab === "sources" && (
        <div className="flex flex-col gap-4">
          {isAdmin && (
            <div className="flex flex-col sm:flex-row gap-3">
              {/* Upload drop zone */}
              <label className="block cursor-pointer flex-1">
                <input
                  type="file"
                  className="hidden"
                  onChange={handleFileUpload}
                  disabled={uploading}
                  accept=".pdf,.docx,.doc,.txt,.md,.csv,.xlsx"
                />
                <div className="bg-card rounded-xl border-2 border-dashed border-border hover:border-primary/40 hover:bg-primary/5 transition-all p-5 flex items-center gap-3 h-full">
                  <span className="material-symbols-outlined text-xl text-muted-foreground">
                    {uploading ? "hourglass_empty" : "cloud_upload"}
                  </span>
                  <div className="flex flex-col">
                    <span className="text-sm font-medium text-foreground">
                      {uploading ? "Uploading..." : "Upload file to workspace"}
                    </span>
                    <span className="text-xs text-muted-foreground">
                      PDF, DOCX, TXT, MD, CSV, XLSX
                    </span>
                  </div>
                </div>
              </label>

              {/* Link existing source */}
              <div className="bg-card rounded-xl border border-border shadow-sahara p-3 flex gap-2 flex-1">
                <Select value={selectedSourceId} onValueChange={(v) => setSelectedSourceId(v ?? "")}>
                  <SelectTrigger className="bg-background flex-1">
                    {selectedSourceId ? (
                      <span className="truncate">
                        {(() => {
                          const s = availableSources.find((src) => src.id === selectedSourceId);
                          return s ? (s.title || s.id) : selectedSourceId;
                        })()}
                      </span>
                    ) : (
                      <SelectValue placeholder="Link existing document..." />
                    )}
                  </SelectTrigger>
                  <SelectContent>
                    {availableSources.map((s) => (
                      <SelectItem key={s.id} value={s.id}>
                        {s.title || s.id}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Button
                  disabled={!selectedSourceId}
                  onClick={handleAddSource}
                  className="bg-primary text-primary-foreground hover:bg-primary/90 shrink-0"
                >
                  <span className="material-symbols-outlined text-base mr-1">add</span>
                  Add
                </Button>
              </div>
            </div>
          )}

          {sources.length === 0 ? (
            <div className="bg-card rounded-xl border border-border shadow-sahara">
              <EmptyState icon="description" title="No documents yet" description="Upload files or link existing documents to build this workspace's knowledge base." />
            </div>
          ) : (
            <div className="bg-card rounded-xl border border-border shadow-sahara overflow-hidden">
              <Table>
                <TableHeader>
                  <TableRow className="hover:bg-transparent">
                    <TableHead className="text-xs uppercase tracking-wider">Name</TableHead>
                    <TableHead className="text-xs uppercase tracking-wider">Type</TableHead>
                    <TableHead className="text-xs uppercase tracking-wider">Status</TableHead>
                    <TableHead className="text-xs uppercase tracking-wider">Added</TableHead>
                    {isAdmin && (
                      <TableHead className="text-xs uppercase tracking-wider text-right">Actions</TableHead>
                    )}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {sources.map((s) => (
                    <TableRow key={s.source_id} className="hover:bg-secondary/30">
                      <TableCell>
                        <div className="flex items-center gap-2.5">
                          <span className="material-symbols-outlined text-muted-foreground text-base">
                            {fileIcons[getFileExt(s)] || (s.source_type === "url" ? "link" : "description")}
                          </span>
                          <span className="text-sm font-medium">{s.title || s.source_id}</span>
                        </div>
                      </TableCell>
                      <TableCell>
                        {s.knowledge_type_name ? (
                          <Badge variant="outline" className="text-xs font-medium">
                            {s.knowledge_type_name}
                          </Badge>
                        ) : (
                          <span className="text-xs text-muted-foreground">—</span>
                        )}
                      </TableCell>
                      <TableCell>
                        <div className="flex flex-col gap-1">
                          <div className="flex items-center gap-1.5">
                            <span className={`w-2 h-2 rounded-full ${s.status === "ready" ? "bg-green-500"
                              : s.status === "processing" ? "bg-yellow-500"
                                : s.status === "error" ? "bg-destructive"
                                  : "bg-muted-foreground"
                              }`} />
                            <span className="text-xs capitalize text-muted-foreground">{s.status}</span>
                            {s.status === "processing" && s.progress !== undefined && (
                              <span className="text-xs text-muted-foreground">({s.progress}%)</span>
                            )}
                          </div>
                          {(s.status === "processing" || s.status === "pending") && s.progress_message && (
                            <span className="text-[10px] text-muted-foreground truncate max-w-[180px]" title={s.progress_message}>
                              {s.progress_message}
                            </span>
                          )}
                        </div>
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {s.added_at ? new Date(s.added_at).toLocaleDateString() : "—"}
                      </TableCell>
                      {isAdmin && (
                        <TableCell className="text-right">
                          <DropdownMenu>
                            <DropdownMenuTrigger className="inline-flex items-center justify-center h-8 w-8 rounded-md hover:bg-accent hover:text-accent-foreground">
                              <span className="material-symbols-outlined text-base">more_vert</span>
                            </DropdownMenuTrigger>
                            <DropdownMenuContent align="end">
                              <DropdownMenuItem
                                onClick={() => handleRemoveSource(s.source_id)}
                                className="text-destructive focus:text-destructive"
                              >
                                <span className="material-symbols-outlined text-base mr-2">close</span>
                                Remove
                              </DropdownMenuItem>
                            </DropdownMenuContent>
                          </DropdownMenu>
                        </TableCell>
                      )}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </div>
      )}

      {/* ================================================================ */}
      {/* Wiki tab — inline viewer, no route changes                        */}
      {/* ================================================================ */}
      {tab === "wiki" && (
        showGraph ? (
          <WikiGraphInline
            projectId={project.id}
            onBack={() => setShowGraph(false)}
          />
        ) : (
        <div className="flex gap-0 -mx-6 md:-mx-8 -mb-6 md:-mb-8 flex-1 min-h-0 border-t border-border overflow-hidden">
          {/* Page Tree sidebar — scoped to workspace */}
          <WikiPageTree
            pagesUrl={`/api/projects/${project.id}/wiki?limit=200`}
            activeSlug={selectedWikiSlug ?? undefined}
            onPageSelect={(slug) => { setSelectedWikiSlug(slug); setSelectedWikiPage(null); }}
          />

          {/* Content area */}
          <div className="flex-1 overflow-y-auto px-8 py-6 min-w-0">
            {selectedWikiSlug ? (
              /* ---- Inline wiki page detail view ---- */
              <WikiDetailInline
                slug={selectedWikiSlug}
                projectId={project.id}
                onBack={() => { setSelectedWikiSlug(null); setSelectedWikiPage(null); }}
                onPageLoaded={setSelectedWikiPage}
                onNavigate={(slug) => { setSelectedWikiSlug(slug); setSelectedWikiPage(null); }}
              />
            ) : (
              /* ---- Wiki pages list view ---- */
              <>
                {wikiLoading ? (
                  <div className="flex items-center justify-center h-32">
                    <span className="material-symbols-outlined text-3xl text-muted-foreground animate-spin">
                      progress_activity
                    </span>
                  </div>
                ) : wikiPages.length === 0 ? (
                  <EmptyState
                    icon="auto_stories"
                    title="No wiki pages yet"
                    description="Upload documents in this workspace to automatically compile knowledge into wiki pages."
                  />
                ) : (
                  <>
                    {/* Stats bar + Graph View button on same row */}
                    <div className="flex flex-wrap items-center gap-3 mb-8">
                      <div className="flex items-center gap-2 bg-card border border-border rounded-xl px-4 py-2.5 shadow-sahara">
                        <span className="material-symbols-outlined text-base text-primary">article</span>
                        <span className="text-sm font-semibold text-foreground">{wikiPages.length}</span>
                        <span className="text-xs text-muted-foreground">Pages</span>
                      </div>
                      {Object.entries(wikiTypeCounts).sort((a, b) => b[1] - a[1]).map(([type, count]) => (
                        <div
                          key={type}
                          className="flex items-center gap-1.5 bg-card border border-border rounded-xl px-3 py-2.5 shadow-sahara"
                        >
                          <WikiTypeBadge type={type} />
                          <span className="text-xs text-muted-foreground tabular-nums">{count}</span>
                        </div>
                      ))}
                      <div className="flex items-center gap-2 ml-auto">
                        {wikiPages[0]?.updated_at && (
                          <div className="flex items-center gap-2 bg-card border border-border rounded-xl px-4 py-2.5 shadow-sahara">
                            <span className="material-symbols-outlined text-base text-muted-foreground">schedule</span>
                            <span className="text-xs text-muted-foreground">
                              Updated {new Date(wikiPages[0].updated_at).toLocaleDateString("en-US", {
                                month: "short",
                                day: "numeric",
                              })}
                            </span>
                          </div>
                        )}
                        <button
                          onClick={() => setShowGraph(true)}
                          className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium bg-primary text-primary-foreground hover:bg-primary/90 transition-colors shadow-sahara"
                        >
                          <span className="material-symbols-outlined text-base">hub</span>
                          Graph View
                        </button>
                      </div>
                    </div>

                    {/* Type filter tabs */}
                    <div className="flex items-center gap-1 mb-5 border-b border-border">
                      {WIKI_TYPE_TABS.map((wt) => {
                        const count = wt === "all"
                          ? wikiPages.length
                          : wikiTypeCounts[wt] ?? 0;
                        if (wt !== "all" && count === 0) return null;
                        return (
                          <button
                            key={wt}
                            onClick={() => setWikiTypeTab(wt)}
                            className={`px-3 py-2 text-xs font-medium capitalize border-b-2 transition-colors ${wikiTypeTab === wt
                              ? "border-primary text-primary"
                              : "border-transparent text-muted-foreground hover:text-foreground"
                              }`}
                          >
                            {wt === "all" ? "All" : wikiTypeGroupLabel(wt)}
                            <span className="ml-1.5 tabular-nums text-muted-foreground">
                              {count}
                            </span>
                          </button>
                        );
                      })}
                    </div>

                    {/* Wiki page cards — click opens inline */}
                    <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
                      {displayWikiPages.map((page) => (
                        <button
                          key={page.slug}
                          onClick={() => setSelectedWikiSlug(page.slug)}
                          className="group block bg-card border border-border rounded-xl p-4 hover:border-primary/40 hover:shadow-sahara transition-all text-left"
                        >
                          <div className="flex items-start justify-between gap-2 mb-2">
                            <div className="flex items-center gap-1.5 flex-wrap">
                              <WikiTypeBadge type={page.page_type} />
                              <ScopeBadge scopeType="workspace" />
                            </div>
                            <span className="text-xs text-muted-foreground shrink-0">
                              v{page.version}
                            </span>
                          </div>
                          <h3 className="font-heading text-base font-normal text-foreground group-hover:text-primary transition-colors mb-1">
                            {page.title}
                          </h3>
                          {page.summary && (
                            <p className="text-xs text-muted-foreground line-clamp-2">
                              {page.summary}
                            </p>
                          )}
                          <p className="text-xs text-muted-foreground mt-3">
                            {new Date(page.updated_at).toLocaleDateString()}
                          </p>
                        </button>
                      ))}
                    </div>
                  </>
                )}
              </>
            )}
          </div>

          {/* Right sidebar — shown when viewing a page, mirrors standalone wiki */}
          {selectedWikiSlug && selectedWikiPage && (
            <div className="hidden lg:flex shrink-0 overflow-hidden">
              <WikiSidebarRight slug={selectedWikiSlug} page={selectedWikiPage} />
            </div>
          )}
        </div>
        )
      )}
    </div>
  );
}

/* ================================================================ */
/* Inline wiki page detail viewer                                    */
/* ================================================================ */
function WikiDetailInline({
  slug,
  projectId,
  onBack,
  onPageLoaded,
  onNavigate,
}: {
  slug: string;
  projectId: string;
  onBack: () => void;
  onPageLoaded: (page: WikiPageDetail) => void;
  onNavigate: (slug: string) => void;
}) {
  const [page, setPage] = useState<WikiPageDetail | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    setPage(null);
    api<WikiPageDetail>(`/api/wiki/pages/${encodeURIComponent(slug)}?scope_type=project&scope_id=${projectId}`)
      .then((data) => { setPage(data); onPageLoaded(data); })
      .catch(() => setPage(null))
      .finally(() => setLoading(false));
  }, [slug, projectId]);

  if (loading) {
    return (
      <div className="max-w-3xl mx-auto">
        <div className="flex items-center gap-2 mb-6">
          <div className="h-4 w-16 rounded bg-muted animate-pulse" />
          <div className="h-4 w-24 rounded bg-muted animate-pulse" />
        </div>
        <div className="h-10 w-2/3 rounded-lg bg-muted animate-pulse mb-3" />
        <div className="h-4 w-full rounded bg-muted animate-pulse mb-2" />
        <div className="h-4 w-5/6 rounded bg-muted animate-pulse mb-8" />
        <div className="space-y-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <div
              key={i}
              className="h-4 rounded bg-muted animate-pulse"
              style={{ width: `${85 - i * 5}%`, opacity: 1 - i * 0.08 }}
            />
          ))}
        </div>
      </div>
    );
  }

  if (!page) {
    return (
      <div className="flex flex-col items-center justify-center py-16 gap-3">
        <span className="material-symbols-outlined text-4xl text-muted-foreground">find_in_page</span>
        <p className="text-sm text-muted-foreground">Page not found: {slug}</p>
        <Button variant="outline" size="sm" onClick={onBack}>
          <span className="material-symbols-outlined text-base mr-1">arrow_back</span>
          Back to list
        </Button>
      </div>
    );
  }

  return (
    <div className="max-w-3xl mx-auto">
      {/* Breadcrumb */}
      <div className="flex items-center gap-3 mb-6">
        <button
          onClick={onBack}
          className="flex items-center justify-center w-8 h-8 rounded-full border border-border bg-background text-muted-foreground hover:bg-accent hover:text-foreground transition-colors shrink-0 shadow-sm"
          title="Back to pages"
        >
          <span className="material-symbols-outlined text-[18px]">arrow_back</span>
        </button>
        <nav className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <button
            onClick={onBack}
            className="hover:text-foreground transition-colors font-medium"
          >
            Wiki
          </button>
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
        <div className="flex items-center gap-2 mb-2">
          <WikiTypeBadge type={page.page_type} />
          <ScopeBadge scopeType="workspace" />
          <span className="text-xs text-muted-foreground ml-auto">v{page.version}</span>
        </div>
        <h1 className="font-heading text-4xl font-normal leading-tight text-foreground">
          {page.title}
        </h1>
        {page.summary && (
          <p className="mt-2 text-muted-foreground text-sm leading-6">{page.summary}</p>
        )}
      </div>

      {/* Markdown body */}
      <WikiContent markdown={page.content_md} onWikiLinkClick={onNavigate} />
    </div>
  );
}

/* ================================================================ */
/* Inline graph viewer — mirrors /wiki/graph but scoped to project   */
/* ================================================================ */
const PAGE_TYPES = ["entity", "concept", "topic", "source"];

function WikiGraphInline({ projectId, onBack }: { projectId: string; onBack: () => void }) {
  const [graphData, setGraphData] = useState<WikiGraphData | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeTypes, setActiveTypes] = useState<Set<string>>(new Set(PAGE_TYPES));
  const [searchQuery, setSearchQuery] = useState("");
  const [highlightSlug, setHighlightSlug] = useState<string | null>(null);
  const [previewSlug, setPreviewSlug] = useState<string | null>(null);
  const [previewData, setPreviewData] = useState<WikiPageDetail | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);

  useEffect(() => {
    api<WikiGraphData>(`/api/projects/${projectId}/wiki/graph`)
      .then(setGraphData)
      .catch(() => setGraphData(null))
      .finally(() => setLoading(false));
  }, [projectId]);

  useEffect(() => {
    if (!previewSlug) { setPreviewData(null); return; }
    setPreviewLoading(true);
    api<WikiPageDetail>(`/api/wiki/pages/${previewSlug}?scope_type=project&scope_id=${projectId}`)
      .then(setPreviewData)
      .catch(() => setPreviewData(null))
      .finally(() => setPreviewLoading(false));
  }, [previewSlug, projectId]);

  const filteredData = useMemo(() => {
    if (!graphData) return null;
    const nodes = graphData.nodes.filter((n) => activeTypes.has(n.page_type));
    const slugSet = new Set(nodes.map((n) => n.slug));
    const edges = graphData.edges.filter((e) => slugSet.has(e.from) && slugSet.has(e.to));
    return { nodes, edges };
  }, [graphData, activeTypes]);

  const searchMatches = useMemo(() => {
    if (!searchQuery || !graphData) return [];
    const q = searchQuery.toLowerCase();
    return graphData.nodes.filter((n) => n.title.toLowerCase().includes(q) || n.slug.includes(q));
  }, [searchQuery, graphData]);

  return (
    <div
      className="relative flex flex-col -mx-6 md:-mx-8 -mb-6 md:-mb-8 flex-1 min-h-0 border-t border-border bg-background"
    >
      {/* Header */}
      <div className="flex items-center justify-between px-5 py-2.5 border-b border-border bg-card/80 backdrop-blur-sm shrink-0">
        <div className="flex items-center gap-3">
          <button
            onClick={onBack}
            className="text-muted-foreground hover:text-foreground transition-colors p-1 rounded-md hover:bg-accent/50"
          >
            <span className="material-symbols-outlined text-base">arrow_back</span>
          </button>
          <div className="flex items-center gap-2">
            <span className="material-symbols-outlined text-base text-muted-foreground">hub</span>
            <span className="text-sm font-semibold text-foreground">Workspace Graph</span>
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
              <button onClick={() => { setSearchQuery(""); setHighlightSlug(null); }} className="text-muted-foreground hover:text-foreground">
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
                  onClick={() => setActiveTypes((prev) => {
                    const next = new Set(prev);
                    next.has(type) ? next.delete(type) : next.add(type);
                    return next;
                  })}
                  className="flex items-center gap-1.5 rounded-lg px-2 py-1 text-xs transition-all border"
                  style={{
                    background: active ? `${color}14` : "transparent",
                    color: active ? color : "var(--color-muted-foreground, #78706a)",
                    borderColor: active ? `${color}30` : "transparent",
                  }}
                  title={wikiTypeGroupLabel(type)}
                >
                  <span className="w-2 h-2 rounded-full shrink-0" style={{ background: active ? color : "var(--color-muted-foreground, #78706a)" }} />
                  <span className="material-symbols-outlined" style={{ fontSize: 12 }}>{wikiTypeIcon(type)}</span>
                  <span className="hidden sm:inline">{wikiTypeGroupLabel(type)}</span>
                </button>
              );
            })}
          </div>
        </div>
      </div>

      {/* Search dropdown */}
      {searchQuery && searchMatches.length > 0 && (
        <div className="absolute top-[52px] right-5 z-20 bg-card border border-border rounded-xl shadow-lg py-1 max-h-48 overflow-y-auto w-64">
          {searchMatches.slice(0, 8).map((n) => (
            <button
              key={n.slug}
              onClick={() => { setHighlightSlug(n.slug); setSearchQuery(""); }}
              className="w-full flex items-center gap-2 px-3 py-2 text-left text-xs hover:bg-accent/50 transition-colors"
            >
              <span className="w-2 h-2 rounded-full shrink-0" style={{ background: wikiTypeColor(n.page_type) }} />
              <span className="truncate font-medium text-foreground">{n.title}</span>
              <span className="text-muted-foreground ml-auto capitalize text-[10px]">{n.page_type}</span>
            </button>
          ))}
        </div>
      )}

      {/* Graph canvas */}
      <div className="flex-1 min-h-0 overflow-hidden">
        {loading ? (
          <div className="w-full h-full flex items-center justify-center">
            <span className="material-symbols-outlined text-4xl animate-spin text-primary">progress_activity</span>
          </div>
        ) : !filteredData || filteredData.nodes.length === 0 ? (
          <div className="w-full h-full flex flex-col items-center justify-center gap-3">
            <span className="material-symbols-outlined text-5xl text-muted-foreground/30">hub</span>
            <p className="text-sm text-muted-foreground font-medium">No wiki pages yet</p>
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

      {/* Preview sheet */}
      <Sheet open={!!previewSlug} onOpenChange={(open) => !open && setPreviewSlug(null)}>
        <SheetContent showCloseButton={false} className="w-[400px] sm:w-[540px] p-0 flex flex-col border-l border-border gap-0">
          <SheetHeader className="px-6 py-4 border-b border-border bg-card shrink-0 flex flex-row items-center justify-between space-y-0">
            <SheetTitle className="text-lg font-heading flex-1 truncate pr-4 text-left">
              {previewLoading ? "Loading..." : previewData?.title ?? previewSlug}
            </SheetTitle>
            <SheetClose
              render={<Button variant="ghost" size="icon-sm" className="h-8 w-8 rounded-full text-muted-foreground hover:text-foreground" />}
            >
              <span className="material-symbols-outlined text-[18px]">close</span>
            </SheetClose>
          </SheetHeader>
          <div className="flex-1 overflow-y-auto p-6 bg-background">
            {previewLoading ? (
              <div className="flex items-center justify-center py-16">
                <span className="material-symbols-outlined text-3xl animate-spin text-primary">progress_activity</span>
              </div>
            ) : previewData ? (
              <WikiContent markdown={previewData.content_md} />
            ) : (
              <p className="text-sm text-muted-foreground py-16 text-center">Failed to load content.</p>
            )}
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
}
