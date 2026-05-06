"use client";

import React from "react";
import { ScopeBadge } from "@/components/shared/scope-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { EmptyState } from "@/components/shared/empty-state";
import { Skill } from "./skill-card";
import { api, apiUpload } from "@/lib/api";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Checkbox } from "@/components/ui/checkbox";
import { SkillDetailDialog } from "./skill-detail-dialog";

type Department = {
  id: string;
  name: string;
};

type SkillTableProps = {
  skills: Skill[];
  departments: Department[];
  loading: boolean;
  onDelete: (id: string, name: string) => void;
  onRefresh: () => void;
  onClick: (slug: string) => void;
  onSearch: (q: string) => void;
  total: number;
  search: string;
};

export function SkillTable({
  skills,
  departments,
  loading,
  onDelete,
  onRefresh,
  onClick,
  onSearch,
  total,
  search,
}: SkillTableProps) {
  const [editSkill, setEditSkill] = React.useState<Skill | null>(null);
  const [uploadSkill, setUploadSkill] = React.useState<Skill | null>(null);
  const [searchInput, setSearchInput] = React.useState(search);

  return (
    <div className="flex flex-col gap-2">
      {/* Search bar + stats */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <div className="relative">
            <span className="material-symbols-outlined text-sm text-muted-foreground absolute left-3 top-1/2 -translate-y-1/2">
              search
            </span>
            <input
              type="text"
              value={searchInput}
              onChange={(e) => {
                const val = e.target.value;
                setSearchInput(val);
                onSearch(val);
              }}
              placeholder="Search skills..."
              className="h-9 pl-9 pr-3 text-sm rounded-lg border border-border bg-background focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary/50 w-[280px] placeholder:text-muted-foreground/60"
            />
            {searchInput && (
              <button
                type="button"
                onClick={() => { setSearchInput(""); onSearch(""); }}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
              >
                <span className="material-symbols-outlined text-sm">close</span>
              </button>
            )}
          </div>
        </div>
        <span className="text-xs text-muted-foreground tabular-nums">
          {total} skill{total !== 1 ? "s" : ""}
        </span>
      </div>

      {/* Table Container */}
      <div className="bg-card rounded-xl border border-border shadow-sahara overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center py-24">
            <span className="material-symbols-outlined text-3xl text-muted-foreground animate-spin">progress_activity</span>
          </div>
        ) : skills.length === 0 ? (
          <EmptyState
            icon="smart_toy"
            title={search ? "No results found" : "No skills found"}
            description={search ? `No skills matching "${search}"` : "Upload skills to start building your AI library."}
          />
        ) : (
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent border-b border-border/50">
                <TableHead className="text-[11px] uppercase tracking-wider font-bold text-muted-foreground">Skill</TableHead>
                <TableHead className="text-[11px] uppercase tracking-wider font-bold text-muted-foreground">Version</TableHead>
                <TableHead className="text-[11px] uppercase tracking-wider font-bold text-muted-foreground">Visibility</TableHead>
                <TableHead className="text-[11px] uppercase tracking-wider font-bold text-muted-foreground">Department</TableHead>
                <TableHead className="text-[11px] uppercase tracking-wider font-bold text-muted-foreground">Status</TableHead>
                <TableHead className="text-[11px] uppercase tracking-wider font-bold text-muted-foreground">Updated</TableHead>
                <TableHead className="text-[11px] uppercase tracking-wider font-bold text-muted-foreground text-right w-[80px]"></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {skills.map((skill) => (
                <TableRow
                  key={skill.id}
                  className="group hover:bg-secondary/30 transition-colors cursor-pointer border-b border-border/40"
                  onClick={() => onClick(skill.slug)}
                >
                  {/* Skill Name */}
                  <TableCell>
                    <div className="flex items-center gap-2.5 py-0.5">
                      <div className="w-6 h-6 rounded-md bg-secondary/40 flex items-center justify-center shrink-0 transition-colors group-hover:bg-secondary/60">
                        <span className="material-symbols-outlined text-muted-foreground/40 text-[16px] group-hover:scale-110 transition-transform duration-300">auto_awesome</span>
                      </div>
                      <div className="flex flex-col min-w-0">
                        <span className="text-sm font-medium text-foreground truncate max-w-[400px] group-hover:text-primary transition-colors font-manrope">
                          {skill.name}
                        </span>
                      </div>
                    </div>
                  </TableCell>

                  {/* Version */}
                  <TableCell>
                    <Badge variant="outline" className="bg-secondary/50 border-border text-[10px] font-mono font-bold px-2 py-0 h-5">
                      v{skill.current_version}
                    </Badge>
                  </TableCell>

                  {/* Visibility */}
                  <TableCell>
                    <ScopeBadge scopeType={skill.scope_type} scopeId={skill.scope_id} />
                  </TableCell>


                  {/* Department */}
                  <TableCell>
                    <div className="flex flex-wrap items-center gap-1.5 max-w-[200px]">
                      {skill.department_names && skill.department_names.length > 0 ? (
                        skill.department_names.map((dept, idx) => (
                          <div key={idx} className="flex items-center gap-1 text-[10px] text-muted-foreground font-semibold uppercase tracking-wider bg-secondary/30 px-1.5 py-0.5 rounded-md">
                            <span className="material-symbols-outlined text-[12px]">corporate_fare</span>
                            {dept}
                          </div>
                        ))
                      ) : (
                        <div className="flex items-center gap-1 text-[10px] text-muted-foreground font-semibold uppercase tracking-wider">
                          <span className="material-symbols-outlined text-[12px]">public</span>
                          Global
                        </div>
                      )}
                    </div>
                  </TableCell>

                  {/* Status */}
                  <TableCell>
                    <div className="flex items-center gap-1.5">
                      <span className={`w-2 h-2 rounded-full ${skill.status === "active" ? "bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.4)]" :
                        skill.status === "deleting" ? "bg-destructive animate-pulse" : "bg-yellow-500"
                        }`} />
                      <span className="text-[11px] font-bold uppercase tracking-widest text-muted-foreground/70">
                        {skill.status}
                      </span>
                    </div>
                  </TableCell>

                  {/* Updated At */}
                  <TableCell>
                    <span className="text-xs text-muted-foreground tabular-nums font-manrope">
                      {(() => {
                        const d = new Date(skill.updated_at);
                        const day = String(d.getDate()).padStart(2, '0');
                        const month = String(d.getMonth() + 1).padStart(2, '0');
                        const year = d.getFullYear();
                        return `${day}/${month}/${year}`;
                      })()}
                    </span>
                  </TableCell>

                  {/* Actions */}
                  <TableCell className="text-right" onClick={(e) => e.stopPropagation()}>
                    <DropdownMenu>
                      <DropdownMenuTrigger className="inline-flex items-center justify-center h-8 w-8 rounded-full hover:bg-secondary text-muted-foreground transition-colors focus:outline-none">
                        <span className="material-symbols-outlined text-lg">more_vert</span>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end" className="w-40 rounded-xl shadow-xl border-border">
                        <DropdownMenuItem onClick={() => setUploadSkill(skill)} className="flex items-center gap-2 py-2.5 cursor-pointer">
                          <span className="material-symbols-outlined text-base text-primary">upload_file</span>
                          Update skill
                        </DropdownMenuItem>
                        <DropdownMenuItem onClick={() => setEditSkill(skill)} className="flex items-center gap-2 py-2.5 cursor-pointer">
                          <span className="material-symbols-outlined text-base">edit</span>
                          Edit Skill
                        </DropdownMenuItem>
                        <DropdownMenuSeparator className="bg-border/50" />
                        <DropdownMenuItem
                          onClick={() => onDelete(skill.id, skill.name)}
                          className="flex items-center gap-2 py-2.5 text-destructive focus:text-destructive cursor-pointer"
                        >
                          <span className="material-symbols-outlined text-base">delete</span>
                          Delete
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>
      {editSkill && (
        <EditSkillDialog
          skill={editSkill}
          departments={departments}
          onClose={() => setEditSkill(null)}
          onSaved={() => { setEditSkill(null); onRefresh(); }}
        />
      )}
      {uploadSkill && (
        <UploadVersionDialog
          skill={uploadSkill}
          onClose={() => setUploadSkill(null)}
          onUploaded={() => { setUploadSkill(null); onRefresh(); }}
        />
      )}
    </div>
  );
}

function EditSkillDialog({
  skill,
  departments,
  onClose,
  onSaved,
}: {
  skill: Skill;
  departments: Department[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = React.useState(skill.name);
  const [scopeType, setScopeType] = React.useState(skill.scope_type || "global");
  const [deptIds, setDeptIds] = React.useState<string[]>(skill.department_ids || []);
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState("");


  const handleSave = async () => {
    setSaving(true);
    setError("");
    try {
      await api(`/api/skills/${skill.id}`, {
        method: "PATCH",
        body: {
          name: name || undefined,
          department_ids: scopeType === "department" ? deptIds : [],
          scope_type: scopeType,
          scope_id: scopeType === "department" && deptIds.length > 0 ? deptIds[0] : null,
        },
      });
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open onOpenChange={onClose}>
      <DialogContent className="sm:max-w-md rounded-2xl">
        <DialogHeader>
          <DialogTitle className="text-xl font-serif">Edit Skill</DialogTitle>
        </DialogHeader>

        <div className="flex flex-col gap-4 mt-2">
          <div className="flex flex-col gap-1.5">
            <Label className="text-xs font-bold uppercase tracking-wider text-muted-foreground">Skill Name</Label>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="bg-background rounded-xl h-11"
              placeholder="e.g. Data Analyst"
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label className="text-xs font-bold uppercase tracking-wider text-muted-foreground">Visibility</Label>
            <Select value={scopeType} onValueChange={(v) => setScopeType(v || "global")}>
              <SelectTrigger className="bg-background rounded-xl h-11">
                <div className="flex items-center gap-2">
                  <span className="material-symbols-outlined text-base text-muted-foreground">
                    {scopeType === "global" ? "public" : "corporate_fare"}
                  </span>
                  <span className="capitalize">
                    {scopeType === "global" ? "Global" : "Department"}
                  </span>
                </div>
              </SelectTrigger>
              <SelectContent className="rounded-xl">
                <SelectItem value="global">
                  <div className="flex items-center gap-2">
                    <span className="material-symbols-outlined text-base text-muted-foreground">public</span>
                    Global (All Departments)
                  </div>
                </SelectItem>
                <SelectItem value="department">
                  <div className="flex items-center gap-2">
                    <span className="material-symbols-outlined text-base text-muted-foreground">corporate_fare</span>
                    Specific Departments
                  </div>
                </SelectItem>
              </SelectContent>
            </Select>
          </div>

          {scopeType === "department" && (
            <div className="flex flex-col gap-1.5 animate-in fade-in slide-in-from-top-2 duration-300">
              <Label className="text-xs font-bold uppercase tracking-wider text-muted-foreground">Target Departments</Label>
              <div className="bg-background rounded-xl border border-border p-3">
                <div className="max-h-[200px] pr-4 overflow-y-auto custom-scrollbar">
                  <div className="grid grid-cols-1 gap-2">
                    {departments.map((d) => (
                      <div key={d.id} className="flex items-center space-x-2 group/item">
                        <Checkbox
                          id={`dept-${d.id}`}
                          checked={deptIds.includes(d.id)}
                          onCheckedChange={(checked) => {
                            if (checked) {
                              setDeptIds([...deptIds, d.id]);
                            } else {
                              setDeptIds(deptIds.filter(id => id !== d.id));
                            }
                          }}
                        />
                        <label
                          htmlFor={`dept-${d.id}`}
                          className="text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70 cursor-pointer group-hover/item:text-primary transition-colors"
                        >
                          {d.name}
                        </label>
                      </div>
                    ))}
                    {departments.length === 0 && (
                      <span className="text-xs text-muted-foreground italic">No departments available</span>
                    )}
                  </div>
                </div>
              </div>
            </div>
          )}


          {error && (
            <div className="text-destructive text-[11px] font-bold bg-destructive/5 px-3 py-2 rounded-xl flex items-center gap-2 border border-destructive/10">
              <span className="material-symbols-outlined text-sm">error</span>
              {error}
            </div>
          )}

          <div className="flex justify-end gap-2 mt-4">
            <Button variant="ghost" onClick={onClose} className="rounded-xl h-11 px-6">Cancel</Button>
            <Button
              disabled={saving}
              onClick={handleSave}
              className="bg-primary text-primary-foreground hover:bg-primary/90 rounded-xl h-11 px-8 font-bold shadow-lg shadow-primary/20"
            >
              {saving ? (
                <span className="flex items-center gap-2">
                  <span className="material-symbols-outlined animate-spin text-sm">progress_activity</span>
                  Saving...
                </span>
              ) : "Save Changes"}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function UploadVersionDialog({
  skill,
  onClose,
  onUploaded,
}: {
  skill: Skill;
  onClose: () => void;
  onUploaded: () => void;
}) {
  const [isUploading, setIsUploading] = React.useState(false);
  const [error, setError] = React.useState("");
  const fileInputRef = React.useRef<HTMLInputElement>(null);

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    if (!file.name.endsWith(".zip")) {
      setError("Please select a ZIP file");
      return;
    }

    setIsUploading(true);
    setError("");

    const formData = new FormData();
    formData.append("file", file);

    console.log(`[UploadDialog] Starting upload for skill: ${skill.name}, slug: ${skill.slug}`);
    const uploadUrl = `/api/skills/${skill.slug}/reupload`;
    console.log(`[UploadDialog] Target URL: ${uploadUrl}`);

    try {
      await apiUpload(uploadUrl, formData);
      console.log(`[UploadDialog] Upload successful`);
      onUploaded();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to upload");
    } finally {
      setIsUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  return (
    <Dialog open onOpenChange={onClose}>
      <DialogContent className="sm:max-w-md rounded-2xl">
        <DialogHeader>
          <DialogTitle className="text-xl font-serif">Upload New Version</DialogTitle>
        </DialogHeader>

        <div className="flex flex-col gap-6 mt-4 items-center py-4">
          <div className="w-20 h-20 rounded-full bg-primary/5 flex items-center justify-center text-primary animate-in zoom-in-50 duration-500">
            <span className="material-symbols-outlined text-4xl">cloud_upload</span>
          </div>

          <div className="text-center space-y-1">
            <p className="text-sm font-medium text-foreground">Update <span className="text-primary font-bold">{skill.name}</span></p>
            <p className="text-xs text-muted-foreground px-8">Upload a new ZIP package to replace the current files and create a new version.</p>
          </div>

          <div className="w-full">
            <Button 
              className="w-full h-12 rounded-xl bg-primary text-primary-foreground font-bold shadow-lg shadow-primary/20 gap-2"
              onClick={() => fileInputRef.current?.click()}
              disabled={isUploading}
            >
              {isUploading ? (
                <span className="material-symbols-outlined animate-spin text-lg">progress_activity</span>
              ) : (
                <span className="material-symbols-outlined text-lg">upload_file</span>
              )}
              {isUploading ? "Uploading..." : "Select ZIP Package"}
            </Button>
            <input 
              type="file" 
              ref={fileInputRef} 
              className="hidden" 
              accept=".zip"
              onChange={handleUpload}
            />
          </div>

          {error && (
            <div className="w-full text-destructive text-[11px] font-bold bg-destructive/5 px-3 py-2 rounded-xl flex items-center gap-2 border border-destructive/10 animate-in fade-in slide-in-from-top-1">
              <span className="material-symbols-outlined text-sm">error</span>
              {error}
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
