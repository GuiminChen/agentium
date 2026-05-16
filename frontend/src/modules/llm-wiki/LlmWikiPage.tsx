import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import ReactFlow, { Background, Controls, MarkerType, MiniMap } from "reactflow";
import type { Edge, Node } from "reactflow";
import { useTranslation } from "react-i18next";

import "reactflow/dist/style.css";

import { readApiError } from "../../core/http/readApiError";
import {
  fetchWikiGraph,
  fetchWikiPage,
  fetchWikiPages,
  fetchWikiWirePing,
  searchWikiLiteral,
  type WikiPageSummaryRow,
} from "./wikiApi";

type Trie = { segment: string; fullPath: string | null; children: Map<string, Trie> };

function insertPath(root: Trie, logicalPath: string): void {
  const parts = logicalPath.split("/").filter(Boolean);
  let cur = root;
  for (let i = 0; i < parts.length; i++) {
    const seg = parts[i];
    if (!cur.children.has(seg)) {
      cur.children.set(seg, { segment: seg, fullPath: null, children: new Map() });
    }
    cur = cur.children.get(seg)!;
    if (i === parts.length - 1) {
      cur.fullPath = logicalPath;
    }
  }
}

function TrieView({
  node,
  depth,
  selectedPath,
  onPick,
}: {
  node: Trie;
  depth: number;
  selectedPath: string | null;
  onPick: (p: string) => void;
}): React.ReactElement {
  const kids = [...node.children.values()].sort((a, b) => {
    const aLeaf = a.children.size === 0;
    const bLeaf = b.children.size === 0;
    if (aLeaf !== bLeaf) {
      return aLeaf ? 1 : -1;
    }
    return a.segment.localeCompare(b.segment);
  });
  const pad = depth * 14;

  const labelClick = (): void => {
    if (node.fullPath) {
      onPick(node.fullPath);
    }
  };

  return (
    <div className="select-none">
      {node.segment !== "" ? (
        <div className="flex items-center gap-1" style={{ paddingLeft: `${pad}px` }}>
          <span className="text-slate-500">•</span>
          {node.fullPath ? (
            <button
              type="button"
              className={`text-left hover:underline ${
                selectedPath === node.fullPath ? "font-semibold text-amber-200" : "text-slate-200"
              }`}
              onClick={labelClick}
            >
              {node.segment}
            </button>
          ) : (
            <span className="text-slate-300">{node.segment}</span>
          )}
        </div>
      ) : null}
      {kids.map((ch, idx) => (
        <TrieView
          key={`${depth}-${idx}-${ch.segment}`}
          node={ch}
          depth={depth + 1}
          selectedPath={selectedPath}
          onPick={onPick}
        />
      ))}
    </div>
  );
}

function flowFromGraph(payload: {
  nodes: Array<{ id: string; label: string; path: string }>;
  edges: Array<{ source: string; target: string }>;
}): { nodes: Node[]; edges: Edge[] } {
  const nlen = payload.nodes.length;
  const cols = Math.max(2, Math.ceil(Math.sqrt(Math.max(1, nlen))));
  const nodes: Node[] = payload.nodes.map((node, idx) => ({
    id: node.id,
    position: {
      x: (idx % cols) * 200,
      y: Math.floor(idx / cols) * 90,
    },
    data: { label: node.label || node.path || node.id },
    style: { fontSize: 11, padding: 6 },
  }));
  const edges: Edge[] = payload.edges.map((e, idx) => ({
    id: `e-${idx}`,
    source: e.source,
    target: e.target,
    markerEnd: { type: MarkerType.ArrowClosed },
  }));
  return { nodes, edges };
}

