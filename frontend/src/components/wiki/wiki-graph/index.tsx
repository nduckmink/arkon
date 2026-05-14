"use client";

import React from "react";
import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { forceX, forceY } from "d3-force";
import { wikiTypeColor, wikiTypeGroupLabel, wikiTypeIcon } from "../wiki-type-badge";
import { NodeInput } from "./types";
import { convexHull, scopeColor, nodeRadius } from "./utils";

// react-force-graph-2d uses canvas APIs (no SSR).
const ForceGraph2D = dynamic(() => import("react-force-graph-2d"), {
  ssr: false,
  loading: () => null,
}) as any;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type ForceGraphInstance = any;

type Node = NodeInput & {
  id: string;
  degree: number;
  // Populated by d3-force runtime:
  x?: number;
  y?: number;
  fx?: number;
  fy?: number;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  __targetX?: number;
};

type Link = {
  source: string | Node;
  target: string | Node;
};

type Props = {
  nodes: NodeInput[];
  edges: { from: string; to: string }[];
  centerSlug?: string;
  mini?: boolean;
  height?: number;
  onNodeClick?: (slug: string) => void;
};

const EDGE_COLOR = "rgba(120,112,106,0.35)";
const EDGE_HIGHLIGHT = "#c2652a";
const LABEL_COLOR = "#3a302a";
const BG_COLOR = "#faf5ee";

