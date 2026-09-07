import { useState } from "react";

/** 一条工具调用记录：工具名 + 参数 + 结果预览（与后端 messages.trace 同构） */
export interface ToolStep {
  tool: string;
  args?: unknown;
  preview?: string;
}

/** 参数摘要：JSON 序列化后截断，避免长参数撑爆卡片 */
function summarizeArgs(args: unknown): string {
  if (args == null) return "";
  let s: string;
  try {
    s = JSON.stringify(args);
  } catch {
    return "";
  }
  return s.length > 80 ? `${s.slice(0, 80)}…` : s;
}

/**
 * 工具调用时间线：紧凑卡片列表，默认折叠，点击展开。
 * 视觉与 CitationCard 一致（纸墨令牌：line 边框、card/paper 底色、faint 辅助文字）。
 */
export function ToolTimeline({
  steps,
  defaultOpen = false,
}: {
  steps: ToolStep[];
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  if (steps.length === 0) return null;

  return (
    <div className="mb-2 rounded-lg border border-line bg-paper/60">
      <button
        type="button"
        className="flex w-full items-center gap-1.5 px-3 py-1.5 text-left text-xs text-faint transition-colors hover:text-ink"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className="w-3 shrink-0" aria-hidden>
          {open ? "▾" : "▸"}
        </span>
        <span>工具调用 · {steps.length} 次</span>
      </button>
      {open && (
        <ul className="space-y-1.5 px-3 pb-2">
          {steps.map((s, i) => {
            const argsSummary = summarizeArgs(s.args);
            return (
              <li
                key={i}
                className="rounded border border-line bg-card px-2.5 py-1.5"
              >
                <p className="font-mono text-xs font-semibold text-iblue">
                  {s.tool}
                </p>
                {argsSummary && (
                  <p className="mt-0.5 break-all font-mono text-xs text-faint">
                    {argsSummary}
                  </p>
                )}
                {s.preview && (
                  <p
                    className="mt-0.5 line-clamp-3 break-all text-xs leading-5 text-faint"
                    title={s.preview}
                  >
                    {s.preview}
                  </p>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
