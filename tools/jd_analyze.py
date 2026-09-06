"""JD 结构化分析：技能词频（jieba + 技能词典）、学历/经验/薪资抽取、章节切分、高频关键词。
这是核心的数据分析工具之一，输出供简历匹配与报告生成使用。"""
import re
from collections import Counter

from tools.base import Tool, ToolError

HARD_SKILLS = {
    "Python": ["python"], "SQL": ["sql"], "Java": ["java"], "Go": ["golang", "go语言"],
    "C/C++": ["c++", "c语言"], "JavaScript/TS": ["javascript", "typescript"],
    "React": ["react"], "Vue": ["vue"], "前端开发": ["前端", "html", "css"],
    "机器学习": ["机器学习", "machine learning"], "深度学习": ["深度学习", "deep learning"],
    "PyTorch": ["pytorch"], "TensorFlow": ["tensorflow"],
    "NLP": ["nlp", "自然语言处理"], "计算机视觉": ["计算机视觉", "cv算法", "图像识别", "图像处理"],
    "大模型/LLM": ["大模型", "llm", "gpt", "llm", "your-model-", "aigc", "transformer"],
    "RAG": ["rag", "检索增强"], "Prompt工程": ["prompt", "提示词"],
    "Agent开发": ["agent", "智能体", "function call", "mcp"],
    "数据分析": ["数据分析"], "Excel": ["excel"], "Tableau": ["tableau"],
    "PowerBI": ["power bi", "powerbi"], "Hive": ["hive"], "Spark": ["spark"],
    "Hadoop": ["hadoop"], "Docker": ["docker"], "K8s": ["k8s", "kubernetes"],
    "Linux": ["linux"], "A/B测试": ["a/b测试", "ab测试", "a/b test"],
    "统计学": ["统计学", "统计学习"], "推荐系统": ["推荐系统", "推荐算法"],
    "爬虫": ["爬虫", "scrapy"], "产品设计": ["产品设计", "产品方案"], "Axure": ["axure"],
    "Figma": ["figma"], "PRD/需求文档": ["prd", "需求文档"], "用户增长": ["用户增长"],
    "项目管理": ["项目管理"], "数据建模": ["数据建模", "数仓"],
}
SOFT_SKILLS = {
    "沟通表达": ["沟通", "表达"], "团队协作": ["协作", "团队合作", "团队合作精神"],
    "学习能力": ["学习能力", "快速学习"], "责任心": ["责任心", "责任感"],
    "抗压能力": ["抗压"], "逻辑思维": ["逻辑思维", "逻辑清晰"], "英语能力": ["英语", "英文"],
    "主动性": ["主动性", "自驱"],
}
STOPWORDS = set("""的 了 和 与 及 或 等 在 是 为 对 由 从 将 并 会 能 可 需 有 无 本 我 你 他 她 它 们 这 那
负责 参与 相关 进行 通过 基于 使用 利用 具备 熟悉 精通 掌握 了解 熟练 优先 良好 较强
以上 以下 工作 岗位 公司 团队 能力 经验 要求 职责 描述 任职 资格 加分 我们 你的 包括 根据
完成 推动 协助 支持 确保 提升 优化 建设 制定 落地 输出 提供 带领 指导 沟通 协调 配合 其他
核心 持续 不断 独立 常用 主要 重点 有效 快速 高效 体系 场景 业务 用户 产品 数据 平台 系统 项目 需求 方案
some the and with for you will our your are have has been more than""".split())

EDU_PATTERN = re.compile(r"(博士|硕士|研究生|本科|大专|统招|学历不限)")
EXP_PATTERN = re.compile(r"(\d+)\s*[-~到]\s*(\d+)\s*年|(\d+)\s*年以上")
SALARY_PATTERN = re.compile(r"(\d+)\s*[-~到]\s*(\d+)\s*([kK万])\s*(?:元)?")
SECTION_PATTERN = re.compile(
    r"(岗位职责|工作职责|职位描述|职位职责|工作内容|岗位描述|任职要求|任职资格|岗位要求|职位要求|加分项|我们希望)")


