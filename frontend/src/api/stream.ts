/**
 * SSE 客户端：`POST /api/agent/run` 无法用 EventSource（它只支持 GET），
 * 因此用 fetch + ReadableStream 手工解析 SSE 帧。
 *
 * 解析要点：
 * - 帧以空行（\n\n）分隔，同一帧内 `event:` 与 `data:` 可多行；
 * - 只认 `data` 里的 JSON，`event` 名与 `data.type` 一致（契约 2.1）；
 * - heartbeat 事件用于保活，不打断流。
 */
import { authHeaders } from '@/lib/auth'
import type { RunEvent, RunRequest } from '@/types'

export interface StreamHandlers {
  onEvent: (evt: RunEvent) => void
  onError?: (err: Error) => void
  onClose?: () => void
}

export interface StreamController {
  abort: () => void
  done: Promise<void>
}

function parseFrame(frame: string): RunEvent | null {
  const dataLines: string[] = []
  for (const raw of frame.split('\n')) {
    const line = raw.trimEnd()
    if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart())
  }
  if (!dataLines.length) return null
  const payload = dataLines.join('\n')
  if (!payload || payload === '[DONE]') return null
  try {
    return JSON.parse(payload) as RunEvent
  } catch {
    return null // 半帧/脏帧直接丢弃，不打断整条流
  }
}

export function runAgentStream(req: RunRequest, handlers: StreamHandlers): StreamController {
  const controller = new AbortController()

  const done = (async () => {
    try {
      const res = await fetch('/api/agent/run', {
        method: 'POST',
        // SSE 走 fetch，与 REST 同样是普通请求，因此带标准的 Authorization 头（契约 §7）
        headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream', ...authHeaders() },
        body: JSON.stringify(req),
        signal: controller.signal,
      })
      if (!res.ok || !res.body) {
        let detail = `${res.status} ${res.statusText}`
        try {
          const data = await res.json()
          detail = data?.detail || detail
        } catch {
          /* 非 JSON 响应保留状态行 */
        }
        throw new Error(detail)
      }

      const reader = res.body.getReader()
      const decoder = new TextDecoder('utf-8')
      let buffer = ''

      for (;;) {
        const { value, done: finished } = await reader.read()
        if (finished) break
        buffer += decoder.decode(value, { stream: true })
        // 兼容 \r\n\r\n 与 \n\n 两种分隔
        let idx = buffer.search(/\r?\n\r?\n/)
        while (idx !== -1) {
          const frame = buffer.slice(0, idx)
          buffer = buffer.slice(idx + (buffer[idx] === '\r' ? 4 : 2))
          const evt = parseFrame(frame)
          if (evt && evt.type !== 'heartbeat') handlers.onEvent(evt)
          idx = buffer.search(/\r?\n\r?\n/)
        }
      }
      const tail = parseFrame(buffer)
      if (tail) handlers.onEvent(tail)
    } catch (err) {
      if ((err as Error).name === 'AbortError') return
      handlers.onError?.(err as Error)
    } finally {
      handlers.onClose?.()
    }
  })()

  return { abort: () => controller.abort(), done }
}
