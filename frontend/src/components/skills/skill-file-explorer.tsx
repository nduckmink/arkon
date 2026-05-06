"use client";

import { useState, useEffect, useCallback, useMemo } from "react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

type SkillFile = {
  path: string;
  size: number;
  last_modified: string;
  is_text: boolean;
};

type TreeNode = {
  name: string;
  path: string;
  type: "file" | "folder";
  children?: TreeNode[];
  is_text?: boolean;
};

type SkillFileExplorerProps = {
  skillId: string;
  version?: number | null;
};

export function SkillFileExplorer({ skillId, version }: SkillFileExplorerProps) {
  const [files, setFiles] = useState<SkillFile[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [content, setContent] = useState<string | null>(null);
  const [contentLoading, setContentLoading] = useState(false);
  const [expandedFolders, setExpandedFolders] = useState<Set<string>>(new Set([""]));
  const [loadedVersion, setLoadedVersion] = useState<number | null | undefined>(undefined);

  const buildTree = (files: SkillFile[]): TreeNode[] => {
    const root: TreeNode[] = [];
    
    files.forEach((file) => {
      const parts = file.path.split("/").filter(p => p !== "");
      let currentLevel = root;
      let currentPath = "";

      parts.forEach((part, index) => {
        currentPath = currentPath ? `${currentPath}/${part}` : part;
        const isLast = index === parts.length - 1;

        let node = currentLevel.find((n) => n.name === part && n.type === (isLast ? "file" : "folder"));
        if (!node) {
          node = {
            name: part,
            path: currentPath,
            type: isLast ? "file" : "folder",
            is_text: isLast ? file.is_text : undefined,
            children: isLast ? undefined : [],
          };
          currentLevel.push(node);
        }
        if (!isLast && node.children) {
          currentLevel = node.children;
        }
      });
    });

    // Sort: Folders first, then files
    const sortNodes = (nodes: TreeNode[]) => {
      nodes.sort((a, b) => {
        if (a.type !== b.type) return a.type === "folder" ? -1 : 1;
        return a.name.localeCompare(b.name);
      });
      nodes.forEach((n) => {
        if (n.children) sortNodes(n.children);
      });
    };

    sortNodes(root);
    return root;
  };

  const fileTree = useMemo(() => buildTree(files), [files]);

  const loadFiles = useCallback(async () => {
    setLoading(true);
    try {
      const query = version ? `?version=${version}` : "";
      const apiUrl = `/api/skills/${skillId}/files${query}`;
      console.log(`[FileExplorer] Fetching files from: ${apiUrl}`);
      const data = await api<SkillFile[]>(apiUrl);
      console.log(`[FileExplorer] [DEBUG] setFiles for version: ${version || 'latest'}`);
      setFiles(data);
      setLoadedVersion(version);
      
      // Auto-select readme or first text file
      const readme = data.find(f => f.path.toLowerCase().endsWith("skill.md"));
      if (readme) {
        setSelectedPath(readme.path);
      } else if (data.length > 0) {
        const firstText = data.find(f => f.is_text);
        if (firstText) setSelectedPath(firstText.path);
        else setSelectedPath(null);
      } else {
        setSelectedPath(null);
      }
    } catch (err) {
      console.error("[FileExplorer] Failed to load skill files:", err);
    } finally {
      setLoading(false);
    }
  }, [skillId, version]);

  const loadContent = useCallback(async (path: string) => {
    setContentLoading(true);
    try {
      const query = version ? `&version=${version}` : "";
      const apiUrl = `/api/skills/${skillId}/files/content?path=${encodeURIComponent(path)}${query}`;
      console.log(`[FileExplorer] [DEBUG] fetchContent: path=${path}, version=${version || 'latest'}, url=${apiUrl}`);
      const data = await api<{ content: string }>(apiUrl);
      
      // Clean up internal markers and YAML frontmatter
      let cleanedContent = data.content
        .replace(/^---[\s\S]*?---\n?/, "") // Remove YAML frontmatter
        .replace(/--[a-z0-9]{4}--/gi, "")   // Remove internal markers
        .trim();
        
      setContent(cleanedContent);
    } catch (err) {
      console.error("[FileExplorer] Failed to load file content:", err);
      setContent("Error loading file content.");
    } finally {
      setContentLoading(false);
    }
  }, [skillId, version]);

  useEffect(() => {
    console.log(`[FileExplorer] [DEBUG] version changed to: ${version || 'latest'}. Clearing state.`);
    setFiles([]);
    setLoadedVersion(undefined); // Mark as not matching any version yet
    setContent(null);
    setSelectedPath(null);
    loadFiles();
  }, [skillId, version, loadFiles]);

  useEffect(() => {
    // Only load content if:
    // 1. We have a selected path
    // 2. We have files loaded
    // 3. The loaded files version matches the current viewing version
    // 4. We are not in a loading state
    const versionMatches = loadedVersion === version;
    
    if (selectedPath && files.length > 0 && versionMatches && !loading) {
      const file = files.find(f => f.path === selectedPath);
      if (file?.is_text) {
        console.log(`[FileExplorer] [DEBUG] useEffect trigger loadContent: ${selectedPath} (version match: ${version || 'latest'})`);
        loadContent(selectedPath);
      } else {
        setContent(null);
      }
    }
  }, [selectedPath, files, loadContent, loading, loadedVersion, version]);

  const toggleFolder = (path: string) => {
    setExpandedFolders((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  const renderTree = (nodes: TreeNode[], level = 0) => {
    return nodes.map((node) => {
      const isExpanded = expandedFolders.has(node.path);
      const isSelected = selectedPath === node.path;

      if (node.type === "folder") {
        return (
          <div key={node.path} className="flex flex-col">
            <button
              onClick={() => toggleFolder(node.path)}
              className="flex items-center gap-1.5 px-2 py-1.5 hover:bg-muted/50 rounded-md text-[13px] transition-colors text-muted-foreground hover:text-foreground group"
              style={{ paddingLeft: `${level * 16 + 8}px` }}
            >
              <span className={cn(
                "material-symbols-outlined text-[18px] transition-transform duration-200",
                isExpanded && "rotate-90"
              )}>
                chevron_right
              </span>
              <span className="material-symbols-outlined text-[20px] text-amber-500/80 group-hover:text-amber-500">
                {isExpanded ? "folder_open" : "folder"}
              </span>
              <span className="truncate font-medium">{node.name}</span>
            </button>
            {isExpanded && node.children && (
              <div className="flex flex-col">
                {renderTree(node.children, level + 1)}
              </div>
            )}
          </div>
        );
      }

      return (
        <button
          key={node.path}
          onClick={() => setSelectedPath(node.path)}
          className={cn(
            "flex items-center gap-2 px-2 py-1.5 rounded-md text-[13px] transition-all text-left group",
            isSelected 
              ? "bg-primary/10 text-primary font-semibold shadow-[inset_2px_0_0_0_currentColor]" 
              : "hover:bg-muted/30 text-muted-foreground hover:text-foreground"
          )}
          style={{ paddingLeft: `${level * 16 + 32}px` }}
        >
          <span className={cn(
            "material-symbols-outlined text-[20px] shrink-0",
            isSelected ? "text-primary" : "text-muted-foreground/60 group-hover:text-primary/70"
          )}>
            {node.name.toLowerCase().endsWith(".md") ? "article" : node.is_text ? "description" : "draft"}
          </span>
          <span className="truncate flex-1">{node.name}</span>
        </button>
      );
    });
  };

  const formatSize = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  return (
    <div className="bg-card rounded-2xl border border-border overflow-hidden shadow-sahara flex flex-col lg:flex-row h-[750px]">
      {/* Sidebar - File Tree */}
      <div className="w-full lg:w-80 border-b lg:border-b-0 lg:border-r border-border bg-secondary/5 flex flex-col overflow-hidden shrink-0">
        <div className="px-5 py-4 border-b border-border bg-secondary/10 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="material-symbols-outlined text-sm text-primary/70">account_tree</span>
            <h4 className="text-[11px] font-bold uppercase tracking-widest text-muted-foreground">Skill Explorer</h4>
          </div>
          <span className="text-[10px] tabular-nums font-bold text-primary bg-primary/10 px-1.5 py-0.5 rounded-full">{files.length}</span>
        </div>
        <div className="flex-1 overflow-y-auto p-3 custom-scrollbar">
          {loading ? (
            <div className="space-y-3 p-2">
              {[1, 2, 3, 4, 5, 6, 7, 8].map(i => (
                <div key={i} className="h-8 w-full bg-muted/40 animate-pulse rounded-lg" />
              ))}
            </div>
          ) : files.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-40 opacity-40 text-center px-4">
              <span className="material-symbols-outlined text-4xl mb-2">folder_open</span>
              <p className="text-xs font-medium">No files available in this package.</p>
            </div>
          ) : (
            renderTree(fileTree)
          )}
        </div>
      </div>

      {/* Main Area - Preview */}
      <div className="flex-1 flex flex-col bg-background overflow-hidden">
        {selectedPath ? (
          <>
            <div className="px-6 py-4 border-b border-border bg-muted/5 flex items-center justify-between shrink-0">
              <div className="flex items-center gap-3 min-w-0">
                <span className="text-xs font-mono text-foreground font-semibold truncate bg-secondary/30 px-2 py-1 rounded-md border border-border/50">
                  {selectedPath}
                </span>
              </div>
              <div className="flex items-center gap-3">
                {content && (
                  <Button 
                    variant="outline" 
                    size="sm" 
                    className="h-8 text-[11px] gap-2 px-3 hover:bg-primary/5 hover:text-primary transition-all font-bold uppercase tracking-widest border-primary/20"
                    onClick={() => navigator.clipboard.writeText(content)}
                  >
                    <span className="material-symbols-outlined text-xs">content_copy</span>
                    Copy
                  </Button>
                )}
                <div className="flex flex-col items-end">
                  <span className="text-[10px] text-muted-foreground font-mono leading-none">
                    {formatSize(files.find(f => f.path === selectedPath)?.size || 0)}
                  </span>
                </div>
              </div>
            </div>
            <div className={cn(
              "flex-1 overflow-y-auto custom-scrollbar",
              selectedPath?.toLowerCase().endsWith(".md") ? "bg-card/30" : "bg-secondary/5"
            )}>
              {contentLoading ? (
                <div className="flex flex-col items-center justify-center h-full gap-4">
                  <span className="material-symbols-outlined animate-spin text-primary/30 text-5xl">progress_activity</span>
                  <p className="text-[11px] font-bold text-muted-foreground uppercase tracking-widest">Loading content...</p>
                </div>
              ) : content ? (
                selectedPath.toLowerCase().endsWith(".md") ? (
                  <div className="p-10 md:p-16 max-w-4xl mx-auto bg-card rounded-2xl my-8 border border-border/50 shadow-sm">
                    <div className="prose prose-sm dark:prose-invert max-w-none markdown-content">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>
                        {content}
                      </ReactMarkdown>
                    </div>
                  </div>
                ) : (
                  <pre className="w-full text-[13px] font-mono p-8 selection:bg-primary/20 whitespace-pre leading-relaxed font-manrope text-foreground/90 border-none rounded-none shadow-none m-0">
                    {content}
                  </pre>
                )
              ) : (
                <div className="flex flex-col items-center justify-center h-full gap-5 opacity-40 bg-muted/5">
                  <div className="w-20 h-20 rounded-full bg-muted/50 flex items-center justify-center">
                    <span className="material-symbols-outlined text-5xl">visibility_off</span>
                  </div>
                  <div className="text-center">
                    <p className="text-sm font-bold uppercase tracking-widest">No Preview Available</p>
                    <p className="text-xs mt-1 text-muted-foreground">Binary or non-text files cannot be previewed.</p>
                  </div>
                </div>
              )}
            </div>
          </>
        ) : (
          <div className="flex flex-col items-center justify-center h-full gap-6 opacity-30 bg-muted/5">
            <div className="w-32 h-32 rounded-full bg-primary/5 flex items-center justify-center animate-pulse">
              <span className="material-symbols-outlined text-8xl text-primary/40">account_tree</span>
            </div>
            <div className="text-center">
              <p className="text-2xl font-serif text-foreground">Skill Explorer</p>
              <p className="text-xs mt-2 uppercase tracking-widest font-bold">Select a file from the tree to preview its content</p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
