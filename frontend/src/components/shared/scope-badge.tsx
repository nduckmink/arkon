import { Badge } from "@/components/ui/badge";

export type ScopeType = "global" | "project" | "department" | "team";

type Props = {
  scopeType?: ScopeType | string;
  scopeId?: string;
  className?: string;
};

export function ScopeBadge({ scopeType, scopeId, className }: Props) {
  if (!scopeType) {
    // Legacy documents might not have a scope type, default to global or unknown
    return (
      <Badge variant="outline" className={`text-xs border-muted text-muted-foreground overflow-visible ${className || ""}`}>
        <span className="material-symbols-outlined mr-1 shrink-0" style={{ fontSize: 13, lineHeight: 1 }}>public</span>
        Global
      </Badge>
    );
  }

  switch (scopeType) {
    case "global":
      return (
        <Badge variant="outline" className={`text-[11px] font-medium border-blue-400/30 text-blue-600 bg-blue-50/40 px-2.5 py-0.5 rounded-full overflow-visible whitespace-nowrap ${className || ""}`}>
          <span className="material-symbols-outlined mr-1.5 shrink-0" style={{ fontSize: 14, lineHeight: 1 }}>public</span>
          Global
        </Badge>
      );
    case "department":
      return (
        <Badge variant="outline" className={`text-[11px] font-medium border-indigo-400/30 text-indigo-600 bg-indigo-50/40 px-2.5 py-0.5 rounded-full overflow-visible whitespace-nowrap ${className || ""}`}>
          <span className="material-symbols-outlined mr-1.5 shrink-0" style={{ fontSize: 14, lineHeight: 1 }}>corporate_fare</span>
          Department
        </Badge>
      );
    case "project":
      return (
        <Badge variant="outline" className={`text-[11px] font-medium border-amber-400/30 text-amber-600 bg-amber-50/40 px-2.5 py-0.5 rounded-full overflow-visible whitespace-nowrap ${className || ""}`}>
          <span className="material-symbols-outlined mr-1.5 shrink-0" style={{ fontSize: 14, lineHeight: 1 }}>folder_special</span>
          Workspace
        </Badge>
      );
    case "team":
      return (
        <Badge variant="outline" className={`text-[11px] font-medium border-emerald-400/30 text-emerald-600 bg-emerald-50/40 px-2.5 py-0.5 rounded-full overflow-visible whitespace-nowrap ${className || ""}`}>
          <span className="material-symbols-outlined mr-1.5 shrink-0" style={{ fontSize: 14, lineHeight: 1 }}>group</span>
          Team
        </Badge>
      );
    default:
      return (
        <Badge variant="outline" className={`text-[11px] font-medium border-muted text-muted-foreground px-2.5 py-0.5 rounded-full overflow-visible whitespace-nowrap ${className || ""}`}>
          {scopeType}
        </Badge>
      );
  }
}

