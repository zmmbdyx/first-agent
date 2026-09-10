# -*- coding: utf-8 -*-
"""生成测试数据：10个真实风格JD（整理自主流招聘平台真实在招岗位）+ 样例简历(txt+PDF)。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
JD_DIR = ROOT / "jds"
RES_DIR = ROOT / "resumes"
JD_DIR.mkdir(exist_ok=True)
RES_DIR.mkdir(exist_ok=True)

JDS = {
"jd01_AI产品经理_大模型.txt": """【岗位】AI产品经理（大模型方向）-北京
薪资：30-45K·15薪

岗位职责：
1. 负责公司大模型ToB产品（智能问答、知识库）的规划与落地，输出PRD与产品方案；
2. 深入理解LLM、RAG、Prompt工程等技术边界，与算法团队协作定义产品能力；
3. 跟进A/B测试与数据指标体系，持续优化回答准确率与用户满意度；
4. 调研行业动态与竞品，输出分析报告；
5. 协调设计、研发、测试团队推进版本迭代。

任职要求：
1. 本科及以上学历，3年以上产品经验，1年以上AI/大模型相关产品经验；
2. 熟悉Prompt工程、RAG检索增强、Agent工作流等大模型应用技术方案；
3. 具备较强的数据分析能力，熟练使用SQL或Excel进行指标分析；
4. 逻辑思维清晰，沟通表达能力出色，能独立对接客户与研发；
5. 有Axure/Figma原型设计能力。

加分项：有ToB SaaS产品经验；熟悉LangChain等开发框架；英语可作为工作语言。""",

"jd02_算法工程师_NLP.txt": """岗位名称：算法工程师（NLP/大模型方向）
工作地点：上海 薪资：35-60K

工作职责：
1. 负责大语言模型的训练、微调（SFT/RLHF）与评测，支撑业务对话与文本理解场景；
2. 负责NLP算法研发：文本分类、命名实体识别、语义检索（Embedding+召回）；
3. 构建高质量数据集与自动化评测体系，持续迭代模型效果；
4. 跟进学界与工业界前沿（Transformer、MoE、大模型微调等）并快速落地。

任职要求：
1. 硕士及以上学历，计算机、数学相关专业，2年以上NLP/深度学习经验；
2. 精通Python与PyTorch，熟悉Transformer架构与预训练/微调全流程；
3. 熟悉RAG检索增强生成技术栈，有Embedding模型调优经验优先；
4. 扎实的统计学与机器学习基础，能独立完成数据建模与实验设计；
5. 熟练使用Linux与Docker，具备良好的工程化能力。

加分项：发表过顶会论文（ACL/EMNLP/NeurIPS）；有百亿参数模型训练经验；Kaggle竞赛获奖。""",

"jd03_数据分析师.txt": """职位：数据分析师（电商方向）
城市：杭州 | 薪资：18-28K·14薪

工作内容：
1. 负责电商核心业务（交易、用户增长、留存）的日常数据分析，输出周报月报；
2. 搭建业务指标体系与看板（Tableau），监控异常波动并定位原因；
3. 设计并分析A/B测试，为运营与产品决策提供数据支持；
4. 使用SQL从数仓（Hive）提取数据，用Python完成深度分析与用户分群建模。

任职要求：
1. 本科及以上学历，统计学、数学、计算机相关专业，1-3年数据分析经验；
2. 精通SQL，熟练使用Python（pandas）进行数据处理与分析；
3. 熟练使用Excel与Tableau/PowerBI，能独立产出高质量分析报告；
4. 熟悉A/B测试方法与常用统计学检验，具备良好的逻辑思维能力；
5. 沟通顺畅，责任心强，能与业务方高效协作。

加分项：有电商平台数据分析经验；会用机器学习模型（如XGBoost）做预测；有数仓建模经验。""",

"jd04_后端开发_Go.txt": """【招聘】后端开发工程师（Go）-深圳
月薪 25-40K

岗位职责：
1. 负责公司核心交易系统微服务的设计与开发（Go语言）；
2. 参与高并发架构设计：缓存（Redis）、消息队列（Kafka）、分库分表；
3. 保障服务稳定性：链路追踪、压测、容量规划与线上问题排查；
4. 编写技术方案与接口文档，参与代码评审。

任职要求：
1. 本科及以上学历，计算机相关专业，3年以上后端开发经验；
2. 精通Go或Java，熟悉常用数据结构与算法；
3. 熟悉MySQL、Redis、Kafka等中间件原理与调优；
4. 熟悉Docker与K8s容器化部署，有Linux环境下开发经验；
5. 良好的团队协作精神与抗压能力。

