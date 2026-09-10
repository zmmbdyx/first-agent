/**
 * 全部图标为手写的几何 SVG（stroke 走 currentColor），不引入任何图标库，
 * 既保证视觉语言统一，也确保不存在任何第三方图形/标识。
 */
import type { SVGProps } from 'react'

type IconProps = SVGProps<SVGSVGElement> & { size?: number }

function Base({ size = 14, children, ...rest }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...rest}
    >
      {children}
    </svg>
  )
}

export const IconPlus = (p: IconProps) => (
  <Base {...p}><path d="M12 5v14M5 12h14" /></Base>
)
export const IconMinus = (p: IconProps) => (
  <Base {...p}><path d="M5 12h14" /></Base>
)
export const IconX = (p: IconProps) => (
  <Base {...p}><path d="M6 6l12 12M18 6L6 18" /></Base>
)
export const IconCheck = (p: IconProps) => (
  <Base {...p}><path d="M4 12.5l5 5L20 6.5" /></Base>
)
export const IconChevronDown = (p: IconProps) => (
  <Base {...p}><path d="M6 9l6 6 6-6" /></Base>
)
export const IconChevronRight = (p: IconProps) => (
  <Base {...p}><path d="M9 6l6 6-6 6" /></Base>
)
export const IconChevronLeft = (p: IconProps) => (
  <Base {...p}><path d="M15 6l-6 6 6 6" /></Base>
)
export const IconPanelLeft = (p: IconProps) => (
  <Base {...p}><rect x="3" y="4" width="18" height="16" rx="1.5" /><path d="M9 4v16" /></Base>
)
export const IconPanelRight = (p: IconProps) => (
  <Base {...p}><rect x="3" y="4" width="18" height="16" rx="1.5" /><path d="M15 4v16" /></Base>
)
export const IconSettings = (p: IconProps) => (
  <Base {...p}>
    <circle cx="12" cy="12" r="3" />
    <path d="M12 2.5v2.2M12 19.3v2.2M4.2 7.2l1.9 1.1M17.9 15.7l1.9 1.1M4.2 16.8l1.9-1.1M17.9 8.3l1.9-1.1" />
  </Base>
)
export const IconSend = (p: IconProps) => (
  <Base {...p}><path d="M5 12h13M12 5.5L18.5 12 12 18.5" /></Base>
)
export const IconStop = (p: IconProps) => (
  <Base {...p}><rect x="6.5" y="6.5" width="11" height="11" rx="1.5" /></Base>
)
export const IconUpload = (p: IconProps) => (
  <Base {...p}><path d="M12 16V4.5M7.5 9L12 4.5 16.5 9M4.5 19.5h15" /></Base>
)
export const IconFolder = (p: IconProps) => (
  <Base {...p}><path d="M3.5 6.5A1.5 1.5 0 015 5h3.6l1.6 2H19a1.5 1.5 0 011.5 1.5v9A1.5 1.5 0 0119 19H5a1.5 1.5 0 01-1.5-1.5z" /></Base>
)
export const IconFile = (p: IconProps) => (
  <Base {...p}><path d="M6 3.5h7l5 5v12H6z" /><path d="M13 3.5v5h5" /></Base>
)
export const IconGit = (p: IconProps) => (
  <Base {...p}>
    <circle cx="7" cy="6" r="2.2" /><circle cx="7" cy="18" r="2.2" /><circle cx="17" cy="12" r="2.2" />
    <path d="M7 8.2v7.6M9.2 6h3.3a2.3 2.3 0 012.3 2.3v1.2" />
  </Base>
)
export const IconTool = (p: IconProps) => (
  <Base {...p}><path d="M14.5 3.5a5 5 0 00-4.2 7.7L4 17.5 6.5 20l6.3-6.3a5 5 0 007.7-4.2l-2.9 2.9-2.9-2.9z" /></Base>
)
export const IconGauge = (p: IconProps) => (
  <Base {...p}><path d="M4 17a8 8 0 1116 0" /><path d="M12 17l4-5" /></Base>
)
export const IconSun = (p: IconProps) => (
  <Base {...p}><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5L19 19M19 5l-1.5 1.5M6.5 17.5L5 19" /></Base>
)
export const IconMoon = (p: IconProps) => (
  <Base {...p}><path d="M20 14.5A8.5 8.5 0 019.5 4a8.5 8.5 0 1010.5 10.5z" /></Base>
)
export const IconTrash = (p: IconProps) => (
  <Base {...p}><path d="M4.5 7h15M9.5 7V4.5h5V7M6.5 7l1 13h9l1-13" /></Base>
)
export const IconPencil = (p: IconProps) => (
  <Base {...p}><path d="M4 20l4-1 10-10-3-3L5 16z" /><path d="M14.5 6.5l3 3" /></Base>
)
export const IconPin = (p: IconProps) => (
  <Base {...p}><path d="M9 3.5h6l-1 5 3 3v1.5H7V11.5l3-3z" /><path d="M12 13v7.5" /></Base>
)
export const IconSearch = (p: IconProps) => (
  <Base {...p}><circle cx="11" cy="11" r="5.5" /><path d="M15.5 15.5L20 20" /></Base>
)
export const IconTerminal = (p: IconProps) => (
  <Base {...p}><rect x="3" y="4.5" width="18" height="15" rx="1.5" /><path d="M7.5 9.5l2.5 2.5-2.5 2.5M12.5 15h4" /></Base>
)
export const IconRefresh = (p: IconProps) => (
  <Base {...p}><path d="M20 12a8 8 0 10-2.5 5.8" /><path d="M20 5.5V12h-6" /></Base>
)
export const IconAlert = (p: IconProps) => (
  <Base {...p}><path d="M12 4l9 16H3z" /><path d="M12 10v4.5M12 17.6v.1" /></Base>
)
export const IconCopy = (p: IconProps) => (
  <Base {...p}><rect x="9" y="9" width="11" height="11" rx="1.5" /><path d="M15 5.5H5.5A1.5 1.5 0 004 7v9" /></Base>
)
export const IconRotate = (p: IconProps) => (
  <Base {...p}><path d="M4 12a8 8 0 118 8 8 8 0 01-5.7-2.4" /><path d="M4 6.5V12h5.5" /></Base>
)
export const IconBranch = (p: IconProps) => (
  <Base {...p}><circle cx="6.5" cy="6" r="2" /><circle cx="6.5" cy="18" r="2" /><circle cx="17.5" cy="9" r="2" /><path d="M6.5 8v8M8.5 6h5a4 4 0 014 4v-1" /></Base>
)
export const IconSparkle = (p: IconProps) => (
  <Base {...p}><path d="M12 3.5l1.9 5.1 5.1 1.9-5.1 1.9L12 17.5l-1.9-5.1L5 10.5l5.1-1.9z" /></Base>
)

/** 加载指示：用旋转的折线弧，避免引入动画库 */
export const IconSpinner = ({ size = 14, className, ...rest }: IconProps) => (
  <Base size={size} className={`animate-spin ${className ?? ''}`} {...rest}>
    <path d="M12 3.5a8.5 8.5 0 108.5 8.5" />
  </Base>
)
