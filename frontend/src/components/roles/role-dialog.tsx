"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

export type PermissionInfo = {
  key: string;
  label: string;
  group: string;
};

export type Role = {
  id: string;
  name: string;
  description?: string;
  permissions: string[];
  is_system: boolean;
};

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  role: Role | null;
  permissions: PermissionInfo[];
  onSaved: () => void;
};

export function RoleDialog({ open, onOpenChange, role, permissions, onSaved }: Props) {
  const isEdit = !!role;
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (role) {
      setName(role.name);
      setDescription(role.description || "");
      setSelected(new Set(role.permissions));
    } else {
      setName("");
      setDescription("");
      setSelected(new Set());
    }
    setError("");
  }, [role, open]);

  const togglePermission = (key: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const toggleGroup = (groupPerms: PermissionInfo[]) => {
    setSelected((prev) => {
      const next = new Set(prev);
      const allSelected = groupPerms.every((p) => next.has(p.key));
      if (allSelected) {
        groupPerms.forEach((p) => next.delete(p.key));
      } else {
        groupPerms.forEach((p) => next.add(p.key));
      }
      return next;
    });
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError("");
    try {
      const body = {
        name: name.trim(),
        description: description.trim() || null,
        permissions: [...selected],
      };
      if (isEdit) {
        await api(`/api/roles/${role.id}`, { method: "PUT", body });
      } else {
        await api("/api/roles", { method: "POST", body });
      }
      onSaved();
      onOpenChange(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  // Group permissions by group name
  const groups = permissions.reduce<Record<string, PermissionInfo[]>>((acc, p) => {
    (acc[p.group] ??= []).push(p);
    return acc;
  }, {});

  const groupEntries = Object.entries(groups);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-4xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="text-xl">
            {isEdit ? "Edit Role" : "Create Role"}
          </DialogTitle>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="flex flex-col gap-5 mt-2">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div className="flex flex-col gap-2">
              <Label htmlFor="role-name">Name</Label>
              <Input
                id="role-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
                disabled={isEdit && role?.is_system}
                className="bg-background"
              />
            </div>

            <div className="flex flex-col gap-2">
              <Label htmlFor="role-desc">Description</Label>
              <Input
                id="role-desc"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Optional"
                className="bg-background"
              />
            </div>
          </div>

          <div className="flex flex-col gap-3">
            <div className="flex items-center justify-between">
              <Label className="text-base">Permissions</Label>
              <span className="text-xs text-muted-foreground">
                {selected.size} of {permissions.length} selected
              </span>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
              {groupEntries.map(([group, perms]) => {
                const allChecked = perms.every((p) => selected.has(p.key));
                const someChecked = perms.some((p) => selected.has(p.key));

                return (
                  <div
                    key={group}
                    className="rounded-lg border border-border bg-card p-4 flex flex-col gap-3"
                  >
                    {/* Group header with select-all */}
                    <label className="flex items-center gap-2.5 cursor-pointer border-b border-border pb-2">
                      <input
                        type="checkbox"
                        checked={allChecked}
                        ref={(el) => {
                          if (el) el.indeterminate = someChecked && !allChecked;
                        }}
                        onChange={() => toggleGroup(perms)}
                        className="h-4 w-4 rounded border-border accent-primary cursor-pointer"
                      />
                      <span className="text-sm font-semibold uppercase tracking-wider text-foreground">
                        {group}
                      </span>
                    </label>

                    {/* Individual permissions */}
                    <div className="flex flex-col gap-2.5">
                      {perms.map((p) => (
                        <label
                          key={p.key}
                          className="flex items-start gap-2.5 cursor-pointer group"
                        >
                          <input
                            type="checkbox"
                            checked={selected.has(p.key)}
                            onChange={() => togglePermission(p.key)}
                            className="mt-0.5 h-4 w-4 rounded border-border accent-primary cursor-pointer"
                          />
                          <div className="min-w-0">
                            <p className="text-sm font-medium leading-none group-hover:text-primary transition-colors">
                              {p.label}
                            </p>
                            <p className="text-[11px] text-muted-foreground mt-0.5 font-mono">
                              {p.key}
                            </p>
                          </div>
                        </label>
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {error && (
            <p className="text-destructive text-sm bg-destructive/10 px-3 py-2 rounded-lg">
              {error}
            </p>
          )}

          <div className="flex justify-end gap-2 mt-2">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={saving}
              className="bg-primary text-primary-foreground hover:bg-primary/90"
            >
              {saving ? "Saving..." : isEdit ? "Update" : "Create"}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
