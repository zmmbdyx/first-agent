"""可交互 HTML 匹配报告：自包含单文件（无外部依赖），悬停技能标签可查看 JD 频次与补齐建议。"""
import html
import math
import re
import time
from pathlib import Path

from config import ROOT, to_root_relative


def _radar_svg(values: dict, size: int = 460) -> str:
    """五维雷达图 SVG：悬停顶点显示数值。"""
    keys = list(values.keys())
    n = len(keys)
    cx = cy = size / 2
    r = size * 0.30
    def pt(i, ratio):
        ang = -math.pi / 2 + i * 2 * math.pi / n
        return cx + r * ratio * math.cos(ang), cy + r * ratio * math.sin(ang)
    rings = ""
    for ratio in (0.25, 0.5, 0.75, 1.0):
        pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in (pt(i, ratio) for i in range(n)))
        rings += f'<polygon points="{pts}" fill="none" stroke="#d8d2f0" stroke-width="1" opacity="0.5"/>'
    axes = ""
    for i in range(n):
        x, y = pt(i, 1.0)
        axes += f'<line x1="{cx}" y1="{cy}" x2="{x:.1f}" y2="{y:.1f}" stroke="#d8d2f0" stroke-width="1" opacity="0.5"/>'
    val_pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in (pt(i, values[k] / 100) for i, k in enumerate(keys)))
    dots = ""
    labels = ""
    for i, k in enumerate(keys):
        x, y = pt(i, values[k] / 100)
        lx, ly = pt(i, 1.18)
        anchor = "middle"
        if lx < cx - 10: anchor = "end"
        if lx > cx + 10: anchor = "start"
        dots += (f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="#7c3aed" stroke="#fff" stroke-width="1.5">'
                 f'<title>{html.escape(k)}: {values[k]}分</title></circle>')
        labels += (f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="{anchor}" dominant-baseline="middle" '
                   f'font-size="14" fill="#312e81" font-weight="600">{html.escape(k)} {values[k]}</text>')
    return (f'<svg viewBox="0 0 {size} {size}" width="{size}" height="{size}" role="img">'
            f'{rings}{axes}<polygon points="{val_pts}" fill="rgba(124,58,237,.25)" '
            f'stroke="#7c3aed" stroke-width="2"/>{dots}{labels}</svg>')