export function LlmWikiPage(): React.ReactElement {
  const { t } = useTranslation();
  const [prefix, setPrefix] = React.useState("");
  const [selectedPath, setSelectedPath] = React.useState<string | null>(null);
  const [mainTab, setMainTab] = React.useState<"preview" | "graph" | "search">("preview");

  const [graphScope, setGraphScope] = React.useState<"tenant" | "session">("tenant");
  const [graphSessionId, setGraphSessionId] = React.useState("");

  const [searchScope, setSearchScope] = React.useState<"tenant" | "session">("tenant");
  const [searchSessionId, setSearchSessionId] = React.useState("");
  const [searchQ, setSearchQ] = React.useState("");
  const [searchResults, setSearchResults] = React.useState<Array<{ logical_path?: string }>>([]);
  const [searchErr, setSearchErr] = React.useState<string | null>(null);

  const pagesQuery = useQuery({
    queryKey: ["wiki-pages", prefix.trim()],
    queryFn: () =>
      fetchWikiPages({
        prefix: prefix.trim() || undefined,
        limit: 500,
        offset: 0,
      }),
    retry: false,
  });

  const pageDetailQuery = useQuery({
    queryKey: ["wiki-page", selectedPath],
    queryFn: () => fetchWikiPage(selectedPath!),
    enabled: Boolean(selectedPath),
    retry: false,
  });

  const trie = React.useMemo(() => {
    const root: Trie = { segment: "", fullPath: null, children: new Map() };
    const paths = pagesQuery.data?.items.map((row: WikiPageSummaryRow) => row.logical_path) ?? [];
    for (const logicalPath of paths) {
      insertPath(root, logicalPath);
    }
    return root;
  }, [pagesQuery.data]);

  const graphQuery = useQuery({
    queryKey: ["wiki-graph", graphScope, graphSessionId.trim()],
    queryFn: () =>
      fetchWikiGraph({
        scope: graphScope,
        sessionId: graphScope === "session" ? graphSessionId.trim() : undefined,
        maxPages: 160,
      }),
    enabled: mainTab === "graph" && (graphScope === "tenant" || graphSessionId.trim().length > 0),
    retry: false,
  });

  const flow = React.useMemo(
    () =>
      graphQuery.data
        ? flowFromGraph(graphQuery.data as Parameters<typeof flowFromGraph>[0])
        : { nodes: [], edges: [] },
    [graphQuery.data],
  );

  const http503 = React.useMemo(() => {
    const err = pagesQuery.error as (Error & { httpStatus?: number }) | undefined;
    return Boolean(err?.httpStatus === 503);
  }, [pagesQuery.error]);

  const wikiWirePingQuery = useQuery({
    queryKey: ["wiki-wire-ping"],
    queryFn: fetchWikiWirePing,
    enabled: pagesQuery.status === "error" && http503,
    retry: false,
  });

  async function runSearch(): Promise<void> {
    setSearchErr(null);
    setSearchResults([]);
    try {
      const hits = await searchWikiLiteral({
        q: searchQ,
        scope: searchScope,
        sessionId: searchScope === "session" ? searchSessionId : undefined,
        limit: 20,
      });
      const literals = hits.literals as Array<{ logical_path?: string }>;
      setSearchResults(Array.isArray(literals) ? literals : []);
    } catch (e: unknown) {
      const msg =
        e instanceof Response
          ? await readApiError(e)
          : e instanceof Error
            ? e.message
            : String(e);
      setSearchErr(msg);
    }
  }

  const detailErr = pageDetailQuery.error as (Error & { httpStatus?: number }) | undefined;

  return (
    <div className="flex min-h-0 flex-1 flex-row bg-white">
      <aside className="flex w-[17rem] shrink-0 flex-col border-r border-slate-800 bg-slate-900 text-sm text-slate-100">
        <div className="border-b border-slate-800 px-3 py-2 font-semibold text-slate-100">
          {t("llmWiki.vault")}
        </div>
        <div className="border-b border-slate-800 px-3 py-2">
          <label className="mb-1 block text-xs text-slate-400">{t("llmWiki.pathPrefixFilter")}</label>
          <input
            type="text"
            value={prefix}
            onChange={(e) => setPrefix(e.target.value)}
            placeholder="sessions/… raw/…"
            className="w-full rounded bg-slate-800 px-2 py-1 text-xs text-slate-100 outline-none ring-blue-600 focus:ring-1"
          />
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-1 py-2">
          {pagesQuery.isPending ? (
            <div className="px-3 text-slate-500">{t("common.loading")}</div>
          ) : http503 ? (
            <div className="px-3 text-amber-200">
              <p className="font-medium">{t("llmWiki.unavailable503")}</p>
              {(pagesQuery.error as Error | undefined)?.message ? (
                <p className="mt-2 whitespace-pre-wrap text-xs text-slate-300 leading-relaxed">
                  {(pagesQuery.error as Error).message}
                </p>
              ) : null}
              <div className="mt-4 border-t border-slate-700 pt-3">
                <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">
                  {t("llmWiki.wireDiagnostics")}
                </p>
                {wikiWirePingQuery.isPending ? (
                  <p className="mt-1 text-xs text-slate-400">{t("common.loading")}</p>
                ) : wikiWirePingQuery.isError ? (
                  <p className="mt-1 whitespace-pre-wrap text-xs text-rose-300">
                    {(wikiWirePingQuery.error as Error)?.message ?? t("common.error")}
                  </p>
                ) : wikiWirePingQuery.data ? (
                  <div className="mt-2 space-y-2">
                    {Array.isArray(wikiWirePingQuery.data.hints) && wikiWirePingQuery.data.hints.length ? (
                      <ul className="list-inside list-disc space-y-1 text-xs text-amber-100/95">
                        {wikiWirePingQuery.data.hints.map((h: string, i: number) => (
                          <li key={`h-${i}`}>{h}</li>
                        ))}
                      </ul>
                    ) : (
                      <p className="text-xs text-slate-400">{t("llmWiki.wireInspectJson")}</p>
                    )}
                    <pre className="max-h-48 overflow-auto rounded bg-slate-950/90 p-2 text-[10px] leading-snug text-slate-300">
                      {JSON.stringify(wikiWirePingQuery.data, null, 2)}
                    </pre>
                  </div>
                ) : null}
              </div>
            </div>
          ) : pagesQuery.isError ? (
            <div className="px-3 text-rose-300">
              {(pagesQuery.error as Error)?.message ?? t("common.error")}
            </div>
          ) : (
            <TrieView
              node={trie}
              depth={0}
              selectedPath={selectedPath}
              onPick={(p) => {
                setSelectedPath(p);
                setMainTab("preview");
              }}
            />
          )}
        </div>
      </aside>

      <main className="flex min-h-0 min-w-0 flex-1 flex-col">
        <header className="flex shrink-0 flex-wrap gap-3 border-b border-slate-200 bg-slate-50 px-4 py-2">
          {(["preview", "graph", "search"] as const).map((tab) => (
            <button
              key={tab}
              type="button"
              className={`rounded px-3 py-1 text-sm ${
                mainTab === tab ? "bg-slate-900 text-white" : "bg-white text-slate-700 hover:bg-slate-100"
              }`}
              onClick={() => setMainTab(tab)}
            >
              {tab === "preview"
                ? t("llmWiki.tabPreview")
                : tab === "graph"
                  ? t("llmWiki.tabGraph")
                  : t("llmWiki.tabSearch")}
            </button>
          ))}
          <span className="ml-auto max-w-xl truncate text-xs text-slate-500">
            {selectedPath ?? t("llmWiki.noPageSelected")}
          </span>
        </header>

        <div className="min-h-0 flex-1 overflow-hidden">
          {mainTab === "preview" ? (
            <div className="h-full overflow-y-auto px-6 py-4">
              {!selectedPath ? (
                <p className="text-slate-500">{t("llmWiki.pickFromVault")}</p>
              ) : pageDetailQuery.isPending ? (
                <p>{t("common.loading")}</p>
              ) : detailErr ? (
                <p className="text-rose-600">{detailErr.message}</p>
              ) : (
                <article className="wiki-md-preview">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{pageDetailQuery.data?.body_md ?? ""}</ReactMarkdown>
                </article>
              )}
            </div>
          ) : null}

          {mainTab === "graph" ? (
            <div className="flex h-full min-h-[28rem] flex-col">
              <div className="flex shrink-0 flex-wrap gap-3 border-b border-slate-200 px-4 py-2 text-sm">
                <label className="flex items-center gap-2 text-slate-700">
                  <span>{t("llmWiki.graphScope")}</span>
                  <select
                    value={graphScope}
                    className="rounded border px-2 py-1 text-xs"
                    onChange={(e) => setGraphScope(e.target.value as "tenant" | "session")}
                  >
                    <option value="tenant">tenant</option>
                    <option value="session">session</option>
                  </select>
                </label>
                {graphScope === "session" ? (
                  <label className="flex items-center gap-2 text-slate-700">
                    <span>{t("llmWiki.sessionId")}</span>
                    <input
                      type="text"
                      value={graphSessionId}
                      onChange={(e) => setGraphSessionId(e.target.value)}
                      className="w-52 rounded border px-2 py-1 text-xs"
                    />
                  </label>
                ) : null}
              </div>
              <div className="relative min-h-0 flex-1">
                {graphQuery.isPending ? (
                  <div className="p-6 text-sm text-slate-500">{t("common.loading")}</div>
                ) : graphQuery.isError ? (
                  <div className="p-6 text-sm text-rose-600">
                    {(graphQuery.error as Error).message ?? t("common.error")}
                  </div>
                ) : (
                  <ReactFlow
                    className="h-full w-full bg-slate-50"
                    nodes={flow.nodes}
                    edges={flow.edges}
                    fitView
                    attributionPosition="bottom-right"
                  >
                    <MiniMap zoomable />
                    <Controls />
                    <Background gap={14} />
                  </ReactFlow>
                )}
              </div>
            </div>
          ) : null}

          {mainTab === "search" ? (
            <div className="space-y-3 px-4 py-4 text-sm">
              <div className="flex flex-wrap gap-3">
                <input
                  type="text"
                  className="min-w-[12rem] flex-1 rounded border px-3 py-1"
                  value={searchQ}
                  placeholder={t("llmWiki.searchPlaceholder")}
                  onChange={(e) => setSearchQ(e.target.value)}
                />
                <select
                  value={searchScope}
                  className="rounded border px-2 py-1 text-xs"
                  onChange={(e) => setSearchScope(e.target.value as "tenant" | "session")}
                >
                  <option value="tenant">tenant</option>
                  <option value="session">session</option>
                </select>
                {searchScope === "session" ? (
                  <input
                    type="text"
                    placeholder={t("llmWiki.sessionId")}
                    className="w-48 rounded border px-2 py-1 text-xs"
                    value={searchSessionId}
                    onChange={(e) => setSearchSessionId(e.target.value)}
                  />
                ) : null}
                <button
                  type="button"
                  className="rounded bg-slate-900 px-3 py-1 text-xs text-white"
                  onClick={() => void runSearch()}
                  disabled={
                    searchQ.trim().length < 1 ||
                    (searchScope === "session" && !searchSessionId.trim())
                  }
                >
                  {t("llmWiki.runSearch")}
                </button>
              </div>
              {searchErr ? <p className="text-rose-600">{searchErr}</p> : null}
              <ul className="divide-y divide-slate-100 rounded border border-slate-200 bg-slate-50">
                {searchResults.map((row, idx) => {
                  const lp = row.logical_path ?? "";
                  return (
                    <li key={lp ? lp : `s-${idx}`} className="cursor-pointer px-3 py-2 hover:bg-white">
                      <button
                        type="button"
                        className="w-full text-left text-blue-800 hover:underline"
                        disabled={!lp}
                        onClick={() => {
                          if (lp) {
                            setSelectedPath(lp);
                            setMainTab("preview");
                          }
                        }}
                      >
                        {lp || "(unknown)"}
                      </button>
                    </li>
                  );
                })}
                {searchResults.length === 0 && !searchErr ? (
                  <li className="px-3 py-4 text-slate-500">{t("llmWiki.searchEmpty")}</li>
                ) : null}
              </ul>
            </div>
          ) : null}
        </div>
      </main>
    </div>
  );
}
