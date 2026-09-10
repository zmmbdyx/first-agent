"""任务规划模块：LLM 规划 + JSON 校验 + 兜底模板规划（mock 模式 / LLM 规划失败时）。"""
import json
import re
from typing import List, Tuple

from config import Config
from core.memory import Task
from core.prompts import PLANNER_SYSTEM
from tools.base import ToolRegistry

ROLE_KEYWORDS = ["产品经理", "算法工程师", "后端开发", "前端开发", "数据分析", "数据产品经理",
                 "大模型", "机器学习", "运营", "测试", "运维", "人力资源", "UI设计", "销售"]


def build_fallback_plan(facts: dict, cfg: Config) -> Tuple[str, List[Task]]:
    """确定性兜底计划：材料齐全走标准分析链路；缺材料先 ask_user。
    JD 已是纯文本（粘贴/OCR）时不再生成"读取文件"任务，直接从结构化分析开始。"""
    from tools.file_tools import default_resume_path
    jd_path = (facts.get("jd_paths") or [None])[0]
    jd_text = facts.get("jd_text") or ""
    try:
        resume_path = (facts.get("resume_paths") or [None])[0] or default_resume_path()
    except Exception:
        resume_path = ""
    role = facts.get("target_role") or "目标岗位"

    if not jd_text and not jd_path:
        goal = f"收集用户求职材料并完成「{role}」岗位分析"
        tasks = [
            Task(id="t1", title="向用户收集材料", tool="ask_user",
                 detail="缺少JD与简历，需要用户提供后才能分析"),
        ]
        return goal, tasks

    ext = (jd_path or "").lower()
    next_id = 1
    tasks = []
    if jd_path:  # 只有材料是文件时才需要读取步骤
        read_tool = ("pdf_extract" if ext.endswith(".pdf")
                     else "docx_extract" if ext.endswith(".docx")
                     else "image_ocr" if ext.endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp"))
                     else "file_read")
        tasks.append(Task(id="t1", title="读取并解析JD材料",
                          detail=f"从 {jd_path} 提取JD全文", tool=read_tool,
                          args={"path": jd_path}))
        next_id = 2

    tasks += [
        Task(id=f"t{next_id}", title=f"结构化分析JD（{role}）",
             detail="抽取硬/软技能频次、学历经验薪资、章节要点",
             tool="jd_analyze", args={"top_k": 15},
             depends_on=[f"t{next_id - 1}"] if next_id > 1 else []),
        Task(id=f"t{next_id + 1}", title="计算简历-JD匹配度",
             detail="加权评分、生成已具备/缺失技能清单与匹配图表",
             tool="resume_match", args={"resume_path": resume_path, "role": role},
             depends_on=[f"t{next_id}"]),
        Task(id=f"t{next_id + 2}", title="生成面试问题清单",
             detail="基于JD要求生成8-10道分类面试题",
             tool="none", depends_on=[f"t{next_id}"]),
        Task(id=f"t{next_id + 3}", title="生成优化建议与最终报告",
             detail="针对差距给出简历优化建议，汇总为markdown报告",
             tool="write_report", args={"filename": f"求职分析报告_{role}"},
             depends_on=[f"t{next_id + 1}", f"t{next_id + 2}"]),
    ]
    return f"分析「{role}」JD并匹配简历、产出优化建议与面试题清单", tasks


def validate_plan(data: dict, cfg: Config, known_tools: set[str] | None = None) -> List[Task]:
    """校验 LLM 规划输出并转换为内部 Task 列表；非法时抛 ValueError。

    校验交由 Pydantic（core/schemas.py）完成——类型、长度、取值、依赖存在性、
    依赖环、id 唯一性都在边界处一次性挡掉。这里只负责：把校验通过的
    PlanSchema 适配成执行器使用的 dataclass Task。

    known_tools: 注册表里的合法工具名；用于把模型偶发的工具名拼写偏差降级为
    "由模型直接作答"（tool=none），而不是让整份计划作废。
    """
    from core.schemas import validate_plan_dict

    if known_tools is None:
        known_tools = set(getattr(cfg, "known_tools", None) or [])
    plan = validate_plan_dict(data, known_tools=known_tools or None, max_tasks=cfg.max_tasks)
    if plan is None:
        raise ValueError("计划校验未通过（结构非法、任务为空、id 重复、依赖缺失或存在依赖环）")

    tasks = [Task(id=t.id, title=t.title[:60], detail=t.detail[:300], tool=t.tool,
                  args=t.args, depends_on=list(t.depends_on), condition=t.condition)
             for t in plan.tasks]
    return tasks


