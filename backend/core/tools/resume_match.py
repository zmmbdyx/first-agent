"""简历-JD 匹配：技能覆盖加权评分 + 差距清单 + matplotlib 匹配度图表。
评分模型：硬技能覆盖 70%（按JD词频加权） + 软技能覆盖 20% + 学历门槛 10%。"""
import re
import time
from pathlib import Path

from core.tools.base import Tool, ToolError
from core.tools.jd_analyze import analyze_jd_text, EDU_PATTERN

EDU_RANK = {"学历不限": 0, "大专": 1, "本科": 2, "统招": 2, "研究生": 3, "硕士": 3, "博士": 4}


def read_resume_text(resume_text: str = "", resume_path: str = "") -> str:
    if resume_text:
        return resume_text
    if not resume_path:
        raise ToolError("需要提供 resume_text 或 resume_path")
    # 改动：原实现按扩展名分支后对 txt/md 直接 read_text，读加密落盘的上传简历
    # 会得到 ENC1: 密文乱码（匹配分静默失真）；统一交给 file_tools.read_text_any，
    # 该入口兼容加密存储与 pdf/docx/图片格式。
    from core.tools.file_tools import read_text_any
    return read_text_any(resume_path)


def compute_match(jd_text: str, resume_text: str) -> dict:
    jd = analyze_jd_text(jd_text)
    resume = analyze_jd_text(resume_text)  # 复用同一套技能词典统计简历侧技能
    jd_hard, rs_hard = jd["skills_hard"], resume["skills_hard"]
    jd_soft, rs_soft = jd["skills_soft"], resume["skills_soft"]

    matched = {k: v for k, v in jd_hard.items() if k in rs_hard}
    missing = {k: v for k, v in jd_hard.items() if k not in rs_hard}
    total_freq = sum(jd_hard.values()) or 1
    hard_cov = sum(jd_hard[k] for k in matched) / total_freq
    soft_cov = (sum(1 for k in jd_soft if k in rs_soft) / len(jd_soft)) if jd_soft else 0.6

    jd_edu = EDU_RANK.get(jd["education"], 2)
    m = EDU_PATTERN.search(resume_text)
    rs_edu = EDU_RANK.get(m.group(1), 2) if m else 2
    edu_ok = 1.0 if rs_edu >= jd_edu else 0.4

    score = round(hard_cov * 70 + soft_cov * 20 + edu_ok * 10, 1)
    grade = "A" if score >= 80 else "B" if score >= 60 else "C" if score >= 40 else "D"
    top5 = list(jd_hard.items())[:5]
    core_hit = round(sum(1 for k, _ in top5 if k in rs_hard) / len(top5) * 100) if top5 else 0
    result = {
        "score": score, "grade": grade,
        "hard_coverage": round(hard_cov * 100, 1),
        "soft_coverage": round(soft_cov * 100, 1),
        "edu_ok": bool(edu_ok == 1.0),
        "core_skill_hit": core_hit,
        "radar": {  # 雷达图维度（0-100）
            "硬技能覆盖": round(hard_cov * 100),
            "软技能覆盖": round(soft_cov * 100),
            "学历门槛": round(edu_ok * 100),
            "核心技能命中": core_hit,
            "综合匹配": round(score),
        },
        "jd_education": jd["education"], "jd_experience": jd["experience_years"],
        "jd_salary": jd["salary"],
        "matched_skills": matched, "missing_skills": missing,
        "soft_matched": [k for k in jd_soft if k in rs_soft],
        "soft_missing": [k for k in jd_soft if k not in rs_soft],
        "jd_keywords": jd["keywords_top"],
        "jd_responsibilities": jd["responsibilities"],
        "jd_requirements": jd["requirements"],
    }
    return result


