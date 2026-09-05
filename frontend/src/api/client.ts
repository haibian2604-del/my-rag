export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, {
    credentials: "include",
    headers:
      init?.body && !(init.body instanceof FormData)
        ? { "Content-Type": "application/json", ...init?.headers }
        : init?.headers,
    ...init,
  });
  if (resp.status === 204) return undefined as T;
  if (!resp.ok) {
    // 会话失效时通知应用切换到登录页（登录接口本身除外）
    if (resp.status === 401 && !path.startsWith("/api/auth/")) {
      window.dispatchEvent(new CustomEvent("rag:unauthorized"));
    }
    let detail = `HTTP ${resp.status}`;
    try {
      const data = await resp.json();
      if (data?.detail) detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } catch {
      /* ignore */
    }
    throw new ApiError(resp.status, detail);
  }
  return (await resp.json()) as T;
}

export const get = <T>(path: string) => request<T>(path);
export const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
export const put = <T>(path: string, body: unknown) =>
  request<T>(path, { method: "PUT", body: JSON.stringify(body) });
export const del = (path: string) => request<void>(path, { method: "DELETE" });

export function uploadFiles(wsId: number, files: File[]): Promise<unknown> {
  const form = new FormData();
  for (const f of files) form.append("file", f);
  return request(`/api/workspaces/${wsId}/documents`, {
    method: "POST",
    body: form,
  });
}

export interface Workspace {
  id: number;
  name: string;
  description: string;
}

let wsPromise: Promise<Workspace> | null = null;

/** 获取默认工作区；不存在则创建 "默认"（并发安全，撞名后回读）。 */
export function ensureDefaultWorkspace(): Promise<Workspace> {
  wsPromise ??= (async () => {
    try {
      const list = await get<Workspace[]>("/api/workspaces");
      if (list.length > 0) return list[0];
      try {
        return await post<Workspace>("/api/workspaces", { name: "默认", description: "" });
      } catch {
        // 并发创建撞唯一名：重新拉取
        const list2 = await get<Workspace[]>("/api/workspaces");
        if (list2.length > 0) return list2[0];
        throw new ApiError(409, "创建默认工作区失败");
      }
    } catch (e) {
      wsPromise = null; // 失败不缓存，允许重试
      throw e;
    }
  })();
  return wsPromise;
}
