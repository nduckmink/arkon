"use client";

import { useState, useCallback } from "react";
import { api } from "@/lib/api";
import { PageHeader } from "@/components/shared/page-header";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useEffect } from "react";

type Employee = {
  id: string;
  name: string;
  department_name: string;
};

type PreviewResult = {
  source_id: string;
  source_title: string | null;
  content: string;
  similarity: number;
  page_number: number | null;
  image_urls: string[];
  source_download_url: string | null;
  knowledge_type_name: string | null;
  department_name: string | null;
};

type PreviewResponse = {
  results: PreviewResult[];
  scope_summary: string;
  employee_name: string | null;
};

export default function SearchPreviewPage() {
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [query, setQuery] = useState("");
  const [employeeId, setEmployeeId] = useState<string>("");
  const [topK, setTopK] = useState("10");
  const [loading, setLoading] = useState(false);
  const [response, setResponse] = useState<PreviewResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Employee[]>("/api/employees").then(setEmployees).catch(() => {});
  }, []);

  const handleSearch = useCallback(async () => {
    if (!query.trim()) return;
    setLoading(true);
    setError(null);
    setResponse(null);
    try {
      const data = await api<PreviewResponse>("/api/search/preview", {
        method: "POST",
        body: JSON.stringify({
          query: query.trim(),
          employee_id: employeeId || null,
          top_k: parseInt(topK) || 10,
        }),
      });
      setResponse(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Search failed");
    } finally {
      setLoading(false);
    }
  }, [query, employeeId, topK]);

  return (
    <>
      <PageHeader
        title="Search Preview"
        description="Test what Claude would see when querying the knowledge base for a specific employee."
      />

      <div className="flex flex-col gap-6">
        {/* Controls */}
        <div className="bg-card rounded-xl border border-border shadow-sahara p-6 flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <Label>Search Query</Label>
            <div className="flex gap-2">
              <Input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleSearch()}
                placeholder="e.g. What is our refund policy?"
                className="bg-background flex-1"
              />
              <Button
                onClick={handleSearch}
                disabled={loading || !query.trim()}
                className="bg-primary text-primary-foreground hover:bg-primary/90 shrink-0"
              >
                {loading ? (
                  <span className="material-symbols-outlined animate-spin text-base">progress_activity</span>
                ) : (
                  <span className="material-symbols-outlined text-base">search</span>
                )}
                <span className="ml-1">{loading ? "Searching..." : "Search"}</span>
              </Button>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="flex flex-col gap-1.5">
              <Label>Simulate as employee</Label>
              <Select value={employeeId} onValueChange={(v) => setEmployeeId(v ?? "")}>
                <SelectTrigger className="bg-background">
                  <SelectValue placeholder="Admin view (unscoped)" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="">Admin view (unscoped)</SelectItem>
                  {employees.map((emp) => (
                    <SelectItem key={emp.id} value={emp.id}>
                      <div className="flex flex-col">
                        <span>{emp.name}</span>
                        <span className="text-xs text-muted-foreground">{emp.department_name}</span>
                      </div>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="flex flex-col gap-1.5">
              <Label>Max results</Label>
              <Select value={topK} onValueChange={(v) => { if (v) setTopK(v); }}>
                <SelectTrigger className="bg-background">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {["5", "10", "20"].map((n) => (
                    <SelectItem key={n} value={n}>{n} results</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
        </div>

        {error && (
          <div className="text-sm text-destructive bg-destructive/10 px-4 py-3 rounded-lg flex items-center gap-2">
            <span className="material-symbols-outlined text-base">error</span>
            {error}
          </div>
        )}

        {/* Results */}
        {response && (
          <div className="flex flex-col gap-4">
            {/* Scope banner */}
            <div className={`rounded-lg px-4 py-3 text-sm flex items-start gap-2 ${
              response.scope_summary.includes("Restricted")
                ? "bg-amber-50 text-amber-800 border border-amber-200"
                : "bg-green-50 text-green-800 border border-green-200"
            }`}>
              <span className="material-symbols-outlined text-base mt-0.5 shrink-0">
                {response.scope_summary.includes("Restricted") ? "lock" : "lock_open"}
              </span>
              <div>
                <span className="font-medium">Scope: </span>
                {response.scope_summary}
              </div>
            </div>

            <p className="text-sm text-muted-foreground">
              {response.results.length === 0
                ? "No results found for this query."
                : `${response.results.length} result${response.results.length !== 1 ? "s" : ""} found`}
            </p>

            {response.results.map((result, i) => (
              <div
                key={i}
                className="bg-card rounded-xl border border-border shadow-sahara p-5 flex flex-col gap-3"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="flex flex-col gap-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-sm font-semibold">{result.source_title || "Untitled"}</span>
                      {result.page_number != null && (
                        <span className="text-xs text-muted-foreground">page {result.page_number}</span>
                      )}
                      {result.knowledge_type_name && (
                        <span className="text-xs bg-secondary text-secondary-foreground px-2 py-0.5 rounded-full">
                          {result.knowledge_type_name}
                        </span>
                      )}
                      {result.department_name && (
                        <span className="text-xs bg-secondary text-secondary-foreground px-2 py-0.5 rounded-full">
                          {result.department_name}
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    <div
                      className="text-xs font-medium px-2 py-0.5 rounded-full"
                      style={{
                        backgroundColor: `hsl(${Math.round(result.similarity * 120)}, 60%, 92%)`,
                        color: `hsl(${Math.round(result.similarity * 120)}, 60%, 30%)`,
                      }}
                    >
                      {Math.round(result.similarity * 100)}% match
                    </div>
                    {result.source_download_url && (
                      <a
                        href={result.source_download_url}
                        target="_blank"
                        rel="noreferrer"
                        className="text-muted-foreground hover:text-foreground"
                        title="Download source"
                      >
                        <span className="material-symbols-outlined text-base">download</span>
                      </a>
                    )}
                  </div>
                </div>

                <p className="text-sm text-muted-foreground whitespace-pre-wrap leading-relaxed border-t border-border pt-3">
                  {result.content}
                </p>

                {result.image_urls.length > 0 && (
                  <p className="text-xs text-muted-foreground flex items-center gap-1">
                    <span className="material-symbols-outlined text-sm">image</span>
                    {result.image_urls.length} image(s) in this section
                  </p>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  );
}
