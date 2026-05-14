"use client";

import React from "react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { Source } from "./types";

type PlanPage = {
  action: "CREATE" | "UPDATE";
  slug: string;
  title: string;
  page_type: string;
  entity_names?: string[];
  priority?: number;
  related_kb_pages?: string[];
};

type PlanData = {
  pages: PlanPage[];
  strategy?: string;
  compilation_notes?: string;
  estimated_page_count?: number;
  source_page_slug?: string;
};

type PlanResponse = {
  id: string;
  status: string;
  plan: PlanData;
  review_note: string | null;
};

export function PlanReviewDialog({
  source,
  onClose,
  onDone,
}: {
  source: Source;
  onClose: () => void;
  onDone: () => void;
}) {
  const [plan, setPlan] = React.useState<PlanData | null>(null);
  const [planStatus, setPlanStatus] = React.useState<string>("pending_review");
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [submitting, setSubmitting] = React.useState<"approve" | "reject" | "regenerate" | null>(null);
  const [reviewNote, setReviewNote] = React.useState("");
  const [confirmReject, setConfirmReject] = React.useState(false);

  const loadPlan = React.useCallback(async () => {
    try {
      const res = await api<PlanResponse>(`/api/sources/${source.id}/plan`);
      setPlan(res.plan);
      setPlanStatus(res.status);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load plan");
    } finally {
      setLoading(false);
    }
  }, [source.id]);

  React.useEffect(() => {
    setLoading(true);
    void loadPlan();
  }, [loadPlan]);

  // Poll while the plan is being regenerated in the background.
  React.useEffect(() => {
    if (planStatus !== "regenerating") return;
    const id = setInterval(() => {
      void loadPlan();
    }, 3000);
    return () => clearInterval(id);
  }, [planStatus, loadPlan]);

  const isRegenerating = planStatus === "regenerating" || submitting === "regenerate";

  const handleApprove = async () => {
    setSubmitting("approve");
    setError(null);
    try {
      await api(`/api/sources/${source.id}/plan/approve`, {
        method: "POST",
        body: { note: reviewNote || "Approved via UI" },
      });
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to approve plan");
      setSubmitting(null);
    }
  };

  const handleReject = async () => {
    if (!confirmReject) {
      setConfirmReject(true);
      return;
    }
    setSubmitting("reject");
    setError(null);
    try {
      await api(`/api/sources/${source.id}/plan/reject`, {
        method: "POST",
        body: { note: reviewNote || "Rejected via UI" },
      });
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to reject plan");
      setSubmitting(null);
    }
  };

  const handleRegenerate = async () => {
    if (!reviewNote.trim()) {
      setError("Please describe what should be changed before regenerating.");
      return;
    }
    setSubmitting("regenerate");
    setError(null);
    try {
      await api(`/api/sources/${source.id}/plan/regenerate`, {
        method: "POST",
        body: { note: reviewNote },
      });
      // Background task — flip local status so the polling effect kicks in.
      setPlanStatus("regenerating");
      setReviewNote("");
      setConfirmReject(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to regenerate plan");
    } finally {
      setSubmitting(null);
    }
  };

  const pages = plan?.pages ?? [];
  const creates = pages.filter((p) => p.action === "CREATE");
  const updates = pages.filter((p) => p.action === "UPDATE");

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-2xl max-h-[85vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <span className="material-symbols-outlined text-blue-500" style={{ fontSize: 20 }}>
              fact_check
            </span>
            Review Compilation Plan
          </DialogTitle>
          <p className="text-sm text-muted-foreground mt-1">
            {source.title} — approve to start writing wiki pages, or reject to stop.
          </p>
        </DialogHeader>

        <div className="flex-1 overflow-y-auto min-h-0 mt-2">
          {loading && (
            <div className="flex items-center justify-center py-12">
              <span className="material-symbols-outlined animate-spin text-muted-foreground text-3xl">
                progress_activity
              </span>
            </div>
          )}

          {error && (
            <div className="text-sm text-destructive bg-destructive/10 px-4 py-2 rounded-lg mb-4">
              {error}
            </div>
          )}

          {isRegenerating && (
            <div className="flex items-center gap-2 text-sm bg-blue-500/10 text-blue-700 dark:text-blue-300 border border-blue-500/30 px-3 py-2 rounded-lg mb-4">
              <span className="material-symbols-outlined animate-spin" style={{ fontSize: 18 }}>
                progress_activity
              </span>
              Regenerating plan with your feedback… this can take 30–90 seconds.
            </div>
          )}

          {plan && (
            <div className={`flex flex-col gap-4 ${isRegenerating ? "opacity-50 pointer-events-none" : ""}`}>
              {/* Summary row */}
              <div className="flex items-center gap-4 text-sm text-muted-foreground flex-wrap">
                <span className="flex items-center gap-1.5">
                  <span className="w-2 h-2 rounded-full bg-green-500" />
                  {creates.length} page{creates.length !== 1 ? "s" : ""} to create
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="w-2 h-2 rounded-full bg-yellow-500" />
                  {updates.length} page{updates.length !== 1 ? "s" : ""} to update
                </span>
                {plan.strategy && (
                  <span className="flex items-center gap-1.5">
                    <span className="material-symbols-outlined text-sm">analytics</span>
                    {plan.strategy}
                  </span>
                )}
              </div>

              {/* Planner notes */}
              {plan.compilation_notes && (
                <div className="text-xs text-muted-foreground bg-secondary/40 rounded-lg px-3 py-2 border border-border">
                  <span className="font-medium text-foreground">Planner note: </span>
                  {plan.compilation_notes}
                </div>
              )}

              {/* Page list */}
              {[...creates, ...updates]
                .sort((a, b) => (a.priority ?? 99) - (b.priority ?? 99))
                .map((page) => (
                  <div
                    key={page.slug}
                    className="flex items-start gap-3 p-3 rounded-lg border border-border bg-card"
                  >
                    <Badge
                      variant="outline"
                      className={`shrink-0 text-[10px] font-medium h-5 px-1.5 ${
                        page.action === "CREATE"
                          ? "border-green-500/50 text-green-600"
                          : "border-yellow-500/50 text-yellow-600"
                      }`}
                    >
                      {page.action}
                    </Badge>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-sm font-medium">{page.title}</span>
                        <span className="text-[10px] text-muted-foreground font-mono">
                          {page.slug}
                        </span>
                        <Badge variant="secondary" className="text-[10px] h-4 px-1.5">
                          {page.page_type}
                        </Badge>
                      </div>
                      {page.entity_names && page.entity_names.length > 0 && (
                        <p className="text-[11px] text-muted-foreground mt-1 truncate">
                          {page.entity_names.slice(0, 5).join(", ")}
                          {page.entity_names.length > 5 && ` +${page.entity_names.length - 5} more`}
                        </p>
                      )}
                    </div>
                  </div>
                ))}
            </div>
          )}
        </div>

        <div className="mt-4 flex flex-col gap-2 shrink-0">
          <textarea
            value={reviewNote}
            onChange={(e) => setReviewNote(e.target.value)}
            placeholder="Feedback or correction for the AI (e.g. 'Page X already exists, it should be UPDATE not CREATE'). Required to regenerate."
            disabled={isRegenerating}
            className="w-full text-sm rounded-lg border border-border bg-background px-3 py-2 resize-none h-16 focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary/50 disabled:opacity-60"
          />
          <p className="text-[11px] text-muted-foreground">
            Write feedback then click <strong>Regenerate</strong> to have AI redo the plan, or click <strong>Approve</strong> to proceed as-is.
          </p>
        </div>

        {isRegenerating && (
          <div className="mt-2 flex items-center gap-2 text-sm text-blue-600 bg-blue-500/10 rounded-lg px-3 py-2 shrink-0">
            <span className="material-symbols-outlined animate-spin" style={{ fontSize: 18 }}>
              progress_activity
            </span>
            Regenerating plan with your feedback — this can take up to a minute.
          </div>
        )}

        <div className="flex items-center justify-between mt-4 pt-4 border-t border-border shrink-0">
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={handleRegenerate}
              disabled={loading || submitting !== null || isRegenerating}
              className="gap-1.5"
            >
              {isRegenerating ? (
                <span className="material-symbols-outlined animate-spin" style={{ fontSize: 15 }}>
                  progress_activity
                </span>
              ) : (
                <span className="material-symbols-outlined" style={{ fontSize: 15 }}>refresh</span>
              )}
              {isRegenerating ? "Regenerating..." : "Regenerate"}
            </Button>
          </div>

          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              onClick={onClose}
              disabled={submitting !== null}
            >
              Cancel
            </Button>
            {!confirmReject ? (
              <Button
                variant="outline"
                onClick={() => setConfirmReject(true)}
                disabled={loading || submitting !== null || isRegenerating}
                className="text-destructive border-destructive/30 hover:bg-destructive/10"
              >
                <span className="material-symbols-outlined" style={{ fontSize: 16 }}>close</span>
                Reject
              </Button>
            ) : (
              <Button
                variant="destructive"
                onClick={handleReject}
                disabled={submitting !== null}
              >
                {submitting === "reject" ? (
                  <span className="material-symbols-outlined animate-spin" style={{ fontSize: 16 }}>
                    progress_activity
                  </span>
                ) : (
                  <span className="material-symbols-outlined" style={{ fontSize: 16 }}>close</span>
                )}
                Confirm Reject
              </Button>
            )}
            <Button
              onClick={handleApprove}
              disabled={loading || submitting !== null || isRegenerating}
            >
              {submitting === "approve" ? (
                <span className="material-symbols-outlined animate-spin" style={{ fontSize: 16 }}>
                  progress_activity
                </span>
              ) : (
                <span className="material-symbols-outlined" style={{ fontSize: 16 }}>check</span>
              )}
              Approve & Compile
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
