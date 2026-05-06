"use client";

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { SkillFileExplorer } from "./skill-file-explorer";

type SkillDetailDialogProps = {
  skillId: string;
  skillName: string;
  onClose: () => void;
};

export function SkillDetailDialog({
  skillId,
  skillName,
  onClose,
}: SkillDetailDialogProps) {
  return (
    <Dialog open onOpenChange={onClose}>
      <DialogContent className="max-w-6xl h-[85vh] p-0 gap-0 overflow-hidden rounded-2xl border-border shadow-2xl flex flex-col">
        <DialogHeader className="p-4 border-b border-border shrink-0 bg-secondary/10">
          <div className="flex items-center justify-between pr-8">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-xl bg-primary/10 flex items-center justify-center">
                <span className="material-symbols-outlined text-primary">terminal</span>
              </div>
              <div>
                <DialogTitle className="text-xl font-serif">{skillName}</DialogTitle>
                <p className="text-xs text-muted-foreground font-manrope">Package Explorer</p>
              </div>
            </div>
            <Badge variant="outline" className="font-mono text-[10px] uppercase tracking-tighter bg-muted/50">
              {skillId.slice(0, 8)}
            </Badge>
          </div>
        </DialogHeader>

        <div className="flex-1 overflow-hidden p-4 bg-muted/5">
          <SkillFileExplorer skillId={skillId} />
        </div>
      </DialogContent>
    </Dialog>
  );
}