def build_html_report(match: dict, role: str = "") -> str:
    """生成自包含交互报告，返回文件路径。悬停任意技能可查看JD频次与建议。"""
    ts = time.strftime("%Y-%m-%d %H:%M")
    safe = re.sub(r"[^\w\u4e00-\u9fff-]", "_", role or "岗位")[:20]
    # 改动：原实现用相对 CWD 的 "data/reports/interactive/..." 落盘，服务从其他
    # 工作目录启动时会写到别处且 /files 链接失效；改为以项目根 ROOT 定位，
    # 返回值仍是相对项目根的正斜杠路径（前端 /files/ 前缀拼接口径不变）。
    out = Path(ROOT) / "data" / "reports" / "interactive" / \
        f"匹配报告_{safe}_{time.strftime('%H%M%S')}.html"
    out.parent.mkdir(parents=True, exist_ok=True)

    def chips(skills: dict, ok: bool):
        if not skills:
            return '<span class="dim">无</span>'
        out = []
        for k, v in skills.items():
            freq = v if isinstance(v, int) else 1
            tip = f"JD中出现 {freq} 次，优先级{'高' if freq >= 2 else '中'}"
            if ok:
                tip += "。已命中：面试时准备一个相关项目实例"
            else:
                tip += "。建议通过小型实战项目补齐并写入简历"
            cls = "ok" if ok else "miss"
            out.append(f'<span class="chip {cls}" title="{html.escape(tip)}">{html.escape(k)}'
                       f'<i>×{freq}</i></span>')
        return "".join(out)

    radar_vals = match.get("radar") or {}
    radar = _radar_svg(radar_vals) if radar_vals else ""
    matched = match.get("matched_skills") or {}
    missing = match.get("missing_skills") or {}
    score = match.get("score", "-")
    grade = match.get("grade", "-")
    chart = match.get("chart") or ""
    html_doc = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>求职智囊 · {html.escape(role)}匹配报告</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: "Microsoft YaHei", sans-serif; background: linear-gradient(160deg, #eef2ff, #f5f3ff);
         min-height: 100vh; padding: 32px 16px; color: #1e1b4b; }}
  .card {{ max-width: 880px; margin: 0 auto; background: #fff; border-radius: 20px; padding: 30px 34px;
          box-shadow: 0 16px 50px rgba(30,27,75,.12); }}
  h1 {{ font-size: 21px; margin-bottom: 4px; }}
  .sub {{ color: #6b7280; font-size: 13px; margin-bottom: 18px; }}
  .scoreRow {{ display: flex; gap: 26px; align-items: center; flex-wrap: wrap; margin-bottom: 22px; }}
  .score {{ font-size: 54px; font-weight: 800; color: #7c3aed; line-height: 1; }}
  .score small {{ font-size: 16px; color: #9ca3af; font-weight: 400; }}
  .grade {{ display: inline-grid; place-items: center; width: 56px; height: 56px; border-radius: 16px;
           background: linear-gradient(135deg,#7c3aed,#4f46e5); color: #fff; font-size: 26px; font-weight: 800; }}
  h2 {{ font-size: 15px; margin: 22px 0 10px; color: #4c1d95; }}
  .chip {{ display: inline-block; margin: 0 6px 8px 0; padding: 6px 12px; border-radius: 10px;
          font-size: 13px; cursor: help; border: 1px solid transparent; }}
  .chip i {{ font-style: normal; opacity: .65; font-size: 11px; margin-left: 3px; }}
  .chip.ok {{ background: #ecfdf5; color: #047857; border-color: #a7f3d0; }}
  .chip.miss {{ background: #fef2f2; color: #b91c1c; border-color: #fecaca; }}
  .dim {{ color: #9ca3af; font-size: 13px; }}
  .bar {{ height: 8px; background: #ede9fe; border-radius: 99px; overflow: hidden; margin: 6px 0 2px; }}
  .bar i {{ display: block; height: 100%; background: linear-gradient(90deg,#22d3ee,#7c3aed); }}
  img.chart {{ max-width: 100%; border-radius: 12px; border: 1px solid #ede9fe; margin-top: 8px; }}
  .tip {{ background: #f5f3ff; border-radius: 10px; padding: 10px 14px; font-size: 12.5px; color: #6d28d9; margin-top: 20px; }}
</style></head><body><div class="card">
<h1>🎯 {html.escape(role or "岗位")} · 匹配报告</h1>
<div class="sub">求职智囊 Agent 生成于 {ts} · 悬停技能标签查看详情</div>
<div class="scoreRow">
  <div><div class="score">{score}<small>/100</small></div></div>
  <div class="grade">{grade}</div>
  <div style="flex:1;min-width:240px">
    <div style="font-size:12.5px;color:#6b7280">硬技能覆盖 {match.get('hard_coverage','-')}%</div>
    <div class="bar"><i style="width:{match.get('hard_coverage',0)}%"></i></div>
    <div style="font-size:12.5px;color:#6b7280;margin-top:6px">软技能覆盖 {match.get('soft_coverage','-')}%</div>
    <div class="bar"><i style="width:{match.get('soft_coverage',0)}%"></i></div>
  </div>
  <div>{radar}</div>
</div>
<h2>✅ 已具备技能</h2><div>{chips(matched, True)}</div>
<h2>❌ 待补齐技能</h2><div>{chips(missing, False)}</div>
{f'<h2>📊 技能频次对比</h2><img class="chart" src="../../{html.escape(chart)}">' if chart else ''}
<div class="tip">💡 使用提示：悬停技能标签可查看该技能在 JD 中的出现频次与补齐建议；本报告由 Agent 自动分析生成，评分模型：硬技能70% + 软技能20% + 学历10%。</div>
</div></body></html>"""
    out.write_text(html_doc, encoding="utf-8")
    return to_root_relative(out)
