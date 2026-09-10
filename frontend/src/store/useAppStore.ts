/**
 * 全局 UI 状态：布局折叠、主题、运行参数与会话选择。
 * 只放"界面与偏好"，运行期事件归约见 useRunStore。
 */
import { create } from 'zustand'
import type { PermissionMode, PresetId, ReasoningEffort } from '@/types'

export type PanelTab = 'files' | 'tools' | 'tokens' | 'git'

const LS = {
  theme: 'pf.theme',
  sidebar: 'pf.sidebarCollapsed',
  panel: 'pf.panelOpen',
  workspace: 'pf.workspace',
  preset: 'pf.preset',
  permission: 'pf.permission',
  model: 'pf.model',
  effort: 'pf.effort',
  session: 'pf.session',
}

function read<T extends string>(key: string, fallback: T): T {
  try {
    return (localStorage.getItem(key) as T) || fallback
  } catch {
    return fallback
  }
}

function readBool(key: string, fallback: boolean): boolean {
  try {
    const v = localStorage.getItem(key)
    return v === null ? fallback : v === '1'
  } catch {
    return fallback
  }
}

function write(key: string, value: string | boolean) {
  try {
    localStorage.setItem(key, typeof value === 'boolean' ? (value ? '1' : '0') : value)
  } catch {
    /* 隐私模式下 localStorage 可能不可写，忽略 */
  }
}

interface AppState {
  theme: 'dark' | 'light'
  sidebarCollapsed: boolean
  panelOpen: boolean
  panelTab: PanelTab
  settingsOpen: boolean
  compactViewport: boolean

  activeSessionId: string | null
  workspace: string
  preset: PresetId
  permissionMode: PermissionMode
  model: string
  reasoningEffort: ReasoningEffort

  toggleTheme: () => void
  toggleSidebar: () => void
  setSidebarCollapsed: (v: boolean) => void
  togglePanel: (open?: boolean) => void
  setPanelTab: (tab: PanelTab) => void
  setSettingsOpen: (open: boolean) => void
  setViewportCompact: (v: boolean) => void

  setActiveSession: (id: string | null) => void
  setWorkspace: (ws: string) => void
  setPreset: (p: PresetId) => void
  setPermissionMode: (m: PermissionMode) => void
  setModel: (m: string) => void
  setReasoningEffort: (e: ReasoningEffort) => void
}

export const useAppStore = create<AppState>((set, get) => ({
  theme: read<'dark' | 'light'>(LS.theme, 'dark'),
  sidebarCollapsed: readBool(LS.sidebar, false),
  panelOpen: readBool(LS.panel, true),
  panelTab: 'files',
  settingsOpen: false,
  compactViewport: false,

  activeSessionId: read(LS.session, '') || null,
  workspace: read(LS.workspace, 'default'),
  preset: read<PresetId>(LS.preset, 'standard'),
  permissionMode: read<PermissionMode>(LS.permission, 'workspace_write'),
  model: read(LS.model, ''),
  reasoningEffort: read<ReasoningEffort>(LS.effort, 'medium'),

  toggleTheme: () => {
    const theme = get().theme === 'dark' ? 'light' : 'dark'
    document.documentElement.classList.toggle('dark', theme === 'dark')
    write(LS.theme, theme)
    set({ theme })
  },
  toggleSidebar: () => {
    const sidebarCollapsed = !get().sidebarCollapsed
    write(LS.sidebar, sidebarCollapsed)
    set({ sidebarCollapsed })
  },
  setSidebarCollapsed: (v) => {
    write(LS.sidebar, v)
    set({ sidebarCollapsed: v })
  },
  togglePanel: (open) => {
    const panelOpen = open ?? !get().panelOpen
    write(LS.panel, panelOpen)
    set({ panelOpen })
  },
  setPanelTab: (panelTab) => set({ panelTab, panelOpen: true }),
  setSettingsOpen: (settingsOpen) => set({ settingsOpen }),
  setViewportCompact: (compactViewport) => {
    // <1024px：右侧面板自动关闭 + 侧栏强制紧凑 Rail（契约 5.2）
    if (compactViewport) set({ compactViewport, panelOpen: false, sidebarCollapsed: true })
    else set({ compactViewport })
  },

  setActiveSession: (activeSessionId) => {
    write(LS.session, activeSessionId ?? '')
    set({ activeSessionId })
  },
  setWorkspace: (workspace) => {
    write(LS.workspace, workspace)
    set({ workspace })
  },
  setPreset: (preset) => {
    write(LS.preset, preset)
    set({ preset })
  },
  setPermissionMode: (permissionMode) => {
    write(LS.permission, permissionMode)
    set({ permissionMode })
  },
  setModel: (model) => {
    write(LS.model, model)
    set({ model })
  },
  setReasoningEffort: (reasoningEffort) => {
    write(LS.effort, reasoningEffort)
    set({ reasoningEffort })
  },
}))
