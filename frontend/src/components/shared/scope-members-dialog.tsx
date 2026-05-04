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
import { EmptyState } from "@/components/shared/empty-state";

type Member = {
  employee_id: string;
  employee_name: string;
  employee_email: string;
  role: string;
};

type Employee = {
  id: string;
  name: string;
  email: string;
};

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  label: string; // e.g. "Sales Department" or "Alpha Project"
  scopeType: "global" | "department" | "project";
  scopeId: string;
};

export function ScopeMembersDialog({ open, onOpenChange, label, scopeType, scopeId }: Props) {
  const [members, setMembers] = useState<Member[]>([]);
  const [allEmployees, setAllEmployees] = useState<Employee[]>([]);
  const [selectedEmpId, setSelectedEmpId] = useState("");
  const [selectedRole, setSelectedRole] = useState("reader");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const loadMembers = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api<Member[]>(`/api/scopes/${scopeType}/${scopeId}/members`);
      setMembers(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load members");
      setMembers([]);
    } finally {
      setLoading(false);
    }
  }, [scopeType, scopeId]);

  useEffect(() => {
    if (!open) return;
    setError("");
    loadMembers();
    api<Employee[]>("/api/employees")
      .then((emps) => setAllEmployees(emps))
      .catch(() => {});
  }, [open, loadMembers]);

  const handleAdd = async () => {
    if (!selectedEmpId) return;
    setSaving(true);
    setError("");
    try {
      await api(`/api/scopes/${scopeType}/${scopeId}/members`, {
        method: "POST",
        body: {
          employee_id: selectedEmpId,
          role: selectedRole,
        },
      });
      setSelectedEmpId("");
      await loadMembers();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add member");
    } finally {
      setSaving(false);
    }
  };

  const handleRemove = async (empId: string) => {
    if (!confirm("Remove this member's access?")) return;
    setError("");
    try {
      await api(`/api/scopes/${scopeType}/${scopeId}/members/${empId}`, { method: "DELETE" });
      await loadMembers();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to remove member");
    }
  };

  const memberIds = new Set(members.map((m) => m.employee_id));
  const availableEmployees = allEmployees.filter((e) => !memberIds.has(e.id));

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle className="text-xl">Access Control — {label}</DialogTitle>
        </DialogHeader>

        <div className="flex flex-col gap-5 mt-1">
          {/* Explanation */}
          <div className="rounded-lg px-4 py-3 text-sm flex items-start gap-2 bg-blue-50 text-blue-800 border border-blue-200">
            <span className="material-symbols-outlined text-base mt-0.5 shrink-0">
              shield_person
            </span>
            <div className="flex flex-col">
              <span className="font-medium">
                {scopeType === "department" ? "Realm 1: Org Knowledge" : "Realm 2: Workspace"}
              </span>
              <span>
                Assign roles to employees to grant them access to this scope. 
                Members will be able to read or maintain documents according to their role.
              </span>
            </div>
          </div>

          {error && (
            <p className="text-destructive text-sm bg-destructive/10 px-3 py-2 rounded-lg">{error}</p>
          )}

          {/* Add member form */}
          <div className="flex gap-2 items-center">
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

            <Select value={selectedRole} onValueChange={(v) => setSelectedRole(v ?? "reader")}>
              <SelectTrigger className="bg-background w-32">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="reader">Reader</SelectItem>
                <SelectItem value="contributor">Contributor</SelectItem>
                <SelectItem value="owner">Owner</SelectItem>
                <SelectItem value="admin">Admin</SelectItem>
              </SelectContent>
            </Select>

            <Button
              disabled={saving || !selectedEmpId}
              onClick={handleAdd}
              className="bg-primary text-primary-foreground shrink-0"
            >
              {saving ? <span className="material-symbols-outlined animate-spin text-sm">progress_activity</span> : "Add"}
            </Button>
          </div>

          {/* Member list */}
          <div className="border border-border rounded-xl bg-card overflow-hidden">
            <div className="bg-muted/50 px-4 py-2 border-b border-border">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Current Members ({members.length})</h3>
            </div>
            
            {loading ? (
              <div className="flex justify-center py-8">
                <span className="material-symbols-outlined animate-spin text-muted-foreground">progress_activity</span>
              </div>
            ) : members.length === 0 ? (
              <div className="p-4">
                <EmptyState icon="group_off" title="No members" description="This scope has no specific members assigned yet." />
              </div>
            ) : (
              <div className="flex flex-col divide-y divide-border max-h-60 overflow-y-auto">
                {members.map((m) => (
                  <div key={m.employee_id} className="flex items-center justify-between px-4 py-3 hover:bg-secondary/20">
                    <div className="flex flex-col">
                      <span className="text-sm font-medium">{m.employee_name}</span>
                      <span className="text-xs text-muted-foreground">{m.employee_email}</span>
                    </div>
                    <div className="flex items-center gap-3">
                      <Badge variant="outline" className={`text-xs capitalize ${
                        m.role === 'admin' || m.role === 'owner' ? 'border-primary text-primary' : ''
                      }`}>
                        {m.role}
                      </Badge>
                      <button
                        onClick={() => handleRemove(m.employee_id)}
                        className="text-muted-foreground hover:text-destructive transition-colors shrink-0"
                        title="Remove access"
                      >
                        <span className="material-symbols-outlined text-base">close</span>
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
