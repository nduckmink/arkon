"use client";

import React, { useEffect, useState, useCallback } from "react";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { EmptyState } from "@/components/shared/empty-state";

type Project = {
  id: string;
  name: string;
  description?: string;
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
  status: string;
  knowledge_type_name?: string;
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

export function ProjectDetail({ project, isAdmin, onBack }: Props) {
  const [members, setMembers] = useState<Member[]>([]);
  const [sources, setSources] = useState<ProjectSource[]>([]);
  const [allEmployees, setAllEmployees] = useState<Employee[]>([]);
  const [allSources, setAllSources] = useState<Source[]>([]);
  const [selectedEmpId, setSelectedEmpId] = useState("");
  const [selectedSourceId, setSelectedSourceId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<"members" | "sources">("members");

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
      }).catch(() => {});
    }
  }, [load, isAdmin]);

  const handleAddMember = async () => {
    if (!selectedEmpId) return;
    setError(null);
    try {
      await api(`/api/projects/${project.id}/members`, {
        method: "POST",
        body: JSON.stringify({ employee_id: selectedEmpId, role: "member" }),
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
        body: JSON.stringify({ source_id: selectedSourceId }),
      });
      setSelectedSourceId("");
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add document");
    }
  };

  const handleRemoveSource = async (sourceId: string) => {
    if (!confirm("Remove this document from the project?")) return;
    setError(null);
    try {
      await api(`/api/projects/${project.id}/sources/${sourceId}`, { method: "DELETE" });
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to remove document");
    }
  };

  const memberIds = new Set(members.map((m) => m.employee_id));
  const sourceIds = new Set(sources.map((s) => s.source_id));
  const availableEmployees = allEmployees.filter((e) => !memberIds.has(e.id));
  const availableSources = allSources.filter((s) => !sourceIds.has(s.id));

  return (
    <div className="flex flex-col gap-6">
      {/* Header */}
      <div className="flex items-start gap-4">
        <Button variant="ghost" size="sm" onClick={onBack} className="mt-0.5">
          <span className="material-symbols-outlined text-base mr-1">arrow_back</span>
          Back
        </Button>
        <div className="flex flex-col gap-1">
          <div className="flex items-center gap-2">
            <span className="material-symbols-outlined text-primary">folder_special</span>
            <h1 className="text-2xl font-semibold font-serif">{project.name}</h1>
            <Badge
              variant="outline"
              className={project.status === "active" ? "text-green-600 border-green-300" : "text-muted-foreground"}
            >
              {project.status}
            </Badge>
          </div>
          {project.description && (
            <p className="text-sm text-muted-foreground">{project.description}</p>
          )}
        </div>
      </div>

      {error && (
        <div className="text-sm text-destructive bg-destructive/10 px-4 py-2 rounded-lg flex items-center gap-2">
          <span className="material-symbols-outlined text-base">error</span>
          {error}
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-2 border-b border-border">
        {(["members", "sources"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-2 text-sm font-medium capitalize border-b-2 transition-colors ${
              tab === t
                ? "border-primary text-primary"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            {t === "members" ? `Members (${members.length})` : `Documents (${sources.length})`}
          </button>
        ))}
      </div>

      {/* Members tab */}
      {tab === "members" && (
        <div className="flex flex-col gap-4">
          {isAdmin && (
            <div className="bg-card rounded-xl border border-border shadow-sahara p-4 flex gap-2">
              <Select value={selectedEmpId} onValueChange={(v) => setSelectedEmpId(v ?? "")}>
                <SelectTrigger className="bg-background flex-1">
                  <SelectValue placeholder="Select employee to add..." />
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
              <EmptyState icon="group" title="No members yet" description="Add employees to give them access to this project's documents" />
            </div>
          ) : (
            <div className="bg-card rounded-xl border border-border shadow-sahara divide-y divide-border">
              {members.map((m) => (
                <div key={m.employee_id} className="flex items-center justify-between px-5 py-3">
                  <div className="flex flex-col">
                    <span className="text-sm font-medium">{m.employee_name}</span>
                    <span className="text-xs text-muted-foreground">{m.employee_email}</span>
                  </div>
                  <div className="flex items-center gap-3">
                    <Badge variant="outline" className="text-xs capitalize">{m.role}</Badge>
                    {isAdmin && (
                      <button
                        onClick={() => handleRemoveMember(m.employee_id)}
                        className="text-muted-foreground hover:text-destructive transition-colors"
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

      {/* Sources tab */}
      {tab === "sources" && (
        <div className="flex flex-col gap-4">
          {isAdmin && (
            <div className="bg-card rounded-xl border border-border shadow-sahara p-4 flex gap-2">
              <Select value={selectedSourceId} onValueChange={(v) => setSelectedSourceId(v ?? "")}>
                <SelectTrigger className="bg-background flex-1">
                  <SelectValue placeholder="Select document to add..." />
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
                Add
              </Button>
            </div>
          )}

          {sources.length === 0 ? (
            <div className="bg-card rounded-xl border border-border shadow-sahara">
              <EmptyState icon="description" title="No documents yet" description="Add documents to make them available to project members via Claude" />
            </div>
          ) : (
            <div className="bg-card rounded-xl border border-border shadow-sahara divide-y divide-border">
              {sources.map((s) => (
                <div key={s.source_id} className="flex items-center justify-between px-5 py-3">
                  <div className="flex items-center gap-3">
                    <span className="material-symbols-outlined text-muted-foreground text-base">
                      {s.source_type === "url" ? "link" : "description"}
                    </span>
                    <div className="flex flex-col">
                      <span className="text-sm font-medium">{s.title || s.source_id}</span>
                      {s.knowledge_type_name && (
                        <span className="text-xs text-muted-foreground">{s.knowledge_type_name}</span>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    <span className={`w-2 h-2 rounded-full ${s.status === "ready" ? "bg-green-500" : "bg-muted-foreground"}`} />
                    {isAdmin && (
                      <button
                        onClick={() => handleRemoveSource(s.source_id)}
                        className="text-muted-foreground hover:text-destructive transition-colors"
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
    </div>
  );
}
