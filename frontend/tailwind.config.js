/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // 全部走 CSS 变量，深/浅色主题只切换变量，无需改组件。
        // 注意：`bg` 与 `bg-elevated` 这类「同前缀 + 连字符」的嵌套键会互相冲突——
        // 解析 `bg-pf-bg-elevated` 时先命中 `pf.bg`，剩余 `-elevated` 无法匹配，
        // 工具类根本不会生成。因此高层背景令牌命名为 `elevated`（类名 bg-pf-elevated）。
        pf: {
          bg: 'var(--pf-bg)',
          elevated: 'var(--pf-bg-elevated)',
          surface: 'var(--pf-surface)',
          'surface-hover': 'var(--pf-surface-hover)',
          border: 'var(--pf-border)',
          'border-strong': 'var(--pf-border-strong)',
          text: 'var(--pf-text)',
          muted: 'var(--pf-text-muted)',
          faint: 'var(--pf-text-faint)',
          accent: 'var(--pf-accent)',
          'accent-text': 'var(--pf-accent-text)',
          ok: 'var(--pf-ok)',
          warn: 'var(--pf-warn)',
          err: 'var(--pf-err)',
          info: 'var(--pf-info)',
        },
      },
      borderRadius: {
        // 小圆角：贴近开发工具观感
        DEFAULT: '4px',
        md: '6px',
        lg: '8px',
      },
      fontFamily: {
        sans: ['-apple-system', 'BlinkMacSystemFont', 'Inter', 'Segoe UI',
               'PingFang SC', 'Microsoft YaHei', 'system-ui', 'sans-serif'],
        mono: ['SFMono-Regular', 'JetBrains Mono', 'Menlo', 'Consolas',
               'Liberation Mono', 'monospace'],
      },
      fontSize: {
        '2xs': ['10px', { lineHeight: '14px' }],
        xs: ['11px', { lineHeight: '16px' }],
        sm: ['12px', { lineHeight: '18px' }],
        base: ['13px', { lineHeight: '20px' }],
        md: ['14px', { lineHeight: '22px' }],
      },
      spacing: {
        rail: '56px',
        sidebar: '280px',
        panel: '360px',
      },
      keyframes: {
        'pf-pulse': { '0%,100%': { opacity: '1' }, '50%': { opacity: '0.35' } },
        'pf-shimmer': { '0%': { backgroundPosition: '-200% 0' },
                        '100%': { backgroundPosition: '200% 0' } },
        'pf-fade-in': { from: { opacity: '0', transform: 'translateY(2px)' },
                        to: { opacity: '1', transform: 'none' } },
      },
      animation: {
        'pf-pulse': 'pf-pulse 1.4s ease-in-out infinite',
        'pf-shimmer': 'pf-shimmer 1.8s linear infinite',
        'pf-fade-in': 'pf-fade-in 140ms ease-out',
      },
    },
  },
  plugins: [],
}
