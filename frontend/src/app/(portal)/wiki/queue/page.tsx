"use client";

import React from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { DraftResponse } from "@/types/wiki";
import { PageHeader } from "@/components/shared/page-header";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/shared/empty-state";

type BulkResult = {
  approved: number;
  skipped: number;
  errored: number;
  results: Array<{
    draft_id: string;
    status: "approved" | "skipped" | "error" | string;
    message: string | null;
    page_version: number | null;
  }>;
};

const STATUS_OPTIONS = [
  { value: "pending", label: "Pending" },
  { value: "needs_revision", label: "Needs revision" },
  { value: "approved", label: "Approved" },
  { value: "rejected", label: "Rejected" },
] as const;

function relativeTime(iso: string): string {
  const date = new Date(iso);
  const diff = (Date.now() - date.getTime()) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
  return date.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

type ScopeMode = "review" | "mine";

export default function WikiQueuePage() {
  const [scopeMode, setScopeMode] = React.useState<ScopeMode>("review");
  const [status, setStatus] = React.useState<string>("pending");
  const [drafts, setDrafts] = React.useState<DraftResponse[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [selected, setSelected] = React.useState<Set<string>>(new Set());
  const [busy, setBusy] = React.useState(false);
  const [lastResult, setLastResult] = React.useState<BulkResult | null>(null);

  const load = React.useCallback(() => {
    setLoading(true);
    const qs = new URLSearchParams();
    qs.set("status", status);
    qs.set("limit", "200");
    if (scopeMode === "mine") qs.set("mine", "true");
    api<DraftResponse[]>(`/api/wiki/drafts?${qs.toString()}`)
      .then((rows) => setDrafts(Array.isArray(rows) ? rows : []))
      .catch(() => setDrafts([]))
      .finally(() => setLoading(false));
  }, [status, scopeMode]);

  React.useEffect(() => {
    load();
  }, [load]);

  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleAll = () => {
    if (selected.size === drafts.length) {
      setSelected(new Set());
    } else {
      setSelected(new Set(drafts.map((d) => d.id)));
    }
  };

  const handleBulkApprove = async () => {
    if (selected.size === 0) return;
    const count = selected.size;
    const ok = window.confirm(
      `Approve ${count} draft${count > 1 ? "s" : ""}? This will publish them all immediately.`,
    );
    if (!ok) return;
    setBusy(true);
    setLastResult(null);
    try {
      const result = await api<BulkResult>("/api/wiki/drafts/bulk-approve", {
        method: "POST",
        body: {
          draft_ids: Array.from(selected),
          allow_conflict: false,
        },
      });
      setLastResult(result);
      setSelected(new Set());
      load();
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error("bulk approve failed", err);
    } finally {
      setBusy(false);
    }
  };

  const allChecked = drafts.length > 0 && selected.size === drafts.length;
  const someChecked = selected.size > 0 && selected.size < drafts.length;

  return (
    <>
      <PageHeader
        title="Contributions"
        description={
          scopeMode === "review"
            ? "Drafts waiting for your review across every scope."
            : "Drafts you've authored, across every status."
        }
        action={
          <div className="flex items-center gap-2">
            <Link href="/wiki/review" prefetch={false}>
              <Button variant="outline" size="sm" className="gap-1.5" title="Open review console (3-pane, keyboard shortcuts)">
                <span className="material-symbols-outlined text-base">dashboard</span>
                Open review console
              </Button>
            </Link>
            <select
              value={status}
              onChange={(e) => {
                setStatus(e.target.value);
                setSelected(new Set());
              }}
              className="h-8 rounded-lg border border-input bg-transparent px-2.5 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
            >
              {STATUS_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
            <Button variant="outline" onClick={load} disabled={loading} className="gap-2">
              <span className="material-symbols-outlined text-base">refresh</span>
              Refresh
            </Button>
            <Button
              onClick={handleBulkApprove}
              disabled={busy || selected.size === 0 || status !== "pending" || scopeMode !== "review"}
              className="gap-2"
              title={
                status !== "pending"
                  ? "Bulk approve only works on pending drafts"
                  : selected.size === 0
                  ? "Select drafts first"
                  : `Approve ${selected.size} draft(s)`
              }
            >
              <span className="material-symbols-outlined text-base">
                {busy ? "progress_activity" : "check_circle"}
              </span>
              Approve selected {selected.size > 0 && `(${selected.size})`}
            </Button>
          </div>
        }
      />

      {/* Mode tabs: Mine (drafts I authored) vs To-review (drafts assigned to me) */}
      <div className="mb-4 inline-flex rounded-lg border border-border bg-card p-0.5">
        {([
          { value: "review", label: "To review", icon: "fact_check" },
          { value: "mine", label: "Mine", icon: "edit_note" },
        ] as const).map((opt) => (
          <button
            key={opt.value}
            type="button"
            onClick={() => {
              setScopeMode(opt.value);
              setSelected(new Set());
              setLastResult(null);
            }}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
              scopeMode === opt.value
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:text-foreground hover:bg-muted"
            }`}
          >
            <span className="material-symbols-outlined" style={{ fontSize: 16 }}>
              {opt.icon}
            </span>
            {opt.label}
          </button>
        ))}
      </div>

      {lastResult && (
        <div className="mb-4 rounded-lg border border-border bg-card p-3 text-sm">
          <p className="font-medium">
            Bulk approve complete:{" "}
            <span className="text-emerald-700 dark:text-emerald-300">
              {lastResult.approved} approved
            </span>
            {lastResult.skipped > 0 && (
              <>
                ,{" "}
                <span className="text-muted-foreground">
                  {lastResult.skipped} skipped
                </span>
              </>
            )}
            {lastResult.errored > 0 && (
              <>
                ,{" "}
                <span className="text-destructive">
                  {lastResult.errored} errored
                </span>
              </>
            )}
            .
          </p>
          {lastResult.errored > 0 && (
            <details className="mt-2">
              <summary className="cursor-pointer text-xs text-muted-foreground">
                See errors
              </summary>
              <ul className="mt-1 text-xs space-y-0.5">
                {lastResult.results
                  .filter((r) => r.status === "error")
                  .map((r) => (
                    <li key={r.draft_id} className="text-destructive font-mono">
                      {r.draft_id.slice(0, 8)}… {r.message}
                    </li>
                  ))}
              </ul>
            </details>
          )}
        </div>
      )}

      {loading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : drafts.length === 0 ? (
        <EmptyState
          icon="inbox"
          title="Empty queue"
          description={`No drafts in ${status} state right now.`}
        />
      ) : (
        <div className="rounded-xl border border-border overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-muted/50 text-xs uppercase tracking-wide text-muted-foreground">
              <tr>
                <th className="p-2 w-8">
                  {status === "pending" && scopeMode === "review" && (
                    <input
                      type="checkbox"
                      checked={allChecked}
                      ref={(el) => {
                        if (el) el.indeterminate = someChecked;
                      }}
                      onChange={toggleAll}
                      className="cursor-pointer"
                      aria-label="Select all"
                    />
                  )}
                </th>
                <th className="p-2 text-left">Page</th>
                <th className="p-2 text-left">Author</th>
                <th className="p-2 text-left">AI</th>
                <th className="p-2 text-left">Round</th>
                <th className="p-2 text-left">Note</th>
                <th className="p-2 text-left">Submitted</th>
              </tr>
            </thead>
            <tbody>
              {drafts.map((d) => {
                const checked = selected.has(d.id);
                const aiBadge =
                  d.ai_check_status === "passed"
                    ? "bg-emerald-100 dark:bg-emerald-900/40 text-emerald-700 dark:text-emerald-200"
                    : d.ai_check_status === "warned"
                    ? "bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-200"
                    : d.ai_check_status === "failed"
                    ? "bg-rose-100 dark:bg-rose-900/40 text-rose-700 dark:text-rose-200"
                    : "bg-muted text-muted-foreground";
                // Preserve scope context — the same slug can exist in
                // multiple scopes (e.g. an IT-dept page and a SALES-dept
                // page sharing "ai-llm-ecosystem"); without the params the
                // detail viewer falls back to the first match.
                const scopeQs =
                  d.page_scope_type && d.page_scope_type !== "global"
                    ? `?scopeType=${d.page_scope_type}${
                        d.page_scope_id ? `&scopeId=${d.page_scope_id}` : ""
                      }`
                    : "";
                const pageHref = d.page_slug
                  ? `/wiki/${d.page_slug}${scopeQs}`
                  : null;
                return (
                  <tr
                    key={d.id}
                    className={`border-t border-border ${
                      checked ? "bg-primary/5" : "hover:bg-muted/30"
                    }`}
                  >
                    <td className="p-2 align-top">
                      {status === "pending" && scopeMode === "review" && (
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => toggle(d.id)}
                          className="cursor-pointer mt-1"
                          aria-label={`Select ${d.page_slug}`}
                        />
                      )}
                    </td>
                    <td className="p-2 align-top">
                      <div className="flex items-center gap-1.5">
                        {d.draft_kind === "create" && (
                          <span className="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-blue-100 dark:bg-blue-900/40 text-blue-700 dark:text-blue-200">
                            new
                          </span>
                        )}
                        {pageHref ? (
                          <Link href={pageHref} className="font-medium hover:underline">
                            {d.page_title || d.page_slug}
                          </Link>
                        ) : (
                          <span className="font-medium">{d.page_title || d.page_slug}</span>
                        )}
                      </div>
                      <p className="text-xs text-muted-foreground font-mono mt-0.5">
                        {d.page_slug}
                        {d.page_scope_type && d.page_scope_type !== "global" && (
                          <span className="ml-2 px-1.5 py-0.5 rounded bg-muted text-muted-foreground font-sans">
                            {d.page_scope_type}
                            {d.page_scope_name ? ` · ${d.page_scope_name}` : ""}
                          </span>
                        )}
                      </p>
                    </td>
                    <td className="p-2 align-top">
                      <p className="text-sm">{d.author_name || "Unknown"}</p>
                      {d.author_stats && d.author_stats.total_reviewed > 0 ? (
                        <p
                          className="text-[11px] text-muted-foreground tabular-nums"
                          title={
                            `${d.author_stats.approved} approved · ` +
                            `${d.author_stats.rejected} rejected` +
                            (d.author_stats.needs_revision
                              ? ` · ${d.author_stats.needs_revision} returned`
                              : "")
                          }
                        >
                          {d.author_stats.approved}✓ ·{" "}
                          {Math.round(d.author_stats.accuracy * 100)}%
                        </p>
                      ) : (
                        <p className="text-[11px] text-muted-foreground italic">
                          first contribution
                        </p>
                      )}
                    </td>
                    <td className="p-2 align-top">
                      <span
                        className={`text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded ${aiBadge}`}
                      >
                        {d.ai_check_status}
                      </span>
                      {d.ai_check_results && (
                        <p className="text-[11px] text-muted-foreground tabular-nums mt-0.5">
                          {d.ai_check_results.summary.warn > 0 && (
                            <span>{d.ai_check_results.summary.warn} warn</span>
                          )}
                          {d.ai_check_results.summary.warn > 0 && d.ai_check_results.summary.fail > 0 && " · "}
                          {d.ai_check_results.summary.fail > 0 && (
                            <span className="text-rose-600 dark:text-rose-400">
                              {d.ai_check_results.summary.fail} fail
                            </span>
                          )}
                          {d.ai_check_results.summary.warn === 0 &&
                            d.ai_check_results.summary.fail === 0 && (
                              <span>{d.ai_check_results.summary.pass} pass</span>
                            )}
                        </p>
                      )}
                    </td>
                    <td className="p-2 align-top tabular-nums text-xs">
                      <span className="text-foreground">
                        round {(d.revision_round ?? 0) + 1}
                      </span>
                      {d.last_returned_note && (
                        <p
                          className="text-[11px] text-muted-foreground italic mt-0.5 truncate max-w-[160px]"
                          title={d.last_returned_note}
                        >
                          sent back
                        </p>
                      )}
                    </td>
                    <td className="p-2 align-top text-xs text-muted-foreground max-w-xs">
                      {d.note ? (
                        <p className="line-clamp-2">{d.note}</p>
                      ) : (
                        <p className="italic text-muted-foreground/70">(no note)</p>
                      )}
                      {d.suggested_reviewers && d.suggested_reviewers.length > 0 && scopeMode === "review" && (
                        <p
                          className="text-[11px] text-muted-foreground mt-1 truncate"
                          title={d.suggested_reviewers
                            .map((r) => `${r.name || r.email || "?"} (${r.score})`)
                            .join(", ")}
                        >
                          <span className="font-medium">→</span>{" "}
                          {d.suggested_reviewers
                            .slice(0, 2)
                            .map((r) => r.name || r.email)
                            .filter(Boolean)
                            .join(", ")}
                          {d.suggested_reviewers.length > 2 &&
                            ` +${d.suggested_reviewers.length - 2}`}
                        </p>
                      )}
                    </td>
                    <td className="p-2 align-top text-xs text-muted-foreground whitespace-nowrap">
                      {relativeTime(d.created_at)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
