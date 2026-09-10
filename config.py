"""全局配置：从 .env 读取 LLM 配置，未配置 key 时自动降级为 mock 模式。"""
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

PROVIDER_PRESETS = {
    # provider: (base_url, 默认模型)
    "generic": ("https://open.generic-endpoint.cn/api/paas/v4", "your-model-flash"),
    "generic-llm": ("https://api.generic-llm.com/v1", "generic-llm-chat"),
    "openai": ("https://api.openai.com/v1", "gpt-4o-mini"),
    "generic": ("https://api.generic.cn/v1", "generic-v1-8k"),
}


@dataclass
class Config:
    provider: str = "mock"
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    models: list = field(default_factory=list)  # 可切换模型列表（同一端点）
    fallback_model: str = ""       # 模型降级：主模型连续失败时自动切换
    privacy_mode: bool = False     # 隐私模式：会话不落盘（PRIVACY_MODE=1 开启）
    temperature: float = 0.3
    max_react_steps: int = 5          # 单个子任务内 ReAct 最大循环次数
    llm_max_retries: int = 3          # LLM 调用最大重试次数
    tool_max_retries: int = 2         # 工具调用最大重试次数
    max_tasks: int = 6                # 计划最多子任务数
    reports_dir: Path = ROOT / "data" / "reports"
    sessions_dir: Path = ROOT / "data" / "sessions"
    extra: dict = field(default_factory=dict)


def _env_number(name: str, default, kind=int):
    """容错读取数字类环境变量。
    改动：原先直接 int()/float() 解析 .env，一旦用户把 MAX_REACT_STEPS=abc 之类写错，
    load_config() 会在 import 阶段抛 ValueError，整个服务/CLI 直接起不来；
    这里改为解析失败回退默认值并打印提示。"""
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return kind(float(raw)) if kind is int else kind(raw)
    except (TypeError, ValueError):
        print(f"[config] {name}={raw!r} 不是合法数字，使用默认值 {default}")
        return default


def to_root_relative(path) -> str:
    """把本地路径统一转成「相对项目根 + 正斜杠」的形式，供 /files 静态路由与前端引用。
    改动：报告/图表原先用相对 CWD 的路径写盘（如 Path("data/reports/...")），
    从其他工作目录启动服务时文件会落到别处、/files 链接也会失效；改为以 ROOT 定位后
    再用本函数还原成与旧返回值完全一致的相对路径。"""
    p = Path(path)
    try:
        p = p.resolve()
    except OSError:
        return p.as_posix()
    try:
        return p.relative_to(ROOT).as_posix()
    except ValueError:
        return p.as_posix()  # 不在项目根下（如用户自定义目录）时退回绝对路径


def load_config() -> Config:
    provider = (os.getenv("LLM_PROVIDER") or "").strip().lower()
    api_key = os.getenv("LLM_API_KEY") or ""
    base_url = os.getenv("LLM_BASE_URL") or ""
    model = os.getenv("LLM_MODEL") or ""

    if not provider:
        # 自动探测：按常见厂商的环境变量兜底
        for env_name, prov in (("GENERIC_ENDPOINTAI_API_KEY", "generic"), ("GENERIC-LLM_API_KEY", "generic-llm"),
                               ("OPENAI_API_KEY", "openai"), ("GENERIC_ENDPOINT_API_KEY", "generic")):
            if os.getenv(env_name):
                provider, api_key = prov, os.getenv(env_name)
                break
        # 兜底：key/端点/模型 三项齐全即视为自定义 OpenAI 兼容端点（provider仅作展示标签）
        if not provider and api_key and base_url and model:
            provider = "custom"
            for prov, frag in (("generic", "generic-endpoint"), ("generic-llm", "generic-llm"),
                               ("generic", "generic"), ("generic", "generic-endpoint")):
                if frag in base_url:
                    provider = prov
                    break
    if provider and provider != "mock":
        preset_url, preset_model = PROVIDER_PRESETS.get(provider, ("", ""))
        base_url = base_url or preset_url
        model = model or preset_model
        if not api_key or not base_url or not model:
            print(f"[config] provider={provider} 配置不完整（缺 api_key/base_url/model），降级为 mock 模式")
            provider = "mock"
    if not provider:
        provider = "mock"

    cfg = Config(
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        model=model,
        fallback_model=os.getenv("LLM_FALLBACK_MODEL", ""),
        privacy_mode=os.getenv("PRIVACY_MODE", "").lower() in ("1", "true", "yes"),
        temperature=_env_number("LLM_TEMPERATURE", 0.3, float),
        # 下限护栏：ReAct/重试次数配成 0 或负数会导致任务一步不跑就判失败、
        # 或 LLM 调用一次都不发直接报错（_chat_retry 里 for 循环不执行）
        max_react_steps=max(1, _env_number("MAX_REACT_STEPS", 5)),
        llm_max_retries=max(1, _env_number("LLM_MAX_RETRIES", 3)),
        tool_max_retries=max(0, _env_number("TOOL_MAX_RETRIES", 2)),
    )
    # 可切换模型列表：LLM_MODELS=模型A,模型B,模型C（同一端点）；当前模型与备用模型自动并入并去重
    models = [m.strip() for m in os.getenv("LLM_MODELS", "").split(",") if m.strip()]
    for m in (model, cfg.fallback_model):
        if m and m not in models:
            models.insert(0, m)
    cfg.models = models
    if not cfg.models and cfg.model:
        cfg.models = [cfg.model]
    for d in (cfg.reports_dir, cfg.reports_dir / "charts", cfg.sessions_dir):
        d.mkdir(parents=True, exist_ok=True)
    return cfg
