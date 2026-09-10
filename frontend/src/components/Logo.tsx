/**
 * 拓径 PATHFORGE 品牌标识：原创抽象「折线路径」几何图形。
 *
 * 构图（viewBox 0 0 24 24）：
 *   起点节点(4,18) → 三段折线 M4 18 L9.5 12 L14 15.5 L20 5.5 → 终点节点(20,5.5) + 末端箭头；
 * 语义：从起点出发、经转折推进、抵达目标的路径。
 * 全部由直线与圆构成，14px 下仍能辨识；不包含任何第三方品牌图形/字母商标。
 * 颜色走 `currentColor`，深浅主题由父级文字色决定。
 */
import { cn } from '@/lib/utils'

export interface LogoProps {
  /** 图形边长（px），默认 18；顶栏使用 15，侧栏 Rail 使用 20 */
  size?: number
  className?: string
}

export default function Logo({ size = 18, className }: LogoProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={cn('shrink-0', className)}
      role="img"
      aria-label="拓径 PATHFORGE"
    >
      {/* 主折线：起点 → 转折 → 转折 → 终点 */}
      <path d="M4 18 L9.5 12 L14 15.5 L20 5.5" />
      {/* 末端箭头：短折角指示推进方向 */}
      <path d="M16.8 5.5 H20 V8.7" />
      {/* 起终点实心节点 */}
      <circle cx="4" cy="18" r="1.6" fill="currentColor" stroke="none" />
      <circle cx="20" cy="5.5" r="1.6" fill="currentColor" stroke="none" />
    </svg>
  )
}
