import { useEffect, useState } from "react";
import { del, get, post, put, type Conversation, type Workspace } from "../api/client";
import ConfirmDialog from "./ConfirmDialog";

/** 侧栏工作区树：全部工作区平铺展示，点击切换，当前工作区下方展开会话记录。 */
export default function WorkspaceSwitcher({
  workspace,
  onSwitch,
  conversations,
  activeConvId,
  onSelectConversation,
  onNewConversation,
  onDeleteConversation,
}: {
  workspace: Workspace;
  onSwitch: (ws: Workspace) => void;
  conversations: Conversation[];
  activeConvId: number | null;
  onSelectConversation: (id: number) => void;
  onNewConversation: () => void;
  onDeleteConversation: (conv: Conversation) => void;
}) {
  const [list, setList] = useState<Workspace[]>([]);
  const [error, setError] = useState("");
  const [renamingId, setRenamingId] = useState<number | null>(null);
  const [renameText, setRenameText] = useState("");
  const [delTarget, setDelTarget] = useState<Workspace | null>(null);
  // 展开会话记录的工作区；默认展开当前工作区
  const [expandedId, setExpandedId] = useState<number | null>(workspace.id);

  useEffect(() => {
    setExpandedId(workspace.id); // 切换工作区时自动展开新的当前项
  }, [workspace.id]);

  const refresh = async () => {
    try {
      setList(await get<Workspace[]>("/api/workspaces"));
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

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
    setDelTarget(null);
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
    <div className="flex min-h-0 flex-1 flex-col px-2 pb-1">
      <div className="flex items-center justify-between px-2 pb-1">
        <span className="text-xs text-faint">工作区</span>
        <button
          className="text-xs text-faint transition-colors hover:text-ink"
          onClick={() => void create()}
          title="新建工作区"
        >
          ＋新建
        </button>
      </div>
      {error && <p className="px-2 py-1 text-xs text-seal">{error}</p>}
      <ul className="min-h-0 flex-1 space-y-0.5 overflow-y-auto">
        {list.map((ws) => {
          const active = ws.id === workspace.id;
          const expanded = ws.id === expandedId;
          return (
            <li key={ws.id}>
              <div
                className={`group flex items-center gap-1.5 rounded-md px-2 py-1.5 ${
                  active ? "bg-iblue-soft text-iblue" : "hover:bg-paper"
                }`}
              >
                <span className="shrink-0" aria-hidden="true">
                  <svg
                    width="14"
                    height="14"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z" />
                  </svg>
                </span>
                {renamingId === ws.id ? (
                  <input
                    autoFocus
                    className="input w-28 px-1.5 py-0.5 text-xs"
                    value={renameText}
                    onChange={(e) => setRenameText(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") void rename(ws, renameText);
                      if (e.key === "Escape") setRenamingId(null);
                    }}
                    onBlur={() => void rename(ws, renameText)}
                  />
                ) : (
                  <button
                    className="min-w-0 flex-1 truncate text-left text-sm"
                    onClick={() => {
                      if (!active) {
                        onSwitch(ws);
                        setExpandedId(ws.id);
                      } else {
                        setExpandedId((v) => (v === ws.id ? null : ws.id)); // 点击当前工作区：展开/收起
                      }
                    }}
                    title={ws.name}
                  >
                    {ws.name}
                  </button>
                  <button
                    className="shrink-0 rounded p-0.5 text-faint transition-colors hover:text-ink"
                    onClick={(e) => {
                      e.stopPropagation();
                      setExpandedId((v) => (v === ws.id ? null : ws.id));
                    }}
                    title={expanded ? "收起" : "展开"}
                  >
                    <svg
                      width="12"
                      height="12"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      aria-hidden="true"
                    >
                      {expanded ? <polyline points="18 15 12 9 6 15" /> : <polyline points="6 9 12 15 18 9" />}
                    </svg>
                  </button>
                )}
                <button
                  className="shrink-0 rounded p-0.5 text-faint opacity-0 transition-opacity hover:text-ink group-hover:opacity-100"
                  onClick={() => {
                    setRenamingId(ws.id);
                    setRenameText(ws.name);
                  }}
                  title="重命名"
                >
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
                  </svg>
                </button>
                {list.length > 1 && (
                  <button
                    className="shrink-0 rounded px-0.5 text-xs text-faint opacity-0 transition-opacity hover:text-seal group-hover:opacity-100"
                    onClick={() => setDelTarget(ws)}
                    title="删除"
                  >
                    删
                  </button>
                )}
              </div>

              {active && expanded && (
                <ul className="mb-1 mt-0.5 space-y-0.5 pl-4">
                  <li>
                    <button
                      className="w-full truncate rounded-md px-2 py-1 text-left text-xs text-faint transition-colors hover:bg-paper hover:text-ink"
                      onClick={onNewConversation}
                    >
                      ＋ 新建对话
                    </button>
                  </li>
                  {conversations.length === 0 && (
                    <li className="px-2 py-1 text-xs text-faint">暂无会话</li>
                  )}
                  {conversations.map((c) => (
                    <li key={c.id} className="group relative">
                      <button
                        className={`w-full truncate rounded-md px-2 py-1.5 pr-7 text-left text-sm transition-colors ${
                          activeConvId === c.id
                            ? "bg-iblue-soft font-medium text-iblue"
                            : "text-ink hover:bg-paper"
                        }`}
                        onClick={() => onSelectConversation(c.id)}
                        title={c.title || `会话 #${c.id}`}
                      >
                        {c.title || `会话 #${c.id}`}
                      </button>
                      <button
                        className="absolute right-1.5 top-1.5 hidden text-xs text-seal group-hover:block"
                        onClick={() => onDeleteConversation(c)}
                        title="删除会话"
                      >
                        删除
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </li>
          );
        })}
      </ul>

      <ConfirmDialog
        open={delTarget !== null}
        title="删除工作区"
        message={delTarget ? `删除工作区「${delTarget.name}」将同时删除其全部文档与对话，确定？` : ""}
        confirmText="删除"
        danger
        onConfirm={() => delTarget && void remove(delTarget)}
        onCancel={() => setDelTarget(null)}
      />
    </div>
  );
}
