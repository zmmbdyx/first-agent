/* sse.js — SSE 流式客户端。
   后端为 POST + StreamingResponse（EventSource 不支持 POST），
   本模块用 fetch + ReadableStream 解析 text/event-stream，语义与 EventSource 对齐。 */

/**
 * 发送一条消息并逐事件回调。
 * @param {{session_id:string, message:string, resume_path:string}} payload
 * @param {(evt:object)=>void} onEvent 每个 SSE 事件（JSON 对象）
 * @param {AbortSignal} [signal] 中断支持
 * @throws {Error} HTTP 非 2xx 或网络失败
 */
export async function chatStream(payload, onEvent, signal) {
  const resp = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.error || `HTTP ${resp.status}`);
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const parts = buf.split("\n\n");
    buf = parts.pop();
    for (const part of parts) {
      const line = part.trim();
      if (!line.startsWith("data:")) continue;
      try {
        onEvent(JSON.parse(line.slice(5).trim()));
      } catch (e) {
        console.warn("[sse] 事件解析失败", e);
      }
    }
  }
}

/** 拉取会话快照（历史恢复） */
export async function fetchSession(sessionId) {
  if (!sessionId) return null;
  const r = await fetch("/api/session/" + sessionId).catch(() => null);
  return r && r.ok ? await r.json() : null;
}

/** 健康检查 */
export async function fetchHealth() {
  try {
    const r = await fetch("/api/health");
    return r.ok ? await r.json() : null;
  } catch {
    return null;
  }
}