def render_match_chart(result: dict, out_png: Path, role: str = "") -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "sans-serif"]
    plt.rcParams["axes.unicode_minus"] = False

    matched = list(result["matched_skills"].items())
    missing = list(result["missing_skills"].items())
    items = matched + missing
    if not items:
        return out_png
    items = sorted(items, key=lambda kv: -kv[1])[:12]
    labels = [k for k, _ in items][::-1]
    values = [v for _, v in items][::-1]
    colors = ["#2ecc71" if k in result["matched_skills"] else "#e74c3c" for k in labels]

    fig, ax = plt.subplots(figsize=(8, max(3.2, 0.45 * len(labels))))
    ax.barh(labels, values, color=colors)
    ax.set_xlabel("JD 中出现次数")
    ax.set_title(f"简历-JD 技能匹配（{role or '目标岗位'}）  总分 {result['score']}（{result['grade']}级）",
                 fontsize=12)
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color="#2ecc71", label="已具备"),
                       Patch(color="#e74c3c", label="需补齐")], loc="lower right")
    plt.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out_png, dpi=130)
    plt.close(fig)
    return out_png


def render_radar_chart(result: dict, out_png: Path, role: str = "") -> Path:
    """五维雷达图（硬技能/软技能/学历/核心技能/综合），与交互HTML报告同维度。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "sans-serif"]
    plt.rcParams["axes.unicode_minus"] = False
    vals = result.get("radar") or {}
    if not vals:
        return out_png
    keys = list(vals.keys())
    n = len(keys)
    angles = np.linspace(0, 2 * np.pi, n, endpoint=False).tolist()
    data = [vals[k] for k in keys] + [vals[keys[0]]]
    angles += angles[:1]
    fig = plt.figure(figsize=(5.2, 4.6))
    ax = fig.add_subplot(111, polar=True)
    ax.plot(angles, data, color="#7c3aed", linewidth=2)
    ax.fill(angles, data, color="#7c3aed", alpha=0.22)
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels([f"{k}\n{vals[k]}" for k in keys], fontsize=9)
    ax.set_ylim(0, 100)
    ax.set_yticks([25, 50, 75, 100])
    ax.set_yticklabels(["25", "50", "75", ""], fontsize=7, color="#9ca3af")
    ax.set_title(f"{role or '岗位'} · 多维匹配雷达", fontsize=12, pad=16)
    ax.grid(color="#d1d5db", alpha=0.6)
    plt.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out_png, dpi=130)
    plt.close(fig)
    return out_png


class ResumeMatchTool(Tool):
    name = "resume_match"
    description = ("计算简历与JD的匹配度：加权总分(A-D级)、已具备/缺失技能清单，"
                   "生成匹配度柱状图+雷达图+可交互HTML报告（悬停查看技能差距）。")
    args_desc = {"jd_text": "JD 原文", "resume_text": "简历原文", "resume_path": "简历文件路径(txt/md/pdf/docx)",
                 "role": "岗位名称（用于图表标题）"}
    cost = "中"
    avg_seconds = 1.5

    def run(self, jd_text: str = "", resume_text: str = "", resume_path: str = "",
            role: str = "") -> dict:
        if not jd_text:
            raise ToolError("jd_text 不能为空（可先经 jd_analyze/file_read 获得）")
        resume_text = read_resume_text(resume_text, resume_path)
        if len(resume_text.strip()) < 30:
            raise ToolError("简历内容过短，无法匹配")
        result = compute_match(jd_text, resume_text)
        ts = time.strftime("%H%M%S")
        safe_role = re.sub(r"[^\w\u4e00-\u9fff-]", "_", role or "岗位")[:20]
        # 改动：图表原先写相对 CWD 的 "data/reports/charts/..."，服务若从其他工作目录
        # 启动就会写到错误位置且 /files 链接失效；改为以项目根 ROOT 定位，
        # 返回值仍用 to_root_relative 还原成与旧格式一致的相对路径（前端无需改动）。
        from config import ROOT, to_root_relative
        charts_dir = Path(ROOT) / "data" / "reports" / "charts"
        chart = render_match_chart(result, charts_dir / f"match_{safe_role}_{ts}.png", role)
        result["chart"] = to_root_relative(chart)
        radar = render_radar_chart(result, charts_dir / f"radar_{safe_role}_{ts}.png", role)
        result["radar_chart"] = to_root_relative(radar)
        from core.tools.report_html import build_html_report
        result["html"] = build_html_report(result, role)
        return result