def plan_with_llm(llm, registry: ToolRegistry, facts: dict, user_msg: str,
                  cfg: Config) -> Tuple[str, List[Task]]:
    system = PLANNER_SYSTEM.format(tool_schema=registry.prompt_schema(), max_tasks=cfg.max_tasks)
    material = json.dumps({"facts": facts, "user_message": user_msg[-1500:]}, ensure_ascii=False)
    data = llm.chat_json([{"role": "system", "content": system},
                          {"role": "user", "content": material}], purpose="plan")
    goal = str(data.get("goal") or user_msg[:80])
    return goal, validate_plan(data, cfg, known_tools=set(registry.tools.keys()))


PATH_RE = re.compile(
    r"[A-Za-z]:[^()\[\]{}\"'，。；！？\n]+?\.(?:pdf|txt|md|docx|png|jpe?g|webp|bmp)"  # Windows 绝对路径（可含空格）
    r"|[\w\u4e00-\u9fff\-/\\. ]+?\.(?:pdf|txt|md|docx|png|jpe?g|webp|bmp)", re.I)


def _resolve_path_candidate(cand: str):
    """候选路径可能混入前后文字（路径含空格时尤甚），从左裁剪并校验真实存在。"""
    from pathlib import Path
    from config import ROOT
    tokens = cand.strip().split(" ")
    for i in range(len(tokens)):
        c = " ".join(tokens[i:]).strip().strip("，。；！？、")
        if not c:
            continue
        for p in (Path(c), ROOT / c):
            try:
                if p.is_file():
                    return str(p)
            except OSError:
                continue
    return None


def extract_facts_regex(text: str) -> dict:
    """不依赖LLM的事实抽取：文件路径（存在性校验）、目标岗位、城市（mock模式与真实模式共用兜底）。"""
    facts = {}
    paths, seen = [], set()
    for cand in PATH_RE.findall(text):
        resolved = _resolve_path_candidate(cand)
        if resolved and resolved not in seen:
            seen.add(resolved)
            paths.append(resolved)
    if paths:
        jds = [p for p in paths if "jd" in p.lower() or "岗位" in p or "招聘" in p]
        res = [p for p in paths if "简历" in p or "resume" in p.lower() or "cv" in p.lower()]
        facts["jd_paths"] = jds or (paths if not res else [])
        facts["resume_paths"] = res
    # 岗位识别：先看去掉文件路径后的用户原话（用户明确说的优先），
    # 再看JD文件名（简历文件名可能含岗位词，会污染判断）；组内长关键词优先
    from pathlib import Path as _P
    text_wo_paths = PATH_RE.sub(" ", text)
    for source_group in ([text_wo_paths],
                         [_P(p).stem for p in facts.get("jd_paths", [])],
                         [_P(p).stem for p in facts.get("resume_paths", [])]):
        for kw in sorted(ROLE_KEYWORDS, key=len, reverse=True):
            if any(kw in s for s in source_group):
                facts["target_role"] = kw
                break
        if "target_role" in facts:
            break
    m = re.search(r"(北京|上海|深圳|广州|杭州|成都|南京|武汉|西安|苏州|长沙|重庆|天津|合肥|厦门)", text)
    if m:
        facts["city"] = m.group(1)
    m = re.search(r"(\d+)\s*年.{0,3}(经验|工作经验)", text)
    if m:
        facts["experience_years"] = m.group(1) + "年"
    # —— 求职智囊画像字段 ——
    m = re.search(r"(?:我叫|姓名[:：])\s*([\u4e00-\u9fa5]{2,4}|[A-Za-z]{2,15})", text)
    if m:
        facts["name"] = m.group(1)
    m = re.search(r"(?:期望薪资|薪资期望|薪资|月薪)[:：]?\s*(\d{1,3}(?:\.\d)?)\s*[-~到]\s*(\d{1,3}(?:\.\d)?)\s*([kK万]?)", text)
    if m:
        facts["salary_range"] = f"{m.group(1)}-{m.group(2)}{m.group(3) or 'K'}"
    else:
        m = re.search(r"(\d{1,2}(?:\.\d)?)\s*[-~到]\s*(\d{1,3}(?:\.\d)?)\s*([kK万])\s*的?(?:薪资|工资)", text)
        if m:
            facts["salary_range"] = f"{m.group(1)}-{m.group(2)}{m.group(3)}"
    m = re.search(r"(博士|硕士|研究生|本科|大专)", text)
    if m:
        facts["education"] = m.group(1)
    if re.search(r"远程|居家办公", text):
        prefs = list(facts.get("preferences") or [])
        if "远程" not in prefs:
            facts["preferences"] = prefs + ["远程"]
    return facts
