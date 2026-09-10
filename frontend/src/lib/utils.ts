import clsx, { type ClassValue } from 'clsx'

export const cn = (...inputs: ClassValue[]) => clsx(inputs)

/** 会话列表用的相对时间：刚刚 / 5分钟前 / 3小时前 / 2天前 / 03-14 */
export function relativeTime(iso: string | number | undefined): string {
  if (!iso) return ''
  const t = typeof iso === 'number' ? iso * 1000 : Date.parse(iso)
  if (Number.isNaN(t)) return ''
  const diff = Date.now() - t
  if (diff < 60_000) return '刚刚'
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`
  if (diff < 7 * 86_400_000) return `${Math.floor(diff / 86_400_000)} 天前`
  const d = new Date(t)
  return `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

export const formatDuration = (ms: number | undefined): string => {
  if (!ms || ms < 0) return '—'
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  const m = Math.floor(ms / 60_000)
  return `${m}m${Math.round((ms % 60_000) / 1000)}s`
}

export const formatBytes = (n: number | undefined): string => {
  if (!n) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let v = n
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i += 1
  }
  return `${v.toFixed(i === 0 ? 0 : 1)} ${units[i]}`
}

export const formatNumber = (n: number | undefined): string =>
  typeof n === 'number' ? n.toLocaleString('en-US') : '0'

export const formatPercent = (v: number | undefined, digits = 1): string =>
  `${((v ?? 0) * 100).toFixed(digits)}%`

/** 工具入参对象 → 紧凑单行展示（长值截断，避免撑爆面板） */
export function argsPreview(args: Record<string, unknown> | undefined, max = 60): string {
  if (!args) return ''
  const parts = Object.entries(args).map(([k, v]) => {
    const text = typeof v === 'string' ? v : JSON.stringify(v)
    const clipped = text && text.length > max ? `${text.slice(0, max)}…` : text
    return `${k}=${clipped}`
  })
  return parts.join('  ')
}

/** 从文件路径猜语言，供高亮组件使用 */
export function languageOf(path: string): string {
  const ext = path.split('.').pop()?.toLowerCase() ?? ''
  const map: Record<string, string> = {
    ts: 'typescript', tsx: 'tsx', js: 'javascript', jsx: 'jsx', py: 'python',
    json: 'json', md: 'markdown', css: 'css', html: 'html', yml: 'yaml', yaml: 'yaml',
    sh: 'bash', sql: 'sql', go: 'go', rs: 'rust', java: 'java', toml: 'toml',
  }
  return map[ext] ?? 'text'
}

export const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform)
