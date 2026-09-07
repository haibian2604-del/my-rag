import { useEffect, useRef, useState } from "react";
import { del, get, post, put, type Workspace } from "../api/client";

/** 侧栏底部工作区切换器：点击展开浮层，支持切换/新建/重命名/删除。 */
export default function WorkspaceSwitcher({
  workspace,
  onSwitch,
}: {
  workspace: Workspace;
  onSwitch: (ws: Workspace) => void;
}) {
  const [open, setOpen] = useState(false);
  const [list, setList] = useState<Workspace[]>([]);
  const [error, setError] = useState("");
  const [renamingId, setRenamingId] = useState<number | null>(null);
  const [renameText, setRenameText] = useState("");
  const boxRef = useRef<HTMLDivElement>(null);

  const refresh = async () => {
    try {
      setList(await get<Workspace[]>("/api/workspaces"));
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    }
  };

  useEffect(() => {
    if (open) void refresh();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [open]);

  const create = async () => {
    // 默认名「工作区 N」；重名则继续追加序号
    const taken = new Set(list.map((w) => w.name));
    let n = list.length + 1;
    while (taken.has(`工作区 ${n}`)) n += 1;
    const name = `工作区 ${n}`;
    try {
      const ws = await post<Workspace>("/api/workspaces", { name, description: "" });
      await refresh();
      onSwitch(ws);
    } catch (e) {
      setError(e instanceof Error ? e.message : "新建失败");
    }
  };

  const rename = async (ws: Workspace, name: string) => {
    const trimmed = name.trim();
    if (!trimmed || trimmed === ws.name) {
      setRenamingId(null);
      return;
    }
    try {
      await put(`/api/workspaces/${ws.id}`, { name: trimmed, description: ws.description });
      const updated = { ...ws, name: trimmed };
      setRenamingId(null);
      await refresh();
      if (ws.id === workspace.id) onSwitch(updated); // 触发 App 刷新当前工作区名
    } catch (e) {
      setError(e instanceof Error ? e.message : "重命名失败");
    }
  };

  const remove = async (ws: Workspace) => {
    if (!window.confirm(`删除工作区「${ws.name}」将同时删除其全部文档与对话，确定？`)) return;
    try {
      await del(`/api/workspaces/${ws.id}`);
      const fresh = await get<Workspace[]>("/api/workspaces");
      setList(fresh);
      if (ws.id === workspace.id && fresh[0]) onSwitch(fresh[0]); // 当前工作区被删：切到剩余第一个
    } catch (e) {
      setError(e instanceof Error ? e.message : "删除失败");
    }
  };

  return (
    <div ref={boxRef} className="relative mt-auto border-t border-line px-3 py-2.5">
      <button
        className="flex w-full items-center gap-1.5 truncate text-left text-xs text-faint hover:text-ink"
        onClick={() => setOpen((v) => !v)}
        title={workspace.name}
      >
        <span className="truncate">{workspace.name}</span>
        <span className="ml-auto shrink-0 text-[10px]">{open ? "▾" : "▴"}</span>
      </button>

      {open && (
        <div className="panel absolute bottom-full left-0 z-20 mb-2 w-56 rounded-lg p-1.5 text-sm shadow-lg">
          {error && <p className="px-2 py-1 text-xs text-seal">{error}</p>}
          <ul className="max-h-56 overflow-y-auto">
            {list.map((ws) => (
              <li
                key={ws.id}
                className={`group flex items-center gap-1 rounded-md px-2 py-1.5 ${
                  ws.id === workspace.id ? "bg-iblue-soft text-iblue" : "hover:bg-iblue-soft/60"
                }`}
              >
                <button
                  className="min-w-0 flex-1 truncate text-left"
                  onClick={() => {
                    onSwitch(ws);
                    setOpen(false);
                  }}
                  title={ws.name}
                >
                  {ws.name}
                </button>
                {renamingId === ws.id ? (
                  <input
                    autoFocus
                    className="input w-28 px-1.5 py-0.5 text-xs"
                    value={renameText}
                    onClick={(e) => e.stopPropagation()}
                    onChange={(e) => setRenameText(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") void rename(ws, renameText);
                      if (e.key === "Escape") setRenamingId(null);
                    }}
                    onBlur={() => void rename(ws, renameText)}
                  />
                ) : (
                  <button
                    className="shrink-0 rounded px-1 text-faint opacity-0 hover:text-ink group-hover:opacity-100"
                    onClick={(e) => {
                      e.stopPropagation();
                      setRenamingId(ws.id);
                      setRenameText(ws.name);
                    }}
                    title="重命名"
                  >
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                      <path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
                    </svg>
                  </button>
                )}
                {list.length > 1 && (
                  <button
                    className="shrink-0 rounded px-1 text-xs text-faint opacity-0 hover:text-seal group-hover:opacity-100"
                    onClick={(e) => {
                      e.stopPropagation();
                      void remove(ws);
                    }}
                    title="删除"
                  >
                    删
                  </button>
                )}
              </li>
            ))}
          </ul>
          <button
            className="mt-1 w-full rounded-md px-2 py-1.5 text-left text-faint hover:bg-iblue-soft/60 hover:text-ink"
            onClick={() => void create()}
          >
            ＋ 新建工作区
          </button>
        </div>
      )}
    </div>
  );
}
