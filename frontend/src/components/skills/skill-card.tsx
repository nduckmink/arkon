"use client";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

export type Skill = {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  department_ids?: string[];
  department_names?: string[];
  current_version: number;
  version_hash?: string | null;
  status: string;
  scope_type: string;
  scope_id: string | null;
  updated_at: string;
};

type SkillCardProps = {
  skill: Skill;
  onDelete: (id: string, name: string) => void;
  onEdit?: (id: string) => void;
  onClick?: (id: string) => void;
};

export function SkillCard({
  skill,
  onDelete,
  onEdit,
  onClick
}: SkillCardProps) {
  const { hasPermission } = useAuth();
  const dateStr = (() => {
    const d = new Date(skill.updated_at);
    return `${String(d.getDate()).padStart(2, '0')}/${String(d.getMonth() + 1).padStart(2, '0')}/${d.getFullYear()}`;
  })();

  return (
    <div
      onClick={() => skill.status !== "deleting" && onClick?.(skill.slug)}
      className={cn(
        "bg-card rounded-xl p-5 border transition-all flex flex-col group animate-in fade-in slide-in-from-bottom-2 duration-300 relative",
        skill.status === "deleting" ? "cursor-not-allowed opacity-80" : "cursor-pointer",
        "border-border shadow-sahara hover:border-primary/30"
      )}
    >

      <div className="flex items-start justify-between mb-3 pr-6">
        <div className="flex items-center gap-2.5">
          <div className="w-6 h-6 rounded-md bg-secondary/40 flex items-center justify-center shrink-0 transition-colors group-hover:bg-secondary/60">
            <span className="material-symbols-outlined text-muted-foreground/40 text-[16px] group-hover:scale-110 transition-transform duration-300">auto_awesome</span>
          </div>
          <div>
            <h3
              className={cn(
                "text-sm font-medium text-foreground transition-colors line-clamp-1 font-manrope",
                skill.status !== "deleting" && "group-hover:text-primary"
              )}
              onClick={(e) => {
                e.stopPropagation();
                if (skill.status !== "deleting") onClick?.(skill.slug);
              }}
            >
              {skill.name}
            </h3>
            <div className="flex flex-wrap items-center gap-2 mt-1">
              <Badge variant="secondary" className="text-[10px] px-1.5 py-0 font-bold bg-secondary/50">v{skill.current_version}</Badge>
              {skill.department_names && skill.department_names.length > 0 ? (
                skill.department_names.map((dept, idx) => (
                  <div key={idx} className="flex items-center gap-1 text-[10px] text-muted-foreground font-semibold uppercase tracking-wider">
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
              <span className={cn(
                "text-[10px] uppercase font-bold",
                skill.status === "active" ? "text-green-500" :
                  skill.status === "deleting" ? "text-destructive animate-pulse" :
                    "text-yellow-500"
              )}>{skill.status}</span>
            </div>
          </div>
        </div>
      </div>

      <div className="pt-3 border-t border-border mt-auto flex items-center justify-between">
        <span className="text-[10px] text-muted-foreground">
          {dateStr}
        </span>

        <div className="flex gap-0.5 flex-nowrap" onClick={(e) => e.stopPropagation()}>
          <Button
            variant="ghost"
            size="sm"
            className="h-7 text-[11px] px-1.5 hover:text-primary whitespace-nowrap"
            onClick={() => onClick?.(skill.slug)}
            disabled={skill.status === "deleting"}
          >
            Details
          </Button>
          {hasPermission("skills.edit") && (
            <Button
              variant="ghost"
              size="sm"
              className="h-7 text-[11px] px-1.5 hover:text-primary whitespace-nowrap"
              onClick={() => onEdit?.(skill.slug)}
              disabled={skill.status === "deleting"}
            >
              Edit
            </Button>
          )}
          {hasPermission("skills.delete") && (
            <Button
              variant="ghost"
              size="sm"
              className="h-7 text-[11px] px-1.5 text-destructive hover:text-destructive hover:bg-destructive/10 whitespace-nowrap"
              onClick={() => onDelete(skill.id, skill.name)}
              disabled={skill.status === "deleting"}
            >
              Delete
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
