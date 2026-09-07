import { useEffect, useState } from "react";
import { ApiError, get, put, type Workspace } from "../api/client";
import EmbeddingSwitcher from "../components/EmbeddingSwitcher";
import ConfirmDialog from "../components/ConfirmDialog";
import ProviderForm, { type Provider } from "../components/ProviderForm";

const KINDS = [
  { key: "llm", label: "LLM", hint: "用来生成回答的模型。本地 oMLX 一般是 http://localhost:19723/v1（容器内用 http://host.docker.internal:19723/v1）。" },
  { key: "embedding", label: "嵌入", hint: "把文档和问题转成向量用于检索。中文场景推荐多语言模型（如 bge-m3）。" },
  { key: "rerank", label: "重排", hint: "可选。对检索结果做二次排序，M3 版本支持。" },
] as const;

interface WorkspaceSettings {
  top_k: number;
  score_threshold: number;
  use_rerank: boolean;
  use_hybrid: boolean;
  context_max_tokens: number;
}

export default function SettingsPage({ workspace }: { workspace: Workspace }) {
  const [tab, setTab] = useState<(typeof KINDS)[number]["key"]>("llm");
  const [providers, setProviders] = useState<Provider[]>([]);
  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [pwMsg, setPwMsg] = useState("");
  const [pwMsgError, setPwMsgError] = useState(false);
  const [wsSettings, setWsSettings] = useState<WorkspaceSettings | null>(null);
  const [wsMsg, setWsMsg] = useState("");
  const [mcpEnabled, setMcpEnabled] = useState<boolean | null>(null);
  const [mcpMsg, setMcpMsg] = useState("");
  const [mcpConfirm, setMcpConfirm] = useState(false);
  const mcpUrl = `${window.location.origin}/mcp`;

  const refreshProviders = async () => {
    setProviders(await get<Provider[]>("/api/settings/providers"));
  };
  const refreshWs = async () => {
    setWsSettings(await get<WorkspaceSettings>(`/api/workspaces/${workspace.id}/settings`));
  };
  const refreshMcp = async () => {
    setMcpEnabled((await get<{ enabled: boolean }>("/api/mcp/settings")).enabled);
  };

  useEffect(() => {
    void refreshProviders().catch(() => undefined);
    void refreshWs().catch(() => undefined);
    void refreshMcp().catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspace.id]);

  const savePassword = async () => {
    setPwMsg("");
    setPwMsgError(false);
    if (newPassword !== confirmPassword) {
      setPwMsg("两次输入的新密码不一致");
      setPwMsgError(true);
      return;
    }
    try {
      await put("/api/auth/password", {
        old_password: oldPassword,
        new_password: newPassword,
      });
      setOldPassword("");
      setNewPassword("");
      setConfirmPassword("");
      setPwMsg("密码已更新");
    } catch (e) {
      setPwMsg(e instanceof ApiError ? e.message : "保存失败");
      setPwMsgError(true);
    }
  };

  const saveWs = async () => {
    if (!wsSettings) return;
    setWsMsg("");
    try {
      await put(`/api/workspaces/${workspace.id}/settings`, wsSettings);
      setWsMsg("已保存");
    } catch (e) {
      setWsMsg(e instanceof ApiError ? e.message : "保存失败");
    }
  };

  const currentProvider = providers.find((p) => p.kind === tab) ?? null;
  const activeKind = KINDS.find((k) => k.key === tab)!;

  const toggleMcp = async (next: boolean) => {
    setMcpConfirm(false);
    setMcpMsg("");
    try {
      const r = await put<{ enabled: boolean }>("/api/mcp/settings", { enabled: next });
      setMcpEnabled(r.enabled);
      setMcpMsg(r.enabled ? "MCP 服务已开启" : "MCP 服务已关闭");
    } catch (e) {
      setMcpMsg(e instanceof ApiError ? e.message : "操作失败：请确认后端已更新并重启");
    }
  };

  const copyMcpUrl = async () => {
    try {
      await navigator.clipboard.writeText(mcpUrl);
      setMcpMsg("端点地址已复制");
    } catch {
      setMcpMsg("复制失败，请手动选中地址复制");
    }
  };

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl px-4 py-6">
      <h1 className="font-display text-lg">设置</h1>

      <section className="mt-6">
        <div className="flex gap-1 border-b border-line pb-3">
          {KINDS.map((k) => (
            <button
              key={k.key}
              className={`rounded-md px-3 py-1.5 text-sm transition-colors ${
                tab === k.key
                  ? "bg-iblue-soft font-medium text-iblue"
                  : "text-faint hover:text-ink"
              }`}
              onClick={() => setTab(k.key)}
            >
              {k.label}
            </button>
          ))}
        </div>
        <p className="mt-3 text-xs leading-5 text-faint">{activeKind.hint}</p>
        <div className="mt-3">
          <ProviderForm
            key={`${tab}-${currentProvider?.id ?? "new"}`}
            kind={tab}
            provider={currentProvider}
            onSaved={() => void refreshProviders()}
          />
          {tab === "embedding" && (
            <div className="mt-4">
              <EmbeddingSwitcher />
            </div>
          )}
        </div>
      </section>

      <div className="mt-8 grid grid-cols-1 items-start gap-x-12 gap-y-8 border-t border-line pt-6 lg:grid-cols-2">
        <section className="lg:border-r lg:border-line lg:pr-12">
          <h2 className="font-display text-base">检索参数</h2>
        <p className="mt-1 text-xs text-faint">控制每次回答时如何从文档中取材，改动只影响当前工作区。</p>
        {wsSettings && (
          <>
            <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
              <label className="block text-sm">
                <span className="mb-1 block">每次检索的段落数（1–20）</span>
                <input
                  type="number"
                  min={1}
                  max={20}
                  className="input"
                  value={wsSettings.top_k}
                  onChange={(e) => setWsSettings({ ...wsSettings, top_k: Number(e.target.value) })}
                />
                <span className="mt-1 block text-xs text-faint">取多少段最相关的原文给模型参考</span>
              </label>
              <label className="block text-sm">
                <span className="mb-1 block">相似度阈值（0–1）</span>
                <input
                  type="number"
                  min={0}
                  max={1}
                  step={0.05}
                  className="input"
                  value={wsSettings.score_threshold}
                  onChange={(e) =>
                    setWsSettings({ ...wsSettings, score_threshold: Number(e.target.value) })
                  }
                />
                <span className="mt-1 block text-xs text-faint">
                  低于这个相似度的段落直接丢弃，0 表示不过滤（仅关闭混合检索时生效）
                </span>
              </label>
              <label className="block text-sm">
                <span className="mb-1 block">单次回答的资料上限（500–8000 tokens）</span>
                <input
                  type="number"
                  min={500}
                  max={8000}
                  step={100}
                  className="input"
                  value={wsSettings.context_max_tokens}
                  onChange={(e) =>
                    setWsSettings({ ...wsSettings, context_max_tokens: Number(e.target.value) })
                  }
                />
                <span className="mt-1 block text-xs text-faint">一次回答最多带入多少原文，太大时回答会变慢</span>
              </label>
              <label className="flex items-start gap-2 pt-7 text-sm">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={wsSettings.use_rerank}
                  onChange={(e) => setWsSettings({ ...wsSettings, use_rerank: e.target.checked })}
                />
                <span>
                  启用重排
                  <span className="mt-0.5 block text-xs text-faint">对召回结果做二次排序（当前为简化实现）</span>
                </span>
              </label>
              <label className="flex items-start gap-2 pt-7 text-sm">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={wsSettings.use_hybrid}
                  onChange={(e) => setWsSettings({ ...wsSettings, use_hybrid: e.target.checked })}
                />
                <span>
                  启用混合检索
                  <span className="mt-0.5 block text-xs text-faint">向量召回与全文检索融合排序；关闭后仅用向量召回</span>
                </span>
              </label>
            </div>
            <div className="mt-4 flex items-center gap-3">
              <button className="btn-primary" onClick={() => void saveWs()}>
                保存
              </button>
              {wsMsg && <span className="text-sm text-faint">{wsMsg}</span>}
            </div>
          </>
        )}
      </section>

      <section className="min-w-0">
        <h2 className="font-display text-base">修改密码</h2>
        <p className="mt-1 text-xs leading-5 text-faint">
          修改后当前会话仍然有效，其他设备下次请求时需要用新密码重新登录。
        </p>
        <input
          type="password"
          className="input mt-4 max-w-56"
          value={oldPassword}
          onChange={(e) => setOldPassword(e.target.value)}
          placeholder="旧密码"
        />
        <input
          type="password"
          className="input mt-3 max-w-56"
          value={newPassword}
          onChange={(e) => setNewPassword(e.target.value)}
          placeholder="新密码（至少 6 位）"
        />
        <input
          type="password"
          className="input mt-3 max-w-56"
          value={confirmPassword}
          onChange={(e) => setConfirmPassword(e.target.value)}
          placeholder="确认新密码"
        />
        <div className="mt-4">
          <button
            className="btn-primary"
            disabled={!oldPassword || !newPassword || !confirmPassword}
            onClick={() => void savePassword()}
          >
            更新密码
          </button>
        </div>
        {pwMsg && (
          <p className={`mt-2 text-sm ${pwMsgError ? "text-seal" : "text-faint"}`}>{pwMsg}</p>
        )}
      </section>

      <section className="lg:border-r lg:border-line lg:pr-12">
        <h2 className="font-display text-base">MCP 服务</h2>
        <p className="mt-1 text-xs leading-5 text-faint">
          开启后，外部 Agent（Claude Code、Cursor 等）可通过 MCP 协议（端点 /mcp，HTTP 直连、无需密钥）检索与问答本知识库。
        </p>
        <div className="mt-4 flex items-center gap-3">
          <button
            role="switch"
            aria-checked={mcpEnabled ?? false}
            className={`relative h-6 w-11 rounded-full transition-colors ${mcpEnabled ? "bg-seal" : "bg-line"}`}
            onClick={() => (mcpEnabled ? void toggleMcp(false) : setMcpConfirm(true))}
          >
            <span
              className={`absolute top-0.5 h-5 w-5 rounded-full bg-card shadow transition-all ${mcpEnabled ? "left-[22px]" : "left-0.5"}`}
            />
          </button>
          <span className="text-sm">
            {mcpEnabled === null ? "状态获取失败（请确认后端已更新并重启）" : mcpEnabled ? "已开启" : "已关闭"}
          </span>
        </div>
        <p className="mt-2 text-xs leading-5 text-faint">
          开启即代表允许任何能访问本服务端口的客户端读取知识库，请仅在可信的内网环境开启。
        </p>
        {mcpEnabled && (
          <div className="panel mt-3 flex items-center gap-2 p-2.5 text-sm">
            <span className="text-faint">MCP 端点</span>
            <code className="min-w-0 flex-1 truncate font-mono text-[13px]">{mcpUrl}</code>
            <button className="btn-ghost shrink-0" onClick={() => void copyMcpUrl()}>
              复制
            </button>
          </div>
        )}
        {mcpMsg && <p className="mt-2 text-sm text-faint">{mcpMsg}</p>}
      </section>
      </div>
      </div>


      <ConfirmDialog
        open={mcpConfirm}
        title="开启 MCP 服务"
        message="开启后任何能访问本服务端口的 MCP 客户端都将可以直接连接并读取知识库（无需密钥）。请确保仅在可信的内网环境使用。"
        confirmText="开启"
        onConfirm={() => void toggleMcp(true)}
        onCancel={() => setMcpConfirm(false)}
      />
    </div>
  );
}
