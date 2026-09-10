"""Agent 预设（standard / minimal / ptc / creative）。

设计权衡
--------
1. 预设**只调整既有编排器的可调参数**（步数、温度、是否校验），不复制任何业务规则：
   规划、ReAct 步进、工具重试/熔断、产物接线全部仍由 `core/agent.py` 决定。
2. `apply_preset` 一律用 `cfg.model_copy(update=...)` 返回**副本**，绝不改全局单例配置——
   同一进程内不同会话可能并发使用不同预设，写全局会互相污染。
3. 预设特有的开关（是否启用 `check` 校验节点、是否要求精简输出）不是 Settings 的声明字段，
   直接进 `model_copy(update=...)` 会被 pydantic 静默丢弃，因此先复制再旁路挂载，
   并用 `getattr(cfg, "preset_check", False)` 这种"缺省即关闭"的读法消费。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from config import Settings

# 预设 id 常量：API 层（/api/agent/run 的 preset 字段）与前端的取值必须与此一致
STANDARD = "standard"
MINIMAL = "minimal"
PTC = "ptc"
CREATIVE = "creative"


@dataclass(frozen=True)
class Preset:
    """一个预设的元数据 + 覆盖参数。`params` 原样回给 `/api/agent/presets` 展示。"""

    id: str
    name: str
    description: str
    params: dict[str, Any] = field(default_factory=dict)


# —— 预设定义 ——
# standard：完全使用配置默认值（不改任何字段），作为"基线"便于对比其它预设
# minimal ：只降步数并要求精简输出，适合快速问答/低成本场景
# ptc     ：步数与默认一致，但打开编排层的「校验节点」（规划-工具-校验）
# creative：提高温度并放宽步数上限，适合头脑风暴/多方案生成
PRESETS: dict[str, Preset] = {
    STANDARD: Preset(
        id=STANDARD,
        name="标准",
        description="使用配置默认值：步数与温度均取自环境变量，适合日常求职分析。",
        params={},
    ),
    MINIMAL: Preset(
        id=MINIMAL,
        name="极简",
        description="最少步数快速收尾，输出精简，适合确认材料或轻量问答。",
        params={"max_react_steps": 2, "temperature": 0.2, "terse_output": True},
    ),
    PTC: Preset(
        id=PTC,
        name="PTC",
        description="规划–工具–校验：在调度与综合之间插入校验节点，产物不满足契约时补救一轮。",
        params={"check_node": True, "max_check_retries": 1},
    ),
    CREATIVE: Preset(
        id=CREATIVE,
        name="创造",
        description="提高采样温度并放宽步数，适合多方案对比与开放式建议。",
        params={"temperature": 0.8, "max_react_steps": 8},
    ),
}

DEFAULT_PRESET = STANDARD

# 步数下限/上限护栏：预设写错（或环境变量异常）也不得让编排器空转或无限循环
_MIN_STEPS = 1
_MAX_STEPS = 12

# 要求精简输出时附加到用户消息的软约束（只影响提示，不改业务规则）
TERSE_SUFFIX = "\n\n（请用精简中文回答：要点式、每条不超过两行，不展开推理过程。）"


def resolve_preset(preset_id: str | None) -> Preset:
    """取预设定义；未知 id 回落 `standard`（API 层不应因参数拼错而 500）。"""
    key = (preset_id or "").strip().lower()
    return PRESETS.get(key) or PRESETS[DEFAULT_PRESET]


def list_presets() -> list[dict]:
    """供 `GET /api/agent/presets` 直接返回。"""
    return [{"id": p.id, "name": p.name, "description": p.description, "params": dict(p.params)}
            for p in PRESETS.values()]


def apply_preset(cfg: Settings, preset_id: str | None) -> Settings:
    """按预设返回配置**副本**。

    - 未在 `params` 中出现的字段一律保持配置原值（standard 即完全不动）；
    - 数值经过护栏裁剪，避免 `max_react_steps=0` 导致 `agent._run_task` 一步不跑；
    - 预设开关（check_node/terse_output/max_check_retries）旁路挂载到副本上，
      不写回全局，也不污染 `load_config()` 的缓存单例。
    """
    preset = resolve_preset(preset_id)
    update: dict[str, Any] = {}

    if "temperature" in preset.params:
        try:
            update["temperature"] = max(0.0, min(2.0, float(preset.params["temperature"])))
        except (TypeError, ValueError):
            pass  # 非法温度保持原值，不因预设配置写错而中断
    if "max_react_steps" in preset.params:
        try:
            steps = int(preset.params["max_react_steps"])
            update["max_react_steps"] = max(_MIN_STEPS, min(_MAX_STEPS, steps))
        except (TypeError, ValueError):
            pass

    new_cfg = cfg.model_copy(update=update) if update else cfg.model_copy()

    # 旁路挂载预设开关：pydantic 副本的 __dict__ 可直接写，不会触发校验/额外字段限制
    flags = {
        "preset_id": preset.id,
        "preset_name": preset.name,
        "preset_check": bool(preset.params.get("check_node", False)),
        "preset_max_check_retries": int(preset.params.get("max_check_retries", 0) or 0),
        "terse_output": bool(preset.params.get("terse_output", False)),
    }
    for key, value in flags.items():
        try:
            object.__setattr__(new_cfg, key, value)
        except Exception:  # 极端情况下（模型被冻结）忽略：预设仍按已更新的字段生效
            pass
    return new_cfg


def preset_flag(cfg: Settings, name: str, default: Any = None) -> Any:
    """读取旁路挂载的预设开关（未挂载时返回默认值）。"""
    return getattr(cfg, name, default)


__all__ = ["Preset", "PRESETS", "DEFAULT_PRESET", "TERSE_SUFFIX", "STANDARD", "MINIMAL",
           "PTC", "CREATIVE", "apply_preset", "resolve_preset", "list_presets", "preset_flag"]