def analyze_jd_text(text: str) -> dict:
    """纯函数：输入 JD 文本，输出结构化分析（工具与批量评测共用）。"""
    if not text or len(text.strip()) < 30:
        raise ToolError("JD 文本过短或为空，无法分析")
    lower = text.lower()

    hard = {name: sum(lower.count(a.lower()) for a in aliases)
            for name, aliases in HARD_SKILLS.items()}
    hard = {k: v for k, v in hard.items() if v > 0}
    soft = {name: sum(lower.count(a.lower()) for a in aliases)
            for name, aliases in SOFT_SKILLS.items()}
    soft = {k: v for k, v in soft.items() if v > 0}

    edu = EDU_PATTERN.search(text)
    exp = EXP_PATTERN.search(text)
    sal = SALARY_PATTERN.search(text)
    salary = None
    if sal:
        unit = {"k": "K", "K": "K", "万": "万"}[sal.group(3)]
        salary = f"{sal.group(1)}-{sal.group(2)}{unit}"

    # 章节切分：顺序扫描「职责 / 要求 / 加分项」标题，归属其下条目
    responsibilities, requirements, bonus = [], [], []
    current = None
    for para in re.split(r"\n+", text):
        head = SECTION_PATTERN.match(para.strip())
        if head and len(para.strip()) < 15:
            t = head.group(1)
            current = "bonus" if "加分" in t else ("req" if "要求" in t or "资格" in t else "resp")
            para = para[head.end():]
        if current and para.strip():
            items = re.split(r"[；;]|(?:\d+[\.、)）])", para)
            bucket = {"resp": responsibilities, "req": requirements, "bonus": bonus}[current]
            bucket += [i.strip() for i in items if len(i.strip()) > 6]

    # jieba 高频关键词（数据统计分析）
    try:
        import jieba
        jieba.setLogLevel(60)
        words = [w for w in jieba.cut(text)
                 if len(w) >= 2 and not w.isdigit() and w not in STOPWORDS
                 and not re.match(r"^[\W_a-zA-Z0-9]+$", w)]
        top_words = Counter(words).most_common(12)
    except Exception:
        top_words = []

    ranked = dict(sorted(hard.items(), key=lambda kv: -kv[1]))
    return {
        "skills_hard": ranked,
        "skills_soft": dict(sorted(soft.items(), key=lambda kv: -kv[1])),
        "education": edu.group(1) if edu else "未明确",
        "experience_years": (f"{exp.group(1)}-{exp.group(2)}年" if exp and exp.group(2)
                             else (f"{exp.group(3)}年以上" if exp else "未明确")),
        "salary": salary or "未明确",
        "responsibilities": responsibilities[:8],
        "requirements": requirements[:10],
        "bonus": bonus[:5],
        "keywords_top": [[w, c] for w, c in top_words],
        "char_count": len(text),
    }


class JdAnalyzeTool(Tool):
    name = "jd_analyze"
    description = "分析JD文本：抽取硬/软技能及频次、学历经验薪资要求、章节要点、高频关键词（jieba+pandas）。"
    args_desc = {"jd_text": "JD 原文（可与 jd_path 二选一）", "jd_path": "JD 文件路径（txt/md/pdf）",
                 "top_k": "返回技能TopK，默认15"}
    cost = "低"
    avg_seconds = 0.4

    def run(self, jd_text: str = "", jd_path: str = "", top_k: int = 15) -> dict:
        if jd_path:
            from pathlib import Path
            from tools.file_tools import _resolve
            p = Path(_resolve(jd_path))
            if p.suffix.lower() == ".pdf":
                from tools.file_tools import PdfExtractTool
                jd_text = PdfExtractTool().run(path=str(p))["text"]
            else:
                jd_text = p.read_text(encoding="utf-8", errors="ignore")
        if not jd_text:
            raise ToolError("需要提供 jd_text 或 jd_path")
        result = analyze_jd_text(jd_text)
        top_k = max(3, min(int(top_k or 15), 30))
        result["skills_hard"] = dict(list(result["skills_hard"].items())[:top_k])
        return result
