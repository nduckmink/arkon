"use client";

import { useState } from "react";
import { api } from "@/lib/api";
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
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { EmptyState } from "@/components/shared/empty-state";
import { ScopeDialog } from "@/components/shared/scope-dialog";

type Employee = {
  id: string;
  name: string;
  email: string;
  role: string;
  department_id: string;
  department_name: string;
  is_active: boolean;
  has_token: boolean;
};

type Props = {
  employees: Employee[];
  loading: boolean;
  onEdit: (emp: Employee) => void;
  onRefresh: () => void;
};

export function EmployeeTable({ employees, loading, onEdit, onRefresh }: Props) {
  const [actionError, setActionError] = useState<string | null>(null);
  const [tokenDialog, setTokenDialog] = useState<{ token: string; instructions: string } | null>(null);
  const [scopeEmployee, setScopeEmployee] = useState<Employee | null>(null);

  const handleToggle = async (id: string) => {
    setActionError(null);
    try {
      await api(`/api/employees/${id}/toggle`, { method: "PATCH" });
      onRefresh();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Action failed");
    }
  };

  const handleGenerateToken = async (id: string) => {
    setActionError(null);
    try {
      const data = await api<{ token: string; instructions: string }>(
        `/api/employees/${id}/token`,
        { method: "POST" }
      );
      setTokenDialog(data);
      onRefresh();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Failed to generate token");
    }
  };

  const handleRevokeToken = async (id: string) => {
    if (!confirm("Revoke this employee's MCP token?")) return;
    setActionError(null);
    try {
      await api(`/api/employees/${id}/token`, { method: "DELETE" });
      onRefresh();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Failed to revoke token");
    }
  };

  const handleDelete = async (id: string) => {
    if (!confirm("Delete this employee? This cannot be undone.")) return;
    setActionError(null);
    try {
      await api(`/api/employees/${id}`, { method: "DELETE" });
      onRefresh();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Failed to delete");
    }
  };

  if (loading) {
    return (
      <div className="bg-card rounded-xl border border-border shadow-sahara flex items-center justify-center py-16">
        <span className="material-symbols-outlined text-3xl text-muted-foreground animate-spin">
          progress_activity
        </span>
      </div>
    );
  }

  if (employees.length === 0) {
    return (
      <div className="bg-card rounded-xl border border-border shadow-sahara">
        <EmptyState
          icon="group"
          title="No employees"
          description="Add employees to give them access to the knowledge base"
        />
      </div>
    );
  }

  return (
    <>
      {actionError && (
        <div className="text-sm text-destructive bg-destructive/10 px-4 py-2 rounded-lg flex items-center gap-2">
          <span className="material-symbols-outlined text-base">error</span>
          {actionError}
        </div>
      )}

      <div className="bg-card rounded-xl border border-border shadow-sahara overflow-hidden">
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead className="text-xs uppercase tracking-wider">Employee</TableHead>
              <TableHead className="text-xs uppercase tracking-wider">Role</TableHead>
              <TableHead className="text-xs uppercase tracking-wider">Department</TableHead>
              <TableHead className="text-xs uppercase tracking-wider">Status</TableHead>
              <TableHead className="text-xs uppercase tracking-wider">MCP Token</TableHead>
              <TableHead className="text-xs uppercase tracking-wider text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {employees.map((emp) => (
              <TableRow key={emp.id} className="hover:bg-secondary/30">
                <TableCell>
                  <div className="flex items-center gap-3">
                    <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center text-primary text-xs font-bold shrink-0">
                      {emp.name.charAt(0).toUpperCase()}
                    </div>
                    <div>
                      <p className="text-sm font-medium">{emp.name}</p>
                      <p className="text-xs text-muted-foreground">{emp.email}</p>
                    </div>
                  </div>
                </TableCell>
                <TableCell>
                  <Badge
                    variant={emp.role === "admin" ? "default" : "secondary"}
                    className="text-xs capitalize"
                  >
                    {emp.role}
                  </Badge>
                </TableCell>
                <TableCell className="text-sm">{emp.department_name}</TableCell>
                <TableCell>
                  <Badge
                    variant={emp.is_active ? "outline" : "secondary"}
                    className={`text-xs ${
                      emp.is_active
                        ? "border-green-500 text-green-700"
                        : "text-muted-foreground"
                    }`}
                  >
                    {emp.is_active ? "Active" : "Inactive"}
                  </Badge>
                </TableCell>
                <TableCell>
                  {emp.has_token ? (
                    <span className="text-xs text-green-600 flex items-center gap-1">
                      <span className="material-symbols-outlined text-sm filled">vpn_key</span>
                      Connected
                    </span>
                  ) : (
                    <span className="text-xs text-muted-foreground">No token</span>
                  )}
                </TableCell>
                <TableCell className="text-right">
                  <DropdownMenu>
                    <DropdownMenuTrigger className="inline-flex items-center justify-center h-8 w-8 rounded-md hover:bg-accent hover:text-accent-foreground">
                      <span className="material-symbols-outlined text-base">more_vert</span>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem onClick={() => onEdit(emp)}>
                        <span className="material-symbols-outlined text-base mr-2">edit</span>
                        Edit
                      </DropdownMenuItem>
                      <DropdownMenuItem onClick={() => setScopeEmployee(emp)}>
                        <span className="material-symbols-outlined text-base mr-2">lock</span>
                        Personal Access
                      </DropdownMenuItem>
                      <DropdownMenuItem onClick={() => handleToggle(emp.id)}>
                        <span className="material-symbols-outlined text-base mr-2">
                          {emp.is_active ? "person_off" : "person"}
                        </span>
                        {emp.is_active ? "Deactivate" : "Activate"}
                      </DropdownMenuItem>
                      <DropdownMenuSeparator />
                      {emp.has_token ? (
                        <DropdownMenuItem onClick={() => handleRevokeToken(emp.id)}>
                          <span className="material-symbols-outlined text-base mr-2">vpn_key_off</span>
                          Revoke Token
                        </DropdownMenuItem>
                      ) : (
                        <DropdownMenuItem onClick={() => handleGenerateToken(emp.id)}>
                          <span className="material-symbols-outlined text-base mr-2">vpn_key</span>
                          Generate Token
                        </DropdownMenuItem>
                      )}
                      <DropdownMenuSeparator />
                      <DropdownMenuItem
                        onClick={() => handleDelete(emp.id)}
                        className="text-destructive"
                      >
                        <span className="material-symbols-outlined text-base mr-2">delete</span>
                        Delete
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      {scopeEmployee && (
        <ScopeDialog
          open={!!scopeEmployee}
          onOpenChange={(open) => { if (!open) setScopeEmployee(null); }}
          label={scopeEmployee.name}
          employeeId={scopeEmployee.id}
        />
      )}

      <Dialog open={!!tokenDialog} onOpenChange={() => setTokenDialog(null)}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle className="text-xl flex items-center gap-2">
              <span className="material-symbols-outlined text-primary">vpn_key</span>
              MCP Token Generated
            </DialogTitle>
          </DialogHeader>
          <div className="flex flex-col gap-4 mt-2">
            <p className="text-sm text-muted-foreground">
              Copy this token and share it securely with the employee. It will not be shown again.
            </p>
            <div className="bg-secondary rounded-lg px-4 py-3 font-mono text-sm break-all select-all">
              {tokenDialog?.token}
            </div>
            {tokenDialog?.instructions && (
              <p className="text-sm text-muted-foreground whitespace-pre-line">
                {tokenDialog.instructions}
              </p>
            )}
            <div className="flex justify-end gap-2">
              <Button
                variant="outline"
                onClick={() => {
                  if (tokenDialog?.token) navigator.clipboard.writeText(tokenDialog.token);
                }}
              >
                <span className="material-symbols-outlined text-base mr-1">content_copy</span>
                Copy Token
              </Button>
              <Button
                className="bg-primary text-primary-foreground hover:bg-primary/90"
                onClick={() => setTokenDialog(null)}
              >
                Done
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
