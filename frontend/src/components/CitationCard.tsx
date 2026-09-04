import type { Citation } from "../api/sse";

export type { Citation };

export function CitationCard({ citation }: { citation: Citation }) {
  return (
    <div className="rounded border border-gray-200 bg-white px-3 py-2 text-sm shadow-sm">
      <div className="flex items-baseline gap-2">
        <span className="rounded bg-gray-200 px-1.5 text-xs font-semibold text-gray-700">
          [{citation.n}]
        </span>
        <span className="font-medium text-gray-900">{citation.filename}</span>
        {citation.heading_path && (
          <span className="text-xs text-gray-500">{citation.heading_path}</span>
        )}
        {citation.page_no != null && (
          <span className="ml-auto text-xs text-gray-400">第 {citation.page_no} 页</span>
        )}
      </div>
      {citation.snippet && (
        <p className="mt-1 line-clamp-3 text-xs text-gray-600">{citation.snippet}</p>
      )}
    </div>
  );
}
