import sys

"""从 AstrBot provider 配置解析 LLM 连接参数（local 模式 standalone 提炼用）。

插件配置里选 provider_id，启动时解析出 base_url/api_key/model，
避免用户手动重复填写——provider 里已有一份。
"""

from tc_memory.launcher import LocalGatewayLauncher
from tc_memory.llm_resolve import (
    ResolvedLLM,
    provider_to_dict,
    resolve_llm_from_providers,
)


def test_resolve_from_provider_dicts():
    providers = [
        {
            "id": "deepseek-chat",
            "api_base": "https://api.deepseek.com/v1",
            "key": ["sk-aaa"],
            "model": "deepseek-chat",
        },
        {
            "id": "kimi",
            "api_base": "https://api.moonshot.cn/v1",
            "key": ["sk-bbb"],
            "model_config": {"model": "kimi-k2"},
        },
    ]
    r = resolve_llm_from_providers(providers, "deepseek-chat")
    assert r == ResolvedLLM(
        base_url="https://api.deepseek.com/v1",
        api_key="sk-aaa",
        model="deepseek-chat",
    )
    # model 兼容两种位置：顶层 model 或 model_config.model
    r2 = resolve_llm_from_providers(providers, "kimi")
    assert r2.model == "kimi-k2"


def test_resolve_unknown_id_returns_none():
    assert resolve_llm_from_providers([], "missing") is None


def test_resolve_empty_key_list_returns_none():
    providers = [{"id": "x", "api_base": "http://a/v1", "key": [], "model": "m"}]
    assert resolve_llm_from_providers(providers, "x") is None


def test_provider_to_dict_extracts_config():
    class FakeMeta:
        id = "p1"

    class FakeProvider:
        meta = FakeMeta()
        provider_config = {  # noqa: RUF012  # 测试替身，类属性即可
            "api_base": "http://a/v1",
            "key": ["sk-x"],
            "model": "m1",
        }

    d = provider_to_dict(FakeProvider())
    assert d == {
        "id": "p1",
        "api_base": "http://a/v1",
        "key": ["sk-x"],
        "model": "m1",
        "model_config": {},
    }


def test_launcher_set_env_overrides_at_spawn(tmp_path):
    """provider 解析结果在 spawn 前注入 env，覆盖初始值。"""
    launcher = LocalGatewayLauncher(
        command=[sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=tmp_path,
        env={"TDAI_LLM_API_KEY": "old"},
        health_url="http://127.0.0.1:1/health",
        log_path=None,
        startup_timeout_sec=0.5,
    )
    launcher.set_env({"TDAI_LLM_API_KEY": "new-key"})
    assert launcher._env["TDAI_LLM_API_KEY"] == "new-key"
