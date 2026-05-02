"use client";

import React, { useEffect, useState, useCallback } from "react";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

type KnowledgeType = { id: string; slug: string; name: string; color: string };
type Source = { id: string; title?: string; knowledge_type_name?: string };
type Scope = {
  id: string;
  scope_type: string;
  knowledge_type_slugs: string[] | null;
  source_ids: string[] | null;
};

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  label: string; // "Sales" or "John Doe"
  departmentId?: string;
  employeeId?: string;
};

export function ScopeDialog({ open, onOpenChange, label, departmentId, employeeId }: Props) {
  const [scopes, setScopes] = useState<Scope[]>([]);
  const [types, setTypes] = useState<KnowledgeType[]>([]);
  const [sources, setSources] = useState<Source[]>([]);
  const [ruleMode, setRuleMode] = useState<"type" | "source">("type");
  const [selectedTypeSlug, setSelectedTypeSlug] = useState("");
  const [selectedSourceId, setSelectedSourceId] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const loadScopes = useCallback(async () => {
    try {
      const url = departmentId
        ? `/api/departments/${departmentId}/scopes`
        : `/api/employees/${employeeId}/scopes`;
      const data = await api<Scope[]>(url);
      setScopes(data);
    } catch {
      setScopes([]);
    }
  }, [departmentId, employeeId]);

  useEffect(() => {
    if (!open) return;
    loadScopes();
    Promise.all([
      api<KnowledgeType[]>("/api/knowledge-types"),
      api<Source[]>("/api/sources"),
    ]).then(([t, s]) => {
      setTypes(t);
      setSources(s.filter((src: any) => src.status === "ready"));
    }).catch(() => {});
  }, [open, loadScopes]);

  const usedTypeSlugs = new Set(scopes.flatMap((s) => s.knowledge_type_slugs || []));
  const usedSourceIds = new Set(scopes.flatMap((s) => s.source_ids || []));
  const availableTypes = types.filter((t) => !usedTypeSlugs.has(t.slug));
  const availableSources = sources.filter((s) => !usedSourceIds.has(s.id));

  const handleAdd = async () => {
    if (ruleMode === "type" && !selectedTypeSlug) return;
    if (ruleMode === "source" && !selectedSourceId) return;
    setSaving(true);
    setError("");
    try {
      await api("/api/scopes", {
        method: "POST",
        body: JSON.stringify({
          department_id: departmentId || null,
          employee_id: employeeId || null,
          scope_type: "grant",
          knowledge_type_slugs: ruleMode === "type" ? [selectedTypeSlug] : null,
          source_ids: ruleMode === "source" ? [selectedSourceId] : null,
        }),
      });
      setSelectedTypeSlug("");
      setSelectedSourceId("");
      await loadScopes();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add rule");
    } finally {
      setSaving(false);
    }
  };

  const handleRemove = async (scopeId: string) => {
    setError("");
    try {
      await api(`/api/scopes/${scopeId}`, { method: "DELETE" });
      await loadScopes();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to remove rule");
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="text-xl">Knowledge Access — {label}</DialogTitle>
        </DialogHeader>

        <div className="flex flex-col gap-5 mt-1">
          {/* Explanation */}
          <div className={`rounded-lg px-4 py-3 text-sm flex items-start gap-2 ${
            scopes.length === 0
              ? "bg-green-50 text-green-800 border border-green-200"
              : "bg-amber-50 text-amber-800 border border-amber-200"
          }`}>
            <span className="material-symbols-outlined text-base mt-0.5 shrink-0">
              {scopes.length === 0 ? "lock_open" : "lock"}
            </span>
            {scopes.length === 0
              ? "No restrictions — full access to all knowledge. Add rules below to restrict."
              : `Access restricted to ${scopes.length} rule${scopes.length > 1 ? "s" : ""} below. Sources outside these rules are hidden.`
            }
          </div>

          {/* Current rules */}
          {scopes.length > 0 && (
            <div className="flex flex-col gap-2">
              <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Current access rules</p>
              <div className="flex flex-col gap-1.5">
                {scopes.map((scope) => (
                  <ScopeRule
                    key={scope.id}
                    scope={scope}
                    types={types}
                    sources={sources}
                    onRemove={() => handleRemove(scope.id)}
                  />
                ))}
              </div>
            </div>
          )}

          {/* Add rule */}
          <div className="flex flex-col gap-3 border border-border rounded-xl p-4 bg-secondary/20">
            <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Add access rule</p>

            {/* Mode toggle */}
            <div className="flex gap-1 p-1 bg-muted rounded-lg w-fit">
              {(["type", "source"] as const).map((m) => (
                <button
                  key={m}
                  onClick={() => setRuleMode(m)}
                  className={`px-3 py-1 rounded-md text-xs font-medium transition-colors ${
                    ruleMode === m
                      ? "bg-background text-foreground shadow-sm"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {m === "type" ? "By knowledge type" : "By specific document"}
                </button>
              ))}
            </div>

            <div className="flex gap-2">
              {ruleMode === "type" ? (
                <Select value={selectedTypeSlug} onValueChange={(v) => setSelectedTypeSlug(v ?? "")}>
                  <SelectTrigger className="bg-background flex-1">
                    <SelectValue placeholder={availableTypes.length === 0 ? "All types added" : "Select knowledge type..."} />
                  </SelectTrigger>
                  <SelectContent>
                    {availableTypes.map((t) => (
                      <SelectItem key={t.slug} value={t.slug}>
                        <div className="flex items-center gap-2">
                          <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ backgroundColor: t.color }} />
                          {t.name}
                        </div>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              ) : (
                <Select value={selectedSourceId} onValueChange={(v) => setSelectedSourceId(v ?? "")}>
                  <SelectTrigger className="bg-background flex-1">
                    <SelectValue placeholder={availableSources.length === 0 ? "All documents added" : "Select document..."} />
                  </SelectTrigger>
                  <SelectContent>
                    {availableSources.map((s) => (
                      <SelectItem key={s.id} value={s.id}>
                        <div className="flex flex-col">
                          <span>{s.title || s.id}</span>
                          {s.knowledge_type_name && (
                            <span className="text-xs text-muted-foreground">{s.knowledge_type_name}</span>
                          )}
                        </div>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}

              <Button
                disabled={saving || (ruleMode === "type" ? !selectedTypeSlug : !selectedSourceId)}
                onClick={handleAdd}
                className="bg-primary text-primary-foreground hover:bg-primary/90 shrink-0"
              >
                {saving ? (
                  <span className="material-symbols-outlined animate-spin text-sm">progress_activity</span>
                ) : "Add"}
              </Button>
            </div>
          </div>

          {error && (
            <p className="text-destructive text-sm bg-destructive/10 px-3 py-2 rounded-lg">{error}</p>
          )}

          <div className="flex justify-end">
            <Button variant="outline" onClick={() => onOpenChange(false)}>Done</Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function ScopeRule({
  scope,
  types,
  sources,
  onRemove,
}: {
  scope: Scope;
  types: KnowledgeType[];
  sources: Source[];
  onRemove: () => void;
}) {
  const typeLabels = scope.knowledge_type_slugs?.map((slug) => {
    const t = types.find((t) => t.slug === slug);
    return t ? { name: t.name, color: t.color } : { name: slug, color: "#888" };
  });

  const sourceLabels = scope.source_ids?.map((id) => {
    const s = sources.find((s) => s.id === id);
    return s?.title || id.slice(0, 8) + "…";
  });

  return (
    <div className="flex items-center justify-between gap-2 bg-background rounded-lg px-3 py-2 border border-border">
      <div className="flex items-center gap-2 flex-wrap flex-1 min-w-0">
        <span className="material-symbols-outlined text-sm text-primary shrink-0">check_circle</span>
        {typeLabels?.map((t) => (
          <Badge
            key={t.name}
            variant="outline"
            className="text-xs"
            style={{ borderColor: t.color, color: t.color }}
          >
            {t.name}
          </Badge>
        ))}
        {sourceLabels?.map((name) => (
          <Badge key={name} variant="secondary" className="text-xs font-normal">
            <span className="material-symbols-outlined text-xs mr-1">description</span>
            {name}
          </Badge>
        ))}
      </div>
      <button
        onClick={onRemove}
        className="text-muted-foreground hover:text-destructive transition-colors shrink-0 ml-1"
      >
        <span className="material-symbols-outlined text-base">close</span>
      </button>
    </div>
  );
}
