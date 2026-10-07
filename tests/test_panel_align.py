"""面板身份自动对齐测试（local 模式：插件采用面板的 team/agent/user 三元组）。"""

from tc_memory.align import resolve_panel_identities
from tc_memory.cache import TTLCache
from tc_memory.capture import CaptureBuffer
from tc_memory.client import IsolationIds
from tc_memory.config import config_from_astrbot
from tc_memory.runtime import PluginRuntime

_UNSET = object()


class StubMeta:
    def __init__(self, teams=_UNSET, agents=_UNSET, user=_UNSET):
        self._teams = (
            [{"team_id": "team-abc", "name": "默认团队"}] if teams is _UNSET else teams
        )
        self._agents = (
            [{"agent_id": "agt-xyz", "name": "default-agent-admin"}]
            if agents is _UNSET
            else agents
        )
        self._user = (
            {"user_id": "usr-admin", "user_type": "system_admin"}
            if user is _UNSET
            else user
        )

    def __init_extra__(self):
        pass

    async def meta_user_of_key(self, user_key):
        return self._user

    async def meta_team_list(self, user_id, user_key):
        assert user_id and user_key  # 真实契约：body 带 user_id、header 带 user-key
        return self._teams

    async def meta_agent_list(self, team_id, user_key):
        assert team_id and user_key
        return self._agents


async def test_resolve_panel_identities_full():
    ids = await resolve_panel_identities(StubMeta(), "sk-mem-x")
    assert ids == IsolationIds(
        team_id="team-abc", agent_id="agt-xyz", user_id="usr-admin"
    )


async def test_resolve_panel_identities_empty_returns_none():
    assert await resolve_panel_identities(StubMeta(teams=[]), "k") is None
    assert await resolve_panel_identities(StubMeta(agents=[]), "k") is None
    assert await resolve_panel_identities(StubMeta(user=None), "k") is None


def make_runtime(adopted=None):
    rt = PluginRuntime(
        cfg=config_from_astrbot({"mode": "local"}),
        core=StubMeta(),
        knowledge=None,
        cache=TTLCache(),
        buffer=CaptureBuffer(),
    )
    rt.adopted_ids = adopted
    return rt


def test_identity_for_uses_adopted_ids():
    rt = make_runtime(IsolationIds("team-abc", "agt-xyz", "usr-admin"))
    ident = rt.identity_for("qq_1", "qq:F:1")
    assert ident.ids.team_id == "team-abc"
    assert ident.ids.agent_id == "agt-xyz"
    assert ident.ids.user_id == "usr-admin"  # 面板桶：固定 admin user
    assert ident.session_id == "qq:F:1"


def test_identity_for_falls_back_without_adoption():
    rt = make_runtime(adopted=None)
    ident = rt.identity_for("qq_1", "qq:F:1")
    assert ident.ids.team_id == "default"
    assert ident.ids.user_id == "u_qq_1"
