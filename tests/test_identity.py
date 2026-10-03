from tc_memory.config import PluginConfig, config_from_astrbot
from tc_memory.identity import resolve_identity


def test_config_defaults_local_mode():
    cfg = config_from_astrbot({})
    assert cfg.mode == "local"
    assert cfg.core_endpoint == "http://127.0.0.1:8420"
    assert cfg.team_id == "default"
    assert cfg.recall_enabled is True
    assert cfg.persist_injected_memory is False
    assert cfg.recall_max_results == 5
    assert cfg.recall_timeout_sec == 3
    assert cfg.user_id_map == {}
    # local 模式内嵌 gateway 的 LLM 配置（standalone 提炼需要）
    assert cfg.local_llm_provider_id == ""  # 未选择时由 on_loaded 告警提示
    # 引擎参数常用子集（与包内 standalone yaml 默认值一致）
    assert cfg.engine_extraction_enabled is True
    assert cfg.engine_pipeline_every_n == 5
    assert cfg.engine_persona_trigger_every_n == 50
    assert cfg.engine_recall_max_results == 5
    assert cfg.engine_recall_score_threshold == 0.3
    assert cfg.panel_enabled is True


def test_config_reads_explicit_values():
    cfg = config_from_astrbot(
        {
            "mode": "server",
            "team_id": "t-real",
            "agent_id": "a-real",
            "user_id_map": {"qq_1": "u_zhangsan"},
        }
    )
    assert cfg.mode == "server"
    assert cfg.team_id == "t-real"
    assert cfg.user_id_map == {"qq_1": "u_zhangsan"}


def make_cfg(**overrides) -> PluginConfig:
    base = {"team_id": "t1", "agent_id": "a1"}
    base.update(overrides)
    return config_from_astrbot(base)


def test_resolve_default_user_prefix():
    identity = resolve_identity("qq_12345", "qq:FriendMessage:12345", make_cfg())
    assert identity.ids.user_id == "u_qq_12345"
    assert identity.ids.team_id == "t1"
    assert identity.ids.agent_id == "a1"


def test_resolve_user_id_map_hit():
    identity = resolve_identity(
        "qq_1", "qq:FriendMessage:1", make_cfg(user_id_map={"qq_1": "u_zhangsan"})
    )
    assert identity.ids.user_id == "u_zhangsan"


def test_resolve_empty_sender_falls_back_default():
    identity = resolve_identity("", "qq:FriendMessage:1", make_cfg())
    assert identity.ids.user_id == "default"


def test_session_id_passthrough():
    identity = resolve_identity("s", "telegram:GroupMessage:99", make_cfg())
    assert identity.session_id == "telegram:GroupMessage:99"
