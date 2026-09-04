import { useState } from "react";
import { ApiError, get, post, put } from "../api/client";

export interface Provider {
  id: number;
  kind: "llm" | "embedding" | "rerank";
  provider: string;
  base_url: string;
  model: string;
  api_key: string | null;
  is_default: boolean;
  params: Record<string, unknown>;
}

const KIND_LABEL: Record<Provider["kind"], string> = {
  llm: "LLM",
  embedding: "嵌入",
  rerank: "重排",
};

export default function ProviderForm({
  kind,
  provider,
  onSaved,
}: {
  kind: Provider["kind"];
  provider: Provider | null;
  onSaved: () => void;
}) {
  const [baseUrl, setBaseUrl] = useState(provider?.base_url ?? "");
  const [model, setModel] = useState(provider?.model ?? "");
  const [apiKey, setApiKey] = useState("");
  const [isDefault, setIsDefault] = useState(provider?.is_default ?? false);
  const [models, setModels] = useState<string[]>([]);
  const [testResult, setTestResult] = useState<{ ok: boolean; text: string } | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const refreshModels = async () => {
    setError("");
    setModels([]);
    if (!baseUrl.trim()) {
      setError("请先填写 Base URL");
      return;
    }
    try {
      const params = new URLSearchParams({ base_url: baseUrl.trim() });
      if (apiKey.trim()) params.set("api_key", apiKey.trim());
      const data = await get<{ models: string[] }>(`/api/settings/models?${params}`);
      setModels(data.models);
      if (data.models.length === 0) setError("服务未返回模型列表");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "获取模型列表失败");
    }
  };

  const testConnection = async () => {
    setError("");
    setTestResult(null);
    try {
      const started = performance.now();
      const res = await post<{ ok: boolean; dim?: number }>("/api/settings/providers/test", {
        kind,
        base_url: baseUrl.trim(),
        model: model.trim(),
        api_key: apiKey.trim() || undefined,
      });
      const ms = Math.round(performance.now() - started);
      const extra =
        kind === "embedding" && res.dim != null ? `，维度 ${res.dim}` : "";
      setTestResult({ ok: true, text: `连接成功${extra}，耗时 ${ms}ms` });
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : "测试失败";
      setTestResult({ ok: false, text: msg });
    }
  };

  const save = async () => {
    setError("");
    setSaving(true);
    const body = {
      kind,
      base_url: baseUrl.trim(),
      model: model.trim(),
      api_key: apiKey.trim() || undefined,
      is_default: isDefault,
      params: provider?.params ?? {},
    };
    try {
      if (provider) await put(`/api/settings/providers/${provider.id}`, body);
      else await post("/api/settings/providers", body);
      onSaved();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-3 rounded border border-gray-200 bg-white p-4">
      <h3 className="text-sm font-semibold text-gray-800">{KIND_LABEL[kind]} 配置</h3>
      {!provider && (
        <p className="text-xs text-gray-400">尚未配置，填写后保存。</p>
      )}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <label className="block text-sm">
          <span className="mb-1 block text-gray-600">Base URL</span>
          <input
            className="w-full rounded border border-gray-300 px-2 py-1.5 text-sm focus:border-blue-500 focus:outline-none"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder="https://api.example.com/v1"
          />
        </label>
        <label className="block text-sm">
          <span className="mb-1 block text-gray-600">API Key（留空保留旧值）</span>
          <input
            type="password"
            className="w-full rounded border border-gray-300 px-2 py-1.5 text-sm focus:border-blue-500 focus:outline-none"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder={provider?.api_key ?? "未设置"}
          />
        </label>
        <label className="block text-sm">
          <span className="mb-1 block text-gray-600">模型</span>
          <div className="flex gap-2">
            {models.length > 0 ? (
              <select
                className="w-full rounded border border-gray-300 px-2 py-1.5 text-sm"
                value={model}
                onChange={(e) => setModel(e.target.value)}
              >
                <option value="">请选择模型</option>
                {models.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            ) : (
              <input
                className="w-full rounded border border-gray-300 px-2 py-1.5 text-sm focus:border-blue-500 focus:outline-none"
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder="模型名称"
              />
            )}
            <button
              type="button"
              className="shrink-0 rounded border border-gray-300 px-2 py-1 text-xs text-gray-700 hover:bg-gray-50"
              onClick={refreshModels}
            >
              刷新模型
            </button>
          </div>
        </label>
        <label className="flex items-center gap-2 pt-6 text-sm text-gray-700">
          <input
            type="checkbox"
            checked={isDefault}
            onChange={(e) => setIsDefault(e.target.checked)}
          />
          设为默认
        </label>
      </div>
      <div className="flex items-center gap-3">
        <button
          className="rounded bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
          disabled={saving || !baseUrl.trim() || !model.trim()}
          onClick={save}
        >
          {saving ? "保存中…" : "保存"}
        </button>
        <button
          className="rounded border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50"
          onClick={testConnection}
        >
          测试连接
        </button>
        {testResult && (
          <span className={`text-sm ${testResult.ok ? "text-green-600" : "text-red-600"}`}>
            {testResult.text}
          </span>
        )}
      </div>
      {error && <p className="text-sm text-red-600">{error}</p>}
    </div>
  );
}
