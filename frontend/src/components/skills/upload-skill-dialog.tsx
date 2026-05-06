"use client";

import { useState, useRef, useMemo, useEffect } from "react";
import { apiUpload, api, ApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Checkbox } from "@/components/ui/checkbox";

type UploadSkillDialogProps = {
  allDepartments: { id: string; name: string }[];
  onUploaded: () => void;
};

export function UploadSkillDialog({ allDepartments, onUploaded }: UploadSkillDialogProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [uploadLoading, setUploadLoading] = useState(false);

  const [selectedFiles, setSelectedFiles] = useState<FileList | null>(null);
  const [scopeType, setScopeType] = useState("global");
  const [deptIds, setDeptIds] = useState<string[]>([]);
  const [force, setForce] = useState(false);
  const [conflictFiles, setConflictFiles] = useState<string[]>([]);

  const inputRef = useRef<HTMLInputElement>(null);

  const resetForm = () => {
    setSelectedFiles(null);
    setScopeType("global");
    setDeptIds([]);
    setForce(false);
    setConflictFiles([]);
  };

  const handleUpload = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (!selectedFiles || selectedFiles.length === 0) return;

    try {
      setUploadLoading(true);
      const formData = new FormData();
      for (let i = 0; i < selectedFiles.length; i++) {
        formData.append("files", selectedFiles[i]);
      }
      formData.append("scope_type", scopeType);
      
      if (scopeType === "department" && deptIds.length > 0) {
        deptIds.forEach(id => {
          formData.append("department_ids", id);
        });
        formData.append("scope_id", deptIds[0]); // Legacy support
      }

      if (force) {
        formData.append("force", "true");
      }

      await apiUpload("/api/skills/upload", formData);
      onUploaded();
      setIsOpen(false);
      resetForm();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setConflictFiles(err.data?.detail?.conflicts || []);
      } else {
        alert(err instanceof Error ? err.message : "Upload failed");
      }
    } finally {
      setUploadLoading(false);
    }
  };


  return (
    <Dialog open={isOpen} onOpenChange={(open) => {
      setIsOpen(open);
      if (!open) resetForm();
    }}>
      <DialogTrigger
        render={
          <Button className="bg-primary text-primary-foreground hover:bg-primary/90 shadow-sahara">
            <span className="material-symbols-outlined text-base mr-1">upload</span>
            Upload Skill
          </Button>
        }
      />
      <DialogContent className="sm:max-w-[500px]">
        <form onSubmit={handleUpload}>
          <DialogHeader>
            <DialogTitle className="text-2xl font-serif font-semibold tracking-tight text-foreground">Upload AI Skill</DialogTitle>
            <DialogDescription className="font-manrope">
              Select one or more ZIP packages containing AI skills.
            </DialogDescription>
          </DialogHeader>

          <div className="grid gap-6 py-6">
            <div className="grid gap-2">
              <Label htmlFor="files">Skill Packages (ZIP)</Label>
              <Input
                id="files"
                type="file"
                accept=".zip"
                multiple
                onChange={(e) => setSelectedFiles(e.target.files)}
                className="cursor-pointer bg-secondary/5 border-dashed border-2 hover:border-primary/50 transition-all py-8 h-auto"
              />
              {selectedFiles && selectedFiles.length > 0 && (
                <p className="text-[11px] text-primary font-medium animate-in fade-in">
                  {selectedFiles.length} file(s) selected
                </p>
              )}
            </div>

            <div className="grid gap-2">
              <Label>Visibility</Label>
              <Select value={scopeType} onValueChange={(v) => {
                setScopeType(v);
                setDeptIds([]);
              }}>
                <SelectTrigger className="bg-secondary/5 h-11">
                  <div className="flex items-center gap-2">
                    <span className="material-symbols-outlined text-base text-muted-foreground">
                      {scopeType === "global" ? "public" : "corporate_fare"}
                    </span>
                    <span className="capitalize">
                      {scopeType === "global" ? "Global" : "Department"}
                    </span>
                  </div>
                </SelectTrigger>
                <SelectContent className="min-w-[240px]">
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
              <div className="grid gap-2 animate-in fade-in slide-in-from-top-1">
                <Label>Target Departments</Label>
                <div className="bg-secondary/5 rounded-xl border border-border p-3">
                  <div className="max-h-[200px] pr-4 overflow-y-auto custom-scrollbar">
                    <div className="grid grid-cols-1 gap-2">
                      {allDepartments.map((d) => (
                        <div key={d.id} className="flex items-center space-x-2 group/item">
                          <Checkbox 
                            id={`upload-dept-${d.id}`} 
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
                            htmlFor={`upload-dept-${d.id}`}
                            className="text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70 cursor-pointer group-hover/item:text-primary transition-colors"
                          >
                            {d.name}
                          </label>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
            
              </div>
            )}


            {conflictFiles.length > 0 && (
              <div className="bg-destructive/5 border border-destructive/20 p-4 rounded-lg flex flex-col gap-2">
                <div className="flex items-center gap-2 text-destructive font-semibold text-sm">
                  <span className="material-symbols-outlined text-lg">warning</span>
                  Duplicate names detected
                </div>
                <p className="text-xs text-muted-foreground">
                  Existing skills: {conflictFiles.join(", ")}. Overwrite them?
                </p>
                <div className="flex items-center gap-2 mt-2">
                  <input
                    type="checkbox"
                    id="force-check"
                    checked={force}
                    onChange={(e) => setForce(e.target.checked)}
                    className="w-4 h-4 cursor-pointer"
                  />
                  <Label htmlFor="force-check" className="text-xs cursor-pointer">I confirm to overwrite</Label>
                </div>
              </div>
            )}
          </div>

          <DialogFooter>
            <Button type="submit" disabled={uploadLoading || !selectedFiles || (conflictFiles.length > 0 && !force)}>
              {uploadLoading ? "Processing..." : "Start Upload"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
