import { useState } from "react";
import type { Citation } from "../api/sse";

export type { Citation };

export function CitationCard({ citation }: { citation: Citation }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="rounded-lg border border-line bg-card px-3 py-2 text-[13px] leading-5">
      <div className="flex items-baseline gap-2">
        <span className="inline-flex h-5 min-w-5 shrink-0 items-center justify-center rounded bg-seal px-1 text-xs font-medium text-white">
          {citation.n}
        </span>
        <span className="truncate font-medium">{citation.filename}</span>
        {citation.heading_path && (
          <span className="truncate text-faint">{citation.heading_path}</span>
        )}
        {citation.page_no != null && (
          <span className="ml-auto shrink-0 text-faint">第 {citation.page_no} 页</span>
        )}
      </div>
      {citation.snippet && (
        <p
          role="button"
          tabIndex={0}
          onClick={() => setExpanded((v) => !v)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") setExpanded((v) => !v);
          }}
          className={`mt-1 cursor-pointer text-faint ${expanded ? "" : "line-clamp-2"}`}
          title={expanded ? "收起" : "点击展开原文"}
        >
          {citation.snippet}
        </p>
      )}
    </div>
  );
}