export function WikiGraph({
  nodes: rawNodes,
  edges: rawEdges,
  centerSlug,
  mini = false,
  height,
  onNodeClick,
}: Props) {
  const router = useRouter();
  const containerRef = React.useRef<HTMLDivElement>(null);
  const fgRef = React.useRef<ForceGraphInstance>(null);
  const [dimensions, setDimensions] = React.useState({ w: 800, h: height ?? 400 });
  // Hover state lives in a ref so canvas redraw callbacks can read it without
  // re-rendering the whole component (which would otherwise reset the sim).
  // We mirror it into a state value (`hoverVersion`) only to drive the tooltip
  // overlay's HTML render — bumping a counter is cheaper than a string update.
  const hoveredIdRef = React.useRef<string | null>(null);
  const [hoverVersion, setHoverVersion] = React.useState(0);
  const hoveredId = hoveredIdRef.current;
  const [tooltip, setTooltip] = React.useState<{
    x: number;
    y: number;
    title: string;
    type: string;
    degree: number;
    scopeType?: string;
    scopeName?: string | null;
  } | null>(null);

  // Measure container.
  React.useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const obs = new ResizeObserver((entries) => {
      const { width, height: h } = entries[0].contentRect;
      setDimensions({ w: width, h: height ?? h });
    });
    obs.observe(el);
    setDimensions({ w: el.clientWidth, h: height ?? el.clientHeight });
    return () => obs.disconnect();
  }, [height]);

  // Persistent map of node objects keyed by id. We mutate these in place
  // across re-renders so react-force-graph keeps the simulation's internal
  // state (vx, vy, x, y, alpha) intact when only the hover state changes.
  const nodeMapRef = React.useRef<Map<string, Node>>(new Map());

  // Build graph data + adjacency (memo so the simulation only re-warms when
  // raw inputs change, not on every parent render).
  const { nodes, links, adjacency, components, componentTargetX } = React.useMemo(() => {
    const degreeMap = new Map<string, number>();
    for (const e of rawEdges) {
      degreeMap.set(e.from, (degreeMap.get(e.from) ?? 0) + 1);
      degreeMap.set(e.to, (degreeMap.get(e.to) ?? 0) + 1);
    }
    // Reuse existing Node objects when possible so simulation state persists.
    const seen = new Set<string>();
    const nodes: Node[] = rawNodes.map((n) => {
      const existing = nodeMapRef.current.get(n.slug);
      const degree = degreeMap.get(n.slug) ?? 0;
      seen.add(n.slug);
      if (existing) {
        existing.title = n.title;
        existing.page_type = n.page_type;
        existing.scope_type = n.scope_type;
        existing.scope_name = n.scope_name;
        existing.degree = degree;
        return existing;
      }
      const fresh: Node = { ...n, id: n.slug, degree };
      nodeMapRef.current.set(n.slug, fresh);
      return fresh;
    });
    // Drop nodes that disappeared from input.
    for (const id of nodeMapRef.current.keys()) {
      if (!seen.has(id)) nodeMapRef.current.delete(id);
    }
    const ids = new Set(nodes.map((n) => n.id));
    const links: Link[] = rawEdges
      .filter((e) => ids.has(e.from) && ids.has(e.to))
      .map((e) => ({ source: e.from, target: e.to }));

    // Adjacency for hover-neighbour highlighting.
    const adj = new Map<string, Set<string>>();
    for (const n of nodes) adj.set(n.id, new Set());
    for (const l of links) {
      const s = typeof l.source === "string" ? l.source : l.source.id;
      const t = typeof l.target === "string" ? l.target : l.target.id;
      adj.get(s)?.add(t);
      adj.get(t)?.add(s);
    }

    // Connected components → spread along X so disconnected clusters separate.
    const compOf = new Map<string, number>();
    let cid = 0;
    for (const n of nodes) {
      if (compOf.has(n.id)) continue;
      const stack = [n.id];
      while (stack.length > 0) {
        const cur = stack.pop()!;
        if (compOf.has(cur)) continue;
        compOf.set(cur, cid);
        for (const nb of adj.get(cur) ?? []) {
          if (!compOf.has(nb)) stack.push(nb);
        }
      }
      cid++;
    }
    return {
      nodes,
      links,
      adjacency: adj,
      components: compOf,
      componentTargetX: cid,
    };
  }, [rawNodes, rawEdges]);

  // Wire custom forces + pin centerSlug + cluster X spread once per dataset.
  React.useEffect(() => {
    const fg = fgRef.current;
    if (!fg || nodes.length === 0) return;

    // Spread components horizontally so they don't all collide into one blob.
    const margin = dimensions.w * 0.15;
    const usable = Math.max(dimensions.w - 2 * margin, 1);

    // Build stable scope-group index for dept/project nodes.
    const scopeOrder = new Map<string, number>();
    for (const n of nodes) {
      if (n.scope_type === "department" || n.scope_type === "project") {
        const key = `${n.scope_type}:${n.scope_id || n.scope_name || ""}`;
        if (!scopeOrder.has(key)) scopeOrder.set(key, scopeOrder.size);
      }
    }
    const totalScopes = scopeOrder.size;

    for (const n of nodes) {
      const c = components.get(n.id) ?? 0;
      const compX =
        componentTargetX <= 1
          ? 0
          : -usable / 2 + (usable * c) / (componentTargetX - 1);

      if (n.scope_type === "department" || n.scope_type === "project") {
        const key = `${n.scope_type}:${n.scope_id || n.scope_name || ""}`;
        const si = scopeOrder.get(key) ?? 0;
        const scopeX =
          totalScopes <= 1 ? 0 : -usable / 2 + (usable * si) / (totalScopes - 1);
        // Bias toward scope cluster (70%) while still respecting component spread (30%).
        n.__targetX = scopeX * 0.7 + compX * 0.3;
      } else {
        n.__targetX = compX;
      }
    }

    fg.d3Force(
      "x",
      forceX<Node>((d: Node) => d.__targetX ?? 0).strength(0.05)
    );
    fg.d3Force("y", forceY<Node>(0).strength(0.03));

    // Charge stronger for hub nodes so leaves don't pile up on top.
    const charge = fg.d3Force("charge");
    if (charge) {
      charge.strength((d: Node) => (mini ? -60 : -120) * Math.sqrt((d.degree ?? 0) + 1));
    }
    const link = fg.d3Force("link");
    if (link) link.distance(mini ? 35 : 70).strength(0.4);

    // Disable the default center force because it fights with our pinned node and rips the graph apart!
    fg.d3Force("center", null);

    // Pin the center node so the graph orbits around it (at 0,0).
    for (const n of nodes) {
      if (n.id === centerSlug) {
        n.fx = 0;
        n.fy = 0;
      } else {
        n.fx = undefined;
        n.fy = undefined;
      }
    }

    fg.d3ReheatSimulation();
  }, [nodes, components, componentTargetX, centerSlug, dimensions.w, dimensions.h, mini]);

  // Auto fit-to-canvas when the simulation cools down.
  const hasFitRef = React.useRef(false);
  React.useEffect(() => {
    hasFitRef.current = false;
  }, [rawNodes.length, rawEdges.length]);

  const handleEngineStop = React.useCallback(() => {
    if (hasFitRef.current) return;
    hasFitRef.current = true;
    if (!mini) {
      if (rawNodes.length <= 1) {
        fgRef.current?.centerAt(0, 0, 0);
        fgRef.current?.zoom(2, 400);
      } else {
        fgRef.current?.zoomToFit(400, 60);
      }
    } else {
      // In mini mode, nodes are pinned around (0, 0), so we pan the camera there!
      fgRef.current?.centerAt(0, 0, 0);
      fgRef.current?.zoom(1.4, 400);
    }
  }, [mini, rawNodes.length]);

  // Stable graphData reference — react-force-graph treats a new object literal
  // as a data change and resets simulation state, which is what was causing
  // nodes to drift away on every hover.
  const graphData = React.useMemo(() => ({ nodes, links }), [nodes, links]);

  // Held in a ref so the canvas draw callbacks below can read it without
  // being recreated on hover. Updated synchronously inside handleNodeHover
  // (BEFORE refresh()) so the next canvas frame sees the up-to-date set.
  const neighborIdsRef = React.useRef<Set<string> | null>(null);
  // Stable ref to adjacency so handleNodeHover stays referentially stable.
  const adjacencyRef = React.useRef(adjacency);
  adjacencyRef.current = adjacency;

  // --- Custom node draw (preserves existing colour scheme) ---------------------
  // Callback identity intentionally changes on hoverVersion — react-force-graph
  // diffs callback props by reference and only repaints when they change. Keeps
  // graphData stable (so sim doesn't reset) but lets the canvas refresh.
  const drawNode = React.useCallback(
    (rawNode: object, ctx: CanvasRenderingContext2D, globalScale: number) => {
      const n = rawNode as Node;
      if (n.x === undefined || n.y === undefined) return;
      const r = nodeRadius(n.degree ?? 0, mini);
      const color = wikiTypeColor(n.page_type);
      const hovered = hoveredIdRef.current;
      const neighborSet = neighborIdsRef.current;
      const isHovered = hovered === n.id;
      const isDimmed = !!hovered && !neighborSet?.has(n.id);
      const isCenter = n.id === centerSlug;

      // Hover glow.
      if (isHovered) {
        ctx.beginPath();
        ctx.fillStyle = color;
        ctx.globalAlpha = 0.12;
        ctx.arc(n.x, n.y, r * 1.8, 0, 2 * Math.PI);
        ctx.fill();
        ctx.globalAlpha = 1;
      }

      // Background ring to occlude edges behind the node.
      if (!isDimmed) {
        ctx.beginPath();
        ctx.fillStyle = BG_COLOR;
        ctx.arc(n.x, n.y, (isHovered ? r * 1.3 : r) + 1, 0, 2 * Math.PI);
        ctx.fill();
      }

      // Node body.
      ctx.beginPath();
      ctx.fillStyle = color;
      ctx.globalAlpha = isDimmed ? 0.15 : 1;
      ctx.arc(n.x, n.y, isHovered ? r * 1.3 : r, 0, 2 * Math.PI);
      ctx.fill();
      ctx.globalAlpha = 1;

      // Border.
      ctx.lineWidth = isCenter ? 2.5 : isHovered ? 2 : 1;
      ctx.strokeStyle = isCenter
        ? "#3a302a"
        : isHovered
          ? color
          : "rgba(255,255,255,0.85)";
      ctx.stroke();

      // Label visibility: hide in mini, hide on dimmed, hide when zoom too low
      // (Obsidian-style declutter), always show on hover and on center.
      const labelVisible =
        !mini &&
        !isDimmed &&
        (isHovered || isCenter || globalScale >= 1.2 || (n.degree ?? 0) >= 4);
      if (labelVisible) {
        const fontSize = isHovered ? 12 : 11;
        ctx.font = `${isHovered ? 600 : 400} ${fontSize}px sans-serif`;
        ctx.fillStyle = LABEL_COLOR;
        ctx.globalAlpha = isHovered || isCenter ? 1 : 0.7;
        ctx.textBaseline = "middle";
        ctx.textAlign = "left";
        const text = n.title.length > 24 ? n.title.slice(0, 22) + "…" : n.title;
        ctx.fillText(text, n.x + r + 5, n.y);
        ctx.globalAlpha = 1;
      }
    },
    // hoverVersion bump → callback ref changes → react-force-graph repaints.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [mini, centerSlug, hoverVersion]
  );

  // --- Custom link colour/width — also bump on hover to trigger repaint ---
  const linkColor = React.useCallback(
    (rawLink: object) => {
      const hovered = hoveredIdRef.current;
      const l = rawLink as Link;
      if (!hovered) return EDGE_COLOR;
      const s = typeof l.source === "string" ? l.source : l.source.id;
      const t = typeof l.target === "string" ? l.target : l.target.id;
      const hot = s === hovered || t === hovered;
      return hot ? EDGE_HIGHLIGHT : "rgba(120,112,106,0.08)";
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [hoverVersion]
  );
  const linkWidth = React.useCallback(
    (rawLink: object) => {
      const hovered = hoveredIdRef.current;
      const l = rawLink as Link;
      if (!hovered) return 1.2;
      const s = typeof l.source === "string" ? l.source : l.source.id;
      const t = typeof l.target === "string" ? l.target : l.target.id;
      return s === hovered || t === hovered ? 2.5 : 1;
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [hoverVersion]
  );

  // --- Scope hulls (workspace boundaries) drawn beneath nodes ---
  const drawScopeHulls = React.useCallback(
    (ctx: CanvasRenderingContext2D) => {
      if (mini) return;

      const drawGroup = (
        gnodes: Node[],
        color: string,
        dash: number[],
        alpha: string,
        label: string,
      ) => {
        const points: [number, number][] = gnodes.map((n) => [n.x!, n.y!]);
        const hull = convexHull(points);
        if (hull.length === 0) return;
        const padding = 30;
        const cx = hull.reduce((s, p) => s + p[0], 0) / hull.length;
        const cy = hull.reduce((s, p) => s + p[1], 0) / hull.length;
        const expanded =
          hull.length === 1
            ? null
            : hull.map(([x, y]) => {
                const dx = x - cx;
                const dy = y - cy;
                const len = Math.sqrt(dx * dx + dy * dy) || 1;
                return [x + (dx / len) * padding, y + (dy / len) * padding] as [number, number];
              });

        ctx.save();
        ctx.setLineDash(dash);
        ctx.strokeStyle = color;
        ctx.lineWidth = 1;
        ctx.fillStyle = color + alpha;
        ctx.globalAlpha = 0.85;

        ctx.beginPath();
        if (expanded == null) {
          const [x, y] = hull[0];
          ctx.arc(x, y, padding, 0, 2 * Math.PI);
        } else if (expanded.length === 2) {
          const [x1, y1] = expanded[0];
          const [x2, y2] = expanded[1];
          ctx.moveTo(x1, y1);
          ctx.lineTo(x2, y2);
        } else {
          const n = expanded.length;
          const start: [number, number] = [
            (expanded[n - 1][0] + expanded[0][0]) / 2,
            (expanded[n - 1][1] + expanded[0][1]) / 2,
          ];
          ctx.moveTo(start[0], start[1]);
          for (let i = 0; i < n; i++) {
            const curr = expanded[i];
            const next = expanded[(i + 1) % n];
            const mx = (curr[0] + next[0]) / 2;
            const my = (curr[1] + next[1]) / 2;
            ctx.quadraticCurveTo(curr[0], curr[1], mx, my);
          }
          ctx.closePath();
        }
        ctx.fill();
        ctx.stroke();
        ctx.setLineDash([]);

        const topY = Math.min(...points.map((p) => p[1])) - padding - 6;
        ctx.fillStyle = color;
        ctx.font = "600 10px sans-serif";
        ctx.textAlign = "center";
        ctx.globalAlpha = 0.7;
        ctx.fillText(label, cx, topY);
        ctx.restore();
      };

      // Department hulls (drawn first, underneath).
      const deptGroups: Record<string, Node[]> = {};
      for (const n of nodes) {
        if (n.scope_type !== "department" || n.x === undefined || n.y === undefined) continue;
        const key = n.scope_name || "Department";
        (deptGroups[key] ||= []).push(n);
      }
      Object.entries(deptGroups).forEach(([key, gnodes], idx) => {
        drawGroup(gnodes, scopeColor(idx), [8, 5], "0e", key);
      });

      // Project hulls (drawn on top, more prominent).
      const projGroups: Record<string, Node[]> = {};
      for (const n of nodes) {
        if (n.scope_type !== "project" || n.x === undefined || n.y === undefined) continue;
        const key = n.scope_name || "Workspace";
        (projGroups[key] ||= []).push(n);
      }
      const deptCount = Object.keys(deptGroups).length;
      Object.entries(projGroups).forEach(([key, gnodes], idx) => {
        drawGroup(gnodes, scopeColor(deptCount + idx), [6, 4], "1e", key);
      });
    },
    [mini, nodes]
  );

  const handleNodeClick = React.useCallback(
    (rawNode: object) => {
      const n = rawNode as Node;
      if (onNodeClick) onNodeClick(n.id);
      else router.push(`/wiki/${n.id}`);
    },
    [onNodeClick, router]
  );

  const handleNodeHover = React.useCallback((rawNode: object | null) => {
    const n = rawNode as Node | null;
    const newId = n?.id ?? null;
    if (hoveredIdRef.current === newId) return;
    hoveredIdRef.current = newId;
    // Compute neighbours synchronously so the next canvas frame paints with
    // the correct dimming. Deferring this to a useEffect would lose one frame.
    if (!newId) {
      neighborIdsRef.current = null;
    } else {
      const set = new Set<string>([newId]);
      for (const nb of adjacencyRef.current.get(newId) ?? []) set.add(nb);
      neighborIdsRef.current = set;
    }
    // Bump version so the canvas callbacks (drawNode/linkColor/linkWidth)
    // get new identities — react-force-graph picks that up and repaints.
    setHoverVersion((v) => v + 1);
    if (!n) {
      setTooltip(null);
    } else {
      setTooltip((prev) => ({
        ...(prev ?? { x: 0, y: 0 }),
        title: n.title,
        type: n.page_type,
        degree: n.degree ?? 0,
        scopeType: n.scope_type,
        scopeName: n.scope_name,
      }));
    }
  }, []);

  // --- Legend data ---
  const typeCounts = React.useMemo(() => {
    const counts: Record<string, number> = {};
    for (const n of rawNodes) counts[n.page_type] = (counts[n.page_type] ?? 0) + 1;
    return counts;
  }, [rawNodes]);

  const scopeCounts = React.useMemo(() => {
    const counts: Record<string, { count: number; scopeType: string }> = {};
    for (const n of rawNodes) {
      let label: string;
      let scopeType: string;
      if (n.scope_type === "project") {
        label = n.scope_name || "Workspace";
        scopeType = "project";
      } else if (n.scope_type === "department") {
        label = n.scope_name || "Department";
        scopeType = "department";
      } else {
        label = "Global";
        scopeType = "global";
      }
      if (!counts[label]) counts[label] = { count: 0, scopeType };
      counts[label].count++;
    }
    return Object.entries(counts)
      .map(([label, { count, scopeType }]) => ({ label, count, scopeType }))
      .sort((a, b) => b.count - a.count);
  }, [rawNodes]);

  return (
    <div
      ref={containerRef}
      className={`relative w-full overflow-hidden ${mini ? "rounded-xl border border-border" : ""}`}
      style={{ height: height ?? "100%", background: BG_COLOR }}
      onMouseMove={(e) => {
        const rect = containerRef.current?.getBoundingClientRect();
        if (!rect) return;
        setTooltip((prev) =>
          prev ? { ...prev, x: e.clientX - rect.left, y: e.clientY - rect.top - 16 } : prev
        );
      }}
    >
      <ForceGraph2D
        ref={fgRef}
        width={dimensions.w}
        height={dimensions.h}
        graphData={graphData}
        backgroundColor={BG_COLOR}
        nodeId="id"
        nodeRelSize={1}
        nodeCanvasObject={drawNode}
        nodePointerAreaPaint={(rawNode: object, color: string, ctx: CanvasRenderingContext2D) => {
          const n = rawNode as Node;
          if (n.x === undefined || n.y === undefined) return;
          ctx.fillStyle = color;
          ctx.beginPath();
          ctx.arc(n.x, n.y, nodeRadius(n.degree ?? 0, mini) + 4, 0, 2 * Math.PI);
          ctx.fill();
        }}
        linkColor={linkColor}
        linkWidth={linkWidth}
        onNodeClick={handleNodeClick}
        onNodeHover={handleNodeHover}
        onRenderFramePre={drawScopeHulls}
        cooldownTicks={mini ? 50 : 90}
        d3AlphaDecay={0.06}
        d3VelocityDecay={0.55}
        onEngineStop={handleEngineStop}
        enableZoomInteraction={!mini}
        enablePanInteraction={!mini}
        enableNodeDrag={!mini}
        minZoom={0.2}
        maxZoom={5}
      />

      {/* Tooltip */}
      {tooltip && hoveredId && (
        <div
          className="pointer-events-none z-50 px-3 py-2 rounded-lg text-xs shadow-lg"
          style={{
            position: "absolute",
            left: Math.min(tooltip.x + 12, dimensions.w - 220),
            top: Math.max(tooltip.y - 8, 8),
            background: "var(--color-card, #fff)",
            color: "var(--color-foreground, #3a302a)",
            border: "1px solid var(--color-border, rgba(216,208,200,0.6))",
            maxWidth: 220,
          }}
        >
          <p className="font-medium text-sm mb-0.5 truncate">{tooltip.title}</p>
          <div className="flex items-center gap-2 text-muted-foreground">
            <span
              className="w-2 h-2 rounded-full shrink-0"
              style={{ background: wikiTypeColor(tooltip.type) }}
            />
            <span className="capitalize">{tooltip.type}</span>
            <span className="ml-auto">{tooltip.degree} links</span>
          </div>
          {tooltip.scopeType && (
            <div className="flex items-center gap-1.5 mt-1 pt-1 border-t border-border/50 text-muted-foreground">
              <span className="material-symbols-outlined" style={{ fontSize: 11 }}>
                {tooltip.scopeType === "project"
                  ? "folder_special"
                  : tooltip.scopeType === "department"
                  ? "business"
                  : "public"}
              </span>
              <span className="truncate">
                {tooltip.scopeType === "project"
                  ? tooltip.scopeName || "Workspace"
                  : tooltip.scopeType === "department"
                  ? tooltip.scopeName || "Department"
                  : "Global"}
              </span>
            </div>
          )}
        </div>
      )}

      {/* Legend */}
      {!mini && (
        <div className="absolute bottom-3 left-3 rounded-xl border border-border bg-card/90 backdrop-blur-sm px-3 py-2.5 text-xs shadow-sm max-w-[240px]">
          <div className="mb-1.5 font-semibold text-foreground text-xs">Node Types</div>
          <div className="flex flex-col gap-1">
            {Object.entries(typeCounts)
              .sort((a, b) => b[1] - a[1])
              .map(([type, count]) => (
                <div
                  key={type}
                  className="flex items-center gap-2 rounded px-1 py-0.5 hover:bg-accent/30 transition-colors"
                >
                  <span
                    className="w-2.5 h-2.5 rounded-full shrink-0"
                    style={{
                      background: wikiTypeColor(type),
                      boxShadow: `0 0 4px ${wikiTypeColor(type)}40`,
                    }}
                  />
                  <span
                    className="material-symbols-outlined"
                    style={{ fontSize: 11, color: wikiTypeColor(type) }}
                  >
                    {wikiTypeIcon(type)}
                  </span>
                  <span className="text-muted-foreground">{wikiTypeGroupLabel(type)}</span>
                  <span className="text-muted-foreground/60 ml-auto tabular-nums">{count}</span>
                </div>
              ))}
          </div>
          {scopeCounts.length > 0 && (
            <>
              <div className="mt-2 pt-2 border-t border-border/50 mb-1.5 font-semibold text-foreground text-xs">
                Scope
              </div>
              <div className="flex flex-col gap-1">
                {scopeCounts.map(({ label, count, scopeType }) => (
                  <div
                    key={label}
                    className="flex items-center gap-2 rounded px-1 py-0.5 hover:bg-accent/30 transition-colors"
                  >
                    <span
                      className="material-symbols-outlined text-muted-foreground"
                      style={{ fontSize: 12 }}
                    >
                      {scopeType === "project"
                        ? "folder_special"
                        : scopeType === "department"
                        ? "business"
                        : "public"}
                    </span>
                    <span className="text-muted-foreground truncate">{label}</span>
                    <span className="text-muted-foreground/60 ml-auto tabular-nums">{count}</span>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      )}

      {/* Zoom controls */}
      {!mini && (
        <div className="absolute bottom-3 right-3 flex flex-col items-center gap-1 rounded-xl border border-border bg-card/90 backdrop-blur-sm shadow-sm p-1">
          <button
            onClick={() => fgRef.current?.zoom(fgRef.current.zoom() * 1.2, 200)}
            className="w-7 h-7 flex items-center justify-center rounded-lg hover:bg-accent/50 transition-colors text-muted-foreground hover:text-foreground"
            title="Zoom In"
          >
            <span className="material-symbols-outlined" style={{ fontSize: 16 }}>
              add
            </span>
          </button>
          <button
            onClick={() => fgRef.current?.zoom(fgRef.current.zoom() / 1.2, 200)}
            className="w-7 h-7 flex items-center justify-center rounded-lg hover:bg-accent/50 transition-colors text-muted-foreground hover:text-foreground"
            title="Zoom Out"
          >
            <span className="material-symbols-outlined" style={{ fontSize: 16 }}>
              remove
            </span>
          </button>
          <div className="w-5 border-t border-border/50" />
          <button
            onClick={() => fgRef.current?.zoomToFit(400, 60)}
            className="w-7 h-7 flex items-center justify-center rounded-lg hover:bg-accent/50 transition-colors text-muted-foreground hover:text-foreground"
            title="Fit View"
          >
            <span className="material-symbols-outlined" style={{ fontSize: 14 }}>
              fit_screen
            </span>
          </button>
        </div>
      )}
    </div>
  );
}

export { WikiGraphMini } from "./wiki-graph-mini";
