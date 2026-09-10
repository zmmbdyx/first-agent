/**
 * 运行编排：把输入区的一次「发送」变成一条 SSE 事件流 + 一条 WebSocket 补充通道，
 * 并把事件喂给 useRunStore。中断时先 abort 流再调用后端 interrupt 接口，
 * 保证服务端图状态也一起停下（否则后台会继续跑完并写库）。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import { runAgentStream, type StreamController } from '@/api/stream'
import { RunSocket } from '@/api/ws'
import { useAppStore } from '@/store/useAppStore'
import { useRunStore } from '@/store/useRunStore'

export interface UseAgentRun {
  running: boolean
  start: (task: string, opts?: { resume?: boolean }) => void
  stop: () => void
  rollback: () => void
}

export function useAgentRun(): UseAgentRun {
  const [running, setRunning] = useState(false)
  const queryClient = useQueryClient()
  const streamRef = useRef<StreamController | null>(null)
  const socketRef = useRef<RunSocket | null>(null)
  const runIdRef = useRef<string>('')

  const applyEvent = useRunStore((s) => s.applyEvent)
  const appendUserMessage = useRunStore((s) => s.appendUserMessage)
  const resetRun = useRunStore((s) => s.reset)
  const setStatus = useRunStore((s) => s.setStatus)

  useEffect(() => () => {
    streamRef.current?.abort()
    socketRef.current?.close()
  }, [])

  const start = useCallback((task: string, opts?: { resume?: boolean }) => {
    const text = task.trim()
    if (!text || streamRef.current) return

    const app = useAppStore.getState()
    const sessionId = app.activeSessionId ?? ''

    appendUserMessage(text)
    setStatus('running')
    setRunning(true)

    streamRef.current = runAgentStream(
      {
        task: text,
        session_id: sessionId,
        preset: app.preset,
        workspace: app.workspace,
        permission_mode: app.permissionMode,
        model: app.model || undefined,
        reasoning_effort: app.reasoningEffort,
        resume: opts?.resume ?? false,
      },
      {
        onEvent: (evt) => {
          if (evt.type === 'run_started') {
            runIdRef.current = evt.run_id
            // 新建会话时后端会回传 session_id，前端据此切换并建立 WS
            if (evt.session_id && evt.session_id !== useAppStore.getState().activeSessionId) {
              useAppStore.getState().setActiveSession(evt.session_id)
            }
            const sid = evt.session_id || useAppStore.getState().activeSessionId
            if (sid) {
              socketRef.current?.close()
              socketRef.current = new RunSocket()
              socketRef.current.connect(sid, { onEvent: applyEvent })
            }
          }
          if (evt.type === 'session_info' && evt.session_id) {
            if (evt.session_id !== useAppStore.getState().activeSessionId) {
              useAppStore.getState().setActiveSession(evt.session_id)
            }
          }
          applyEvent(evt)
        },
        onError: (err) => {
          applyEvent({ type: 'error', message: err.message })
        },
        onClose: () => {
          streamRef.current = null
          setRunning(false)
          const status = useRunStore.getState().status
          if (status === 'running') setStatus('done')
          // 运行结束后让会话列表与历史失效：标题/更新时间/落库轨迹都需要重新拉取
          const sid = useAppStore.getState().activeSessionId
          queryClient.invalidateQueries({ queryKey: ['sessions'] })
          if (sid) queryClient.invalidateQueries({ queryKey: ['session-history', sid] })
        },
      },
    )
  }, [applyEvent, appendUserMessage, setStatus, queryClient])

  const stop = useCallback(() => {
    const runId = runIdRef.current
    streamRef.current?.abort()
    streamRef.current = null
    setRunning(false)
    setStatus('interrupted')
    if (runId) {
      // 后端可能已经跑完，404/409 都属于正常竞态，静默即可
      api.interrupt(runId).catch(() => undefined)
    }
  }, [setStatus])

  const rollback = useCallback(() => {
    const sid = useAppStore.getState().activeSessionId
    if (!sid) return
    api.rollback(sid)
      .then((res) => {
        applyEvent({ type: 'rolled_back', checkpoint_id: res.checkpoint_id ?? '' })
        resetRun(sid)
      })
      .catch((err: Error) => applyEvent({ type: 'error', message: `回滚失败：${err.message}` }))
  }, [applyEvent, resetRun])

  return { running, start, stop, rollback }
}
