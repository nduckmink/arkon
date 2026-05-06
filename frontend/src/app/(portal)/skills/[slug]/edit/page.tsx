"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { PageHeader } from "@/components/shared/page-header";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

export default function SkillEditPage() {
  const { slug: urlSlug } = useParams();
  const router = useRouter();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const [formData, setFormData] = useState({
    name: "",
    description: "",
  });
  const [originalDescription, setOriginalDescription] = useState("");

  useEffect(() => {
    async function loadData() {
      try {
        setLoading(true);
        const skillData = await api<any>(`/api/skills/${urlSlug}`);

        setFormData({
          name: skillData.name,
          description: skillData.description || "",
        });
        setOriginalDescription(skillData.description || "");
      } catch (error) {
        console.error("Failed to load data:", error);
        alert("Failed to load skill data");
        router.push("/skills");
      } finally {
        setLoading(false);
      }
    }
    if (urlSlug) loadData();
  }, [urlSlug, router]);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    const descriptionChanged = formData.description !== originalDescription;
    let incrementVersion = false;

    if (descriptionChanged) {
      const confirmed = window.confirm(
        "You have modified the documentation (SKILL.md). This will increment the skill version. Proceed?"
      );
      if (!confirmed) return;
      incrementVersion = true;
    }

    try {
      setSaving(true);
      
      const payload: any = {
        name: formData.name,
        description: formData.description,
        increment_version: incrementVersion
      };

      await api(`/api/skills/${urlSlug}`, {
        method: "PATCH",
        body: payload
      });

      router.push(`/skills/${urlSlug}`);
    } catch (error) {
      console.error("Save failed:", error);
      alert("Failed to save skill changes");
    } finally {
      setSaving(false);
    }
  };


  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <span className="material-symbols-outlined text-4xl animate-spin text-primary">progress_activity</span>
      </div>
    );
  }

  return (
    <div className="max-w-6xl mx-auto space-y-8 animate-in fade-in duration-500 pb-20">
      <div className="flex items-center gap-2">
        <button 
          onClick={() => router.push(`/skills/${urlSlug}`)}
          className="flex items-center text-xs font-bold text-muted-foreground hover:text-primary transition-colors uppercase tracking-widest"
        >
          <span className="material-symbols-outlined text-base mr-1">arrow_back</span>
          Back to Details
        </button>
      </div>

      <PageHeader
        title="Edit Skill"
        description="Update information, documentation, and metadata for this skill."
        action={
          <div className="flex items-center gap-2">
            <Button
              type="submit"
              form="skill-edit-form"
              disabled={saving || !formData.name.trim()}
              className="w-32 sm:w-40 shadow-sahara"
            >
              {saving ? "Saving..." : "Save Changes"}
            </Button>
            <Button
              type="button"
              variant="ghost"
              onClick={() => router.push(`/skills/${urlSlug}`)}
              className="text-xs sm:text-sm"
            >
              Cancel
            </Button>
          </div>
        }
      />

      <form id="skill-edit-form" onSubmit={handleSave}>
        <div className="bg-card rounded-xl border border-border p-5 md:p-8 shadow-sm space-y-8">
          <div className="space-y-2">
            <Label htmlFor="name">Skill Name</Label>
            <Input
              id="name"
              value={formData.name}
              onChange={(e) => setFormData({ ...formData, name: e.target.value })}
              placeholder="e.g. Document Analyzer"
              className="h-11"
              required
            />
          </div>

          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label htmlFor="description">Documentation (SKILL.md)</Label>
              <span className="text-[10px] text-muted-foreground font-mono italic">Markdown Supported</span>
            </div>
            <Textarea
              id="description"
              value={formData.description}
              onChange={(e) => setFormData({ ...formData, description: e.target.value })}
              placeholder="Describe what this skill does..."
              className="min-h-[500px] font-mono text-sm leading-relaxed focus:ring-1 focus:ring-primary/20"
            />
          </div>
        </div>
      </form>
    </div>
  );
}
