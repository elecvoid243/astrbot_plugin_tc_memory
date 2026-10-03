"""插件配置模型。

AstrBotConfig 是 dict 子类，config_from_astrbot 用 dict 接口读取，
因此测试与运行时可共用同一条路径。缺省值与 _conf_schema.json 保持一致。
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PluginConfig:
    mode: str = "local"
    core_endpoint: str = "http://127.0.0.1:8420"
    core_api_key: str = "local"
    service_id: str = "default"
    team_id: str = "default"
    agent_id: str = "default"
    knowledge_enabled: bool = False
    knowledge_endpoint: str = "http://127.0.0.1:8421"
    knowledge_wiki_id: str = ""
    knowledge_codegraph_id: str = ""
    recall_enabled: bool = True
    capture_enabled: bool = True
    persist_injected_memory: bool = False
    recall_max_results: int = 5
    recall_timeout_sec: int = 3
    user_id_map: dict = field(default_factory=dict)
    # local 模式：standalone 提炼所用 LLM，从 AstrBot provider 中选择（对用户透明）
    local_llm_provider_id: str = ""
    # local 模式：引擎参数常用子集（合并进包内 standalone yaml 的 memory.* 段）
    engine_extraction_enabled: bool = True
    engine_pipeline_every_n: int = 5
    engine_persona_trigger_every_n: int = 50
    engine_recall_max_results: int = 5
    engine_recall_score_threshold: float = 0.3


def config_from_astrbot(cfg: dict[str, Any]) -> PluginConfig:
    """从 AstrBot 配置 dict 构建 PluginConfig；缺省值即 local 模式开箱即用。"""
    kwargs = {}
    for f in PluginConfig.__dataclass_fields__.values():
        if f.name in cfg and cfg[f.name] is not None:
            kwargs[f.name] = cfg[f.name]
    return PluginConfig(**kwargs)
