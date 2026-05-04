"use client";

import React from "react";
import Link from "next/link";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

function preprocessWikilinks(md: string): string {
  return md
    .replace(/\[\[([^\]|]+)\|([^\]]+)\]\]/g, "[$2](/wiki/$1)")
    .replace(/\[\[([^\]]+)\]\]/g, "[$1](/wiki/$1)");
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = React.useState(false);
  return (
    <button
      onClick={() => {
        navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      }}
      className="absolute top-2 right-2 opacity-0 group-hover/code:opacity-100 transition-opacity px-2 py-1 rounded-md bg-muted/80 hover:bg-muted text-xs text-muted-foreground hover:text-foreground border border-border"
      title="Copy code"
    >
      <span className="material-symbols-outlined" style={{ fontSize: 13 }}>
        {copied ? "check" : "content_copy"}
      </span>
    </button>
  );
}

function extractHeadings(md: string): { id: string; text: string; level: number }[] {
  const headings: { id: string; text: string; level: number }[] = [];
  const lines = md.split("\n");
  for (const line of lines) {
    const match = line.match(/^(#{2,4})\s+(.+)$/);
    if (match) {
      const level = match[1].length;
      const text = match[2].trim();
      const id = text
        .toLowerCase()
        .replace(/[^\w\s-]/g, "")
        .replace(/\s+/g, "-");
      headings.push({ id, text, level });
    }
  }
  return headings;
}

export function WikiContent({
  markdown,
  onWikiLinkClick,
}: {
  markdown: string;
  onWikiLinkClick?: (slug: string) => void;
}) {
  const processed = preprocessWikilinks(markdown);
  const headings = React.useMemo(() => extractHeadings(markdown), [markdown]);
  const [activeHeading, setActiveHeading] = React.useState<string | null>(null);

  // Intersection observer for active heading tracking
  React.useEffect(() => {
    if (headings.length === 0) return;
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setActiveHeading(entry.target.id);
          }
        }
      },
      { rootMargin: "-80px 0px -60% 0px", threshold: 0.5 }
    );
    for (const h of headings) {
      const el = document.getElementById(h.id);
      if (el) observer.observe(el);
    }
    return () => observer.disconnect();
  }, [headings]);

  return (
    <div className="relative">
      {/* Table of Contents — only show when enough headings */}
      {headings.length >= 3 && (
        <div className="mb-8 rounded-xl border border-border bg-card/50 px-5 py-4">
          <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2.5 flex items-center gap-1.5">
            <span className="material-symbols-outlined" style={{ fontSize: 13 }}>toc</span>
            Contents
          </p>
          <nav className="flex flex-col gap-0.5">
            {headings.map((h) => (
              <a
                key={h.id}
                href={`#${h.id}`}
                className={`text-xs py-0.5 hover:text-primary transition-colors ${
                  activeHeading === h.id
                    ? "text-primary font-medium"
                    : "text-muted-foreground"
                }`}
                style={{ paddingLeft: (h.level - 2) * 16 }}
                onClick={(e) => {
                  e.preventDefault();
                  document.getElementById(h.id)?.scrollIntoView({ behavior: "smooth" });
                }}
              >
                {h.text}
              </a>
            ))}
          </nav>
        </div>
      )}

      <div className="prose-wiki">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            h1: ({ children }) => (
              <h1 className="font-heading text-3xl font-normal leading-tight text-foreground mt-0 mb-4">
                {children}
              </h1>
            ),
            h2: ({ children }) => {
              const text = typeof children === "string" ? children : "";
              const id = String(text)
                .toLowerCase()
                .replace(/[^\w\s-]/g, "")
                .replace(/\s+/g, "-");
              return (
                <h2
                  id={id}
                  className="font-heading text-2xl font-normal mt-10 mb-3 pb-2 border-b border-border text-foreground scroll-mt-20"
                >
                  {children}
                </h2>
              );
            },
            h3: ({ children }) => {
              const text = typeof children === "string" ? children : "";
              const id = String(text)
                .toLowerCase()
                .replace(/[^\w\s-]/g, "")
                .replace(/\s+/g, "-");
              return (
                <h3
                  id={id}
                  className="font-heading text-xl font-normal mt-7 mb-2 text-foreground scroll-mt-20"
                >
                  {children}
                </h3>
              );
            },
            h4: ({ children }) => {
              const text = typeof children === "string" ? children : "";
              const id = String(text)
                .toLowerCase()
                .replace(/[^\w\s-]/g, "")
                .replace(/\s+/g, "-");
              return (
                <h4
                  id={id}
                  className="font-heading text-lg font-normal mt-5 mb-1.5 text-foreground scroll-mt-20"
                >
                  {children}
                </h4>
              );
            },
            p: ({ children }) => (
              <p className="text-sm leading-7 text-foreground/90 mb-4">{children}</p>
            ),
            a: ({ href, children }) => {
              if (href?.startsWith("/wiki/")) {
                const slug = href.slice("/wiki/".length);
                if (onWikiLinkClick) {
                  return (
                    <button
                      onClick={() => onWikiLinkClick(slug)}
                      className="text-primary underline underline-offset-2 hover:text-primary/80 transition-colors"
                    >
                      {children}
                    </button>
                  );
                }
                return (
                  <Link
                    href={href}
                    className="text-primary underline underline-offset-2 hover:text-primary/80 transition-colors"
                  >
                    {children}
                  </Link>
                );
              }
              return (
                <a
                  href={href}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-primary underline underline-offset-2 hover:text-primary/80 transition-colors"
                >
                  {children}
                </a>
              );
            },
            code: ({ children, className }) => {
              const isBlock = className?.startsWith("language-");
              if (isBlock) {
                return (
                  <code className="block text-xs font-mono text-foreground/90 leading-6">
                    {children}
                  </code>
                );
              }
              return (
                <code className="bg-surface-variant text-primary px-1.5 py-0.5 rounded-sm font-mono text-[0.8em]">
                  {children}
                </code>
              );
            },
            pre: ({ children }) => {
              const codeText = React.Children.toArray(children)
                .map((child) => {
                  if (React.isValidElement(child)) {
                    return (child.props as { children?: string }).children ?? "";
                  }
                  return String(child);
                })
                .join("");
              return (
                <div className="relative group/code">
                  <pre className="bg-surface border border-border rounded-xl p-4 overflow-x-auto my-5 text-sm">
                    {children}
                  </pre>
                  <CopyButton text={codeText} />
                </div>
              );
            },
            blockquote: ({ children }) => (
              <blockquote className="border-l-4 border-primary/40 pl-4 my-4 italic text-muted-foreground">
                {children}
              </blockquote>
            ),
            ul: ({ children }) => (
              <ul className="list-disc list-outside pl-5 mb-4 space-y-1 text-sm text-foreground/90">
                {children}
              </ul>
            ),
            ol: ({ children }) => (
              <ol className="list-decimal list-outside pl-5 mb-4 space-y-1 text-sm text-foreground/90">
                {children}
              </ol>
            ),
            li: ({ children }) => <li className="leading-7">{children}</li>,
            hr: () => <hr className="border-border my-8" />,
            strong: ({ children }) => (
              <strong className="font-semibold text-foreground">{children}</strong>
            ),
            em: ({ children }) => <em className="italic">{children}</em>,
            table: ({ children }) => (
              <div className="my-5 rounded-xl border border-border overflow-hidden shadow-sahara">
                <Table>{children}</Table>
              </div>
            ),
            thead: ({ children }) => <TableHeader>{children}</TableHeader>,
            tbody: ({ children }) => <TableBody>{children}</TableBody>,
            tr: ({ children }) => <TableRow>{children}</TableRow>,
            th: ({ children }) => (
              <TableHead className="text-xs uppercase tracking-wider">{children}</TableHead>
            ),
            td: ({ children }) => <TableCell className="text-sm">{children}</TableCell>,
          }}
        >
          {processed}
        </ReactMarkdown>
      </div>
    </div>
  );
}
