"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { PageHeader } from "@/components/shared/page-header";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/shared/empty-state";
import { Skill } from "@/components/skills/skill-card";
import { SkillTable } from "@/components/skills/skill-table";
import { UploadSkillDialog } from "@/components/skills/upload-skill-dialog";
import { cn } from "@/lib/utils";
import { SkillSidebarFilters } from "@/components/skills/skill-sidebar-filters";
import "./skills.css";

type SkillListResponse = {
  items: Skill[];
  total: number;
};

type Department = {
  id: string;
  name: string;
};

const LIMIT = 2000;

export default function SkillsPage() {
  const router = useRouter();
  const { canAccess } = useAuth();
  const [skills, setSkills] = useState<Skill[]>([]);
  const [total, setTotal] = useState(0);
  const [allDepartments, setAllDepartments] = useState<Department[]>([]);
  const [loading, setLoading] = useState(true);

  // Selection state

  // Filters state
  const [search, setSearch] = useState("");
  const [selectedDepartment, setSelectedDepartment] = useState<string | null>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);

  const loadSkills = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (search) params.set("q", search);
      if (selectedDepartment) params.set("department_id", selectedDepartment);
      params.set("limit", String(LIMIT));

      const data = await api<SkillListResponse>(`/api/skills?${params.toString()}`);
      setSkills(data.items);
      setTotal(data.total);
    } catch (err) {
      console.error("Failed to load skills:", err);
      setSkills([]);
    } finally {
      setLoading(false);
    }
  }, [search, selectedDepartment]);


  const loadAllDepartments = useCallback(async () => {
    try {
      const data = await api<Department[]>("/api/departments");
      setAllDepartments(data);
    } catch {
      setAllDepartments([]);
    }
  }, []);

  // Initial load
  useEffect(() => {
    loadAllDepartments();
  }, [loadAllDepartments]);

  // Load skills when filters change (debounced search)
  useEffect(() => {
    const timer = setTimeout(() => {
      loadSkills();
    }, 200);

    return () => clearTimeout(timer);
  }, [search, selectedDepartment, loadSkills]);

  useEffect(() => {
    const processingIds = skills
      .filter(s => s.status === "processing" || s.status === "deleting")
      .map(s => s.id);

    if (processingIds.length === 0) return;

    const interval = setInterval(() => {
      const params = new URLSearchParams();
      processingIds.forEach(id => params.append("ids", id));
      params.set("limit", "2000"); // Ensure all processing items are returned

      api<SkillListResponse>(`/api/skills?${params.toString()}`)
        .then(data => {
          setSkills(prev => {
            // IDs được trả về từ API (còn tồn tại trong DB)
            const returnedIds = new Set(data.items.map(i => i.id));

            // IDs đang poll nhưng không có trong response → đã bị xóa khỏi DB
            const deletedIds = new Set(processingIds.filter(id => !returnedIds.has(id)));

            // Bắt đầu bằng cách loại bỏ các skill đã xóa
            let updatedItems = deletedIds.size > 0
              ? prev.filter(s => !deletedIds.has(s.id))
              : [...prev];
            let hasChanges = deletedIds.size > 0;

            // Cập nhật skill có trạng thái mới (processing → active, etc.)
            data.items.forEach(newItem => {
              const idx = updatedItems.findIndex(s => s.id === newItem.id);
              if (idx !== -1 && JSON.stringify(updatedItems[idx]) !== JSON.stringify(newItem)) {
                updatedItems[idx] = newItem;
                hasChanges = true;
              }
            });

            // Đồng bộ total khi có skill bị xóa khỏi state
            if (deletedIds.size > 0) {
              setTotal(prev => Math.max(0, prev - deletedIds.size));
            }

            return hasChanges ? updatedItems : prev;
          });
        })
        .catch(err => console.error("Polling error:", err));
    }, 3000);

    return () => clearInterval(interval);
  }, [skills.map(s => s.status).join(",")]);



  const handleDelete = async (id: string, name: string) => {
    if (!confirm(`Are you sure you want to delete Skill "${name}"?`)) return;
    try {
      await api(`/api/skills/${id}`, { method: "DELETE" });
      loadSkills();
    } catch (error) {
      alert("Delete failed: " + (error instanceof Error ? error.message : "Unknown error"));
    }
  };

  const handleSearch = (q: string) => {
    setSearch(q);
  };





  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="AI Skill Library"
        description="Manage and deploy skill packages for your AI system."
        action={
          canAccess("skill", "create") ? (
            <UploadSkillDialog
              allDepartments={allDepartments}
              onUploaded={() => loadSkills()}
            />
          ) : null
        }
      />

          <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
            <div className="lg:col-span-1">
              <SkillSidebarFilters
                departments={allDepartments}
                selectedDepartment={selectedDepartment}
                onSelectDepartment={setSelectedDepartment}
                totalSkills={total}
              />
            </div>

            {/* Main Content Area */}
            <div 
              ref={scrollContainerRef}
              className="lg:col-span-3 flex flex-col gap-6"
            >

              <div className="bg-background/40 rounded-2xl border border-border/50 p-6">
                <div className="flex flex-col gap-4">
                {loading && skills.length === 0 ? (
                  <div className="flex items-center justify-center py-24">
                    <span className="material-symbols-outlined text-3xl text-muted-foreground animate-spin">progress_activity</span>
                  </div>
                ) : (
                  <SkillTable
                    skills={skills}
                    departments={allDepartments}
                    loading={loading}
                    onDelete={handleDelete}
                    onRefresh={loadSkills}
                    onClick={(slug) => router.push(`/skills/${slug}`)}
                    onSearch={handleSearch}
                    total={total}
                    search={search}
                  />
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
  );
}
