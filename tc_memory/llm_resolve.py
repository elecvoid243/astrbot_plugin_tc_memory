"""从 AstrBot provider 解析 LLM 连接参数（纯逻辑，不依赖 AstrBot 类型）。"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ResolvedLLM:
    base_url: str
    api_key: str
    model: str


def provider_to_dict(provider: Any) -> dict:
    """把运行时 Provider 对象压成纯 dict（隔离 AstrBot 内部结构）。"""
    cfg = getattr(provider, "provider_config", {}) or {}
    meta = getattr(provider, "meta", None)
    meta_id = getattr(meta, "id", None) if meta else None
    return {
        "id": meta_id or cfg.get("id", ""),
        "api_base": cfg.get("api_base", ""),
        "key": cfg.get("key") or [],
        "model": cfg.get("model", ""),
        "model_config": cfg.get("model_config") or {},
    }


def providers_pending_initialization(context: Any, instances: list) -> bool:
    """框架是否处于「provider 配置已就绪、实例尚未创建」的冷启动窗口。

    冷启动顺序是 plugin_manager.reload() → provider_manager.initialize()，
    插件加载阶段只能拿到空实例。取不到框架内部结构时返回 False：宁可照常
    启动（降级运行）也不要无限等待 on_astrbot_loaded。
    """
    if instances:
        return False
    manager = getattr(context, "provider_manager", None)
    configs = getattr(manager, "providers_config", None)
    return isinstance(configs, list) and bool(configs)


def resolve_llm_from_providers(
    providers: list[dict], provider_id: str
) -> ResolvedLLM | None:
    """按 id 匹配 provider，解析 OpenAI 兼容连接参数。解析不出返回 None
    （调用方降级：服务端提炼不可用，召回/记录照常）。"""
    if not provider_id:
        return None
    for p in providers:
        if p.get("id") != provider_id:
            continue
        keys = p.get("key") or []
        api_key = keys[0] if keys else ""
        model = p.get("model") or (p.get("model_config") or {}).get("model") or ""
        base_url = (p.get("api_base") or "").strip()
        if not base_url or not api_key or not model:
            return None
        return ResolvedLLM(base_url=base_url, api_key=api_key, model=model)
    return None