加分项：有日活千万级系统经验；熟悉云原生（Service Mesh）；有开源项目贡献。""",

"jd05_前端开发_React.txt": """岗位：前端开发工程师（React方向）
坐标：北京 | 25-38K

工作职责：
1. 负责SaaS产品Web端的前端架构设计与核心功能开发（React+TypeScript）；
2. 搭建前端工程化体系：构建优化、组件库、微前端；
3. 与后端协作设计API，保障首屏性能与交互体验；
4. 沉淀可视化组件，支持数据大屏与图表类需求。

任职要求：
1. 本科及以上学历，2年以上前端开发经验；
2. 精通JavaScript/TypeScript，深入理解React原理与Hooks机制；
3. 熟悉Webpack/Vite构建与性能优化，熟悉Node.js；
4. 有数据可视化经验（ECharts/AntV）者优先；
5. 逻辑清晰，沟通协作顺畅，有代码质量意识。

加分项：参与过开源组件库；熟悉WebGL/Three.js；有AI产品前端经验（流式渲染、SSE）。""",

"jd06_大模型应用工程师.txt": """职位：大模型应用工程师（Agent方向）
上海 | 30-50K·16薪

工作职责：
1. 负责LLM应用研发：智能体（Agent）编排、Function Call、多轮对话系统；
2. 搭建RAG知识库问答链路：文档解析、切分、Embedding、重排、生成；
3. 开发后端服务（Python/FastAPI）支撑模型推理与业务逻辑；
4. 建立LLM应用的评测与监控体系，持续优化准确率与成本。

任职要求：
1. 本科及以上学历，1年以上大模型应用或NLP相关开发经验；
2. 精通Python，熟悉主流大模型应用与 Agent 开发框架，理解Agent与RAG原理；
3. 熟悉向量数据库（Milvus/ES）与Prompt调优；
4. 熟悉Linux与Docker部署，具备较强工程能力；
5. 学习能力强，对AIGC生态有热情。

加分项：有MCP/工具调用生态开发经验；熟悉模型微调（LoRA）；有开源项目或技术博客。""",

"jd07_数据产品经理.txt": """招聘：数据产品经理-杭州
20-35K·14薪

岗位职责：
1. 负责数据中台产品（指标体系、报表平台、标签系统）的规划与迭代；
2. 调研业务方数据需求，输出PRD，推动数据仓库建模与口径统一；
3. 设计自助分析（BI）功能，降低业务用数门槛；
4. 通过A/B测试与使用数据度量产品价值。

任职要求：
1. 本科及以上学历，3年以上产品经验，有数据产品或BI产品经验；
2. 熟悉数仓基本概念（维度建模、指标口径），会写SQL优先；
3. 熟练使用Axure输出原型，具备优秀的逻辑思维与文档能力；
4. 数据分析能力强，能通过数据发现问题并提出产品方案；
5. 跨团队沟通协作经验丰富。

加分项：熟悉Tableau/PowerBI等BI工具；有大数据平台（Hive/Spark）使用经验；有AI+数据产品构想实践。""",

"jd08_机器学习工程师_推荐.txt": """岗位名称：机器学习工程师（推荐系统）
深圳 35-55K

工作职责：
1. 负责推荐系统召回/排序模型的研发与迭代（精排、重排）；
2. 基于深度学习框架（PyTorch/TensorFlow）设计CVR、时长等目标模型；
3. 离线特征工程与在线特征服务（Spark/Flink）建设；
4. 设计离线实验与在线A/B测试，持续提升核心业务指标。

任职要求：
1. 硕士学历优先，计算机/数学相关专业，2年以上机器学习经验；
2. 精通Python与PyTorch，深入理解CTR预估常用模型（DIN、MMoE等）；
3. 扎实的统计学与数据建模功底，熟练使用SQL与Spark处理大规模数据；
4. 熟悉Linux开发环境，代码工程能力强；
5. 对推荐系统有热情，具备快速学习能力。

加分项：有短视频/电商推荐经验；有强化学习或大模型推荐方向实践；顶会论文。""",

"jd09_用户增长运营.txt": """【急聘】用户增长运营-成都
12-20K·13薪

工作内容：
1. 负责App新用户获取与留存增长，策划裂变活动与激励体系；
2. 搭建增长指标体系（AARRR），通过数据分析定位增长机会；
3. 设计并复盘A/B测试，持续优化转化漏斗；
4. 联动产品、设计与投放团队落地增长实验。

