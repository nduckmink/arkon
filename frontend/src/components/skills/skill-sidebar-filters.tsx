"use client";

import React, { useState, useMemo } from "react";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

type Department = {
  id: string;
  name: string;
};

type SkillSidebarFiltersProps = {
  departments: Department[];
  selectedDepartment: string | null;
  onSelectDepartment: (id: string | null) => void;
  totalSkills?: number;
};

export function SkillSidebarFilters({
  departments,
  selectedDepartment,
  onSelectDepartment,
}: SkillSidebarFiltersProps) {

  return (
    <div className="w-full flex flex-col gap-5 animate-in fade-in slide-in-from-left-4 duration-700">
      

      {/* 2. Department Filter Card */}
      <div className="bg-card rounded-2xl p-5 border border-border shadow-sahara flex flex-col">
        <div className="flex items-center justify-between mb-4">
          <h4 className="text-sm font-semibold text-foreground tracking-tight flex items-center gap-2">
            <span className="material-symbols-outlined text-primary/70 text-sm">corporate_fare</span>
            Department
          </h4>
        </div>

        <div className="flex flex-col gap-1 max-h-[250px] overflow-y-auto custom-scrollbar -mx-1 px-1">
          <FilterItem
            label="All Departments"
            icon="grid_view"
            active={selectedDepartment === null}
            onClick={() => onSelectDepartment(null)}
          />
          {departments.map((dept) => (
            <FilterItem
              key={dept.id}
              label={dept.name}
              icon="corporate_fare"
              active={selectedDepartment === dept.id}
              onClick={() => onSelectDepartment(selectedDepartment === dept.id ? null : dept.id)}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

// --- Internal Helper Components ---

interface FilterItemProps {
  label: string;
  icon: string;
  active: boolean;
  onClick: () => void;
}

function FilterItem({ label, icon, active, onClick }: FilterItemProps) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "flex items-center gap-2.5 px-3 py-2 rounded-xl text-sm transition-all duration-300 group relative text-left",
        active 
          ? "bg-primary/10 text-primary font-bold shadow-[0_2px_12px_rgba(194,101,42,0.1)]" 
          : "text-muted-foreground hover:bg-secondary/40 hover:text-foreground"
      )}
    >
      <span className={cn(
        "material-symbols-outlined text-sm transition-all duration-300",
        active ? "scale-110" : "text-muted-foreground/30 group-hover:text-primary/40"
      )}>
        {icon}
      </span>
      <span className="truncate flex-1 font-manrope">{label}</span>
    </button>
  );
}
