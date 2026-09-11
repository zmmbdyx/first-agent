/**
 * WebSocket 客户端：接收工具调用状态与实时指标的推送（与 SSE 并行的第二条通道）。
 * 设计原则：WS 只是增强——连不上或断开都不得影响 SSE 主链路，因此全部错误静默处理。
 */
import { withAuth } from '@/lib/auth'
import type { RunEvent } from '@/types'

export interface SocketHandlers {
  onEvent: (evt: RunEvent) => void
  onOpen?: () => void
  onClose?: () => void
}

export class RunSocket {
  private ws: WebSocket | null = null
  private sessionId = ''
  private handlers: SocketHandlers | null = null
  private retry = 0
  private pingTimer: number | null = null
  private retryTimer: number | null = null
  private closedByUser = false

  connect(sessionId: string, handlers: SocketHandlers) {
    this.close()
    if (!sessionId) return
    this.sessionId = sessionId
    this.handlers = handlers
    this.closedByUser = false
    this.open()
  }

  private open() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    try {
      // WebSocket 构造器不支持自定义请求头，令牌只能走查询参数（withAuth 负责拼接）；
      // 每次重连重新取令牌，用户在设置里补填令牌后无需刷新页面即可生效
      this.ws = new WebSocket(withAuth(`${proto}://${location.host}/ws/agent/${this.sessionId}`))
    } catch {
      this.scheduleRetry()
      return
    }

    this.ws.onopen = () => {
      this.retry = 0
      this.handlers?.onOpen?.()
      this.pingTimer = window.setInterval(() => {
        try {
          this.ws?.send(JSON.stringify({ type: 'ping' }))
        } catch {
          /* 忽略：下次心跳或重连会恢复 */
        }
      }, 25_000)
    }

    this.ws.onmessage = (ev: MessageEvent<string>) => {
      try {
        const evt = JSON.parse(ev.data) as RunEvent
        if (evt.type === 'heartbeat') return
        this.handlers?.onEvent(evt)
      } catch {
        /* 非 JSON 帧忽略 */
      }
    }

    this.ws.onclose = () => {
      this.clearTimers()
      this.handlers?.onClose?.()
      if (!this.closedByUser) this.scheduleRetry()
    }

    this.ws.onerror = () => {
      /* onclose 会随后触发重连，这里不重复处理 */
    }
  }

  private scheduleRetry() {
    if (this.closedByUser) return
    // 指数退避，上限 15s：后端未启动时避免刷屏
    const delay = Math.min(1000 * 2 ** this.retry, 15_000)
    this.retry += 1
    this.retryTimer = window.setTimeout(() => this.open(), delay)
  }

  private clearTimers() {
    if (this.pingTimer !== null) window.clearInterval(this.pingTimer)
    if (this.retryTimer !== null) window.clearTimeout(this.retryTimer)
    this.pingTimer = null
    this.retryTimer = null
  }

  close() {
    this.closedByUser = true
    this.clearTimers()
    try {
      this.ws?.close()
    } catch {
      /* 已断开 */
    }
    this.ws = null
  }
}