任职要求：
1. 本科及以上学历，2年以上用户运营或增长运营经验；
2. 数据分析能力强，熟练使用Excel，会SQL者优先；
3. 有完整增长案例（拉新/促活/召回），熟悉A/B测试方法；
4. 逻辑思维清晰，执行力强，抗压能力好；
5. 优秀的沟通协作与文案能力。

加分项：有千万级用户产品经验；熟悉增长黑客方法论；会用Python做数据分析。""",

"jd10_测试开发.txt": """职位：测试开发工程师（AI产品线）
北京 | 20-32K

工作职责：
1. 负责AI产品（对话机器人/知识库）的质量保障体系建设；
2. 开发自动化测试框架与工具（Python+Pytest），覆盖接口与模型效果评测；
3. 构建LLM输出质量评测集与自动化评估流水线；
4. 参与需求评审，输出测试方案，跟进线上问题。

任职要求：
1. 本科及以上学历，计算机相关专业，2年以上测试开发经验；
2. 精通Python，熟悉接口自动化与性能测试（JMeter/Locust）；
3. 熟悉Linux与Docker，了解CI/CD流程（Jenkins/GitLab CI）；
4. 逻辑思维清晰，责任心强，具备良好的沟通能力。

加分项：有大模型评测经验；会使用PyTorch进行简单模型验证；有Selenium/UI自动化经验。""",
}

RESUME_TXT = """李明 - 求职意向：数据分析师/数据产品经理（杭州）
电话：138****5678 | 邮箱：liming@email.com | 本科·计算机科学与技术

【教育背景】
2019.09-2023.06 华东某大学 计算机科学与技术 本科
主修课程：数据结构、数据库原理、统计学、机器学习

【工作经历】
2023.07-至今 某电商科技公司 数据分析师
1. 负责交易与用户增长线日常数据分析，独立输出周报/月报与专题分析报告20+篇；
2. 使用SQL（Hive）提取清洗数据，用Python（pandas）完成用户分群与留存分析；
3. 搭建部门Tableau看板8个，覆盖GMV、转化漏斗等核心指标体系；
4. 支持6场A/B测试的设计与复盘，推动「改版详情页」项目转化率提升8%。

【项目经历】
1. 销量预测模型（机器学习课程项目）：使用Python+scikit-learn（XGBoost）预测商品周销量，MAPE降低15%；
2. 评论情感分析（毕业设计）：Python爬虫采集10万条评论，jieba分词+NLP情感分类，准确率86%；
3. 大模型应用学习项目：基于大模型接口开发 RAG 知识库问答 Demo（检索增强 + 向量检索）。

【技能清单】
- 数据分析：SQL（熟练）、Python（pandas/numpy）、Excel（熟练）、统计学基础
- 可视化：Tableau、PowerBI
- 机器学习：scikit-learn、XGBoost、A/B测试方法论
- 其他：Axure原型、PRD文档撰写、Docker基础

【自我评价】
逻辑思维能力较强，学习能力强，对大模型与AI产品方向有浓厚兴趣，沟通协作顺畅。"""


def make_pdf():
    """用 reportlab 内置 CID 字体生成中文简历PDF（演示 pdf_extract 工具）。"""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    styles = {
        "title": ParagraphStyle("t", fontName="STSong-Light", fontSize=16, leading=22),
        "h": ParagraphStyle("h", fontName="STSong-Light", fontSize=12, leading=18, spaceBefore=8),
        "p": ParagraphStyle("p", fontName="STSong-Light", fontSize=10.5, leading=16),
    }
    doc = SimpleDocTemplate(str(RES_DIR / "简历_李明_数据分析师.pdf"), pagesize=A4)
    story, lines = [], RESUME_TXT.splitlines()
    for line in lines:
        line = line.strip()
        if not line:
            story.append(Spacer(1, 4))
        elif line.startswith("【"):
            story.append(Paragraph(line, styles["h"]))
        elif line.startswith("李明"):
            story.append(Paragraph(line, styles["title"]))
        else:
            story.append(Paragraph(line, styles["p"]))
    doc.build(story)
    return RES_DIR / "简历_李明_数据分析师.pdf"


if __name__ == "__main__":
    for name, content in JDS.items():
        (JD_DIR / name).write_text(content, encoding="utf-8")
    (RES_DIR / "简历_李明_数据分析师.txt").write_text(RESUME_TXT, encoding="utf-8")
    pdf = make_pdf()
    print(f"已生成 {len(JDS)} 个JD -> {JD_DIR}")
    print(f"简历 txt + PDF -> {pdf}")
