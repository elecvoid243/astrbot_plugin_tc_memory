"""admin user_key 引导测试（local 模式面板登录用）。"""

from tc_memory.admin_key import ensure_admin_key, generate_key
from tc_memory.errors import TDAMError


class StubMetaClient:
    def __init__(self, verify_valid=False, init_conflict=False):
        self.verify_valid = verify_valid
        self.init_conflict = init_conflict
        self.verify_calls = []
        self.init_calls = []

    async def meta_auth_verify(self, user_key: str) -> bool:
        self.verify_calls.append(user_key)
        return self.verify_valid and user_key in [c[1] for c in self.init_calls]

    async def internal_init_admin(self, username: str, user_key: str) -> dict:
        self.init_calls.append((username, user_key))
        if self.init_conflict:
            raise TDAMError(409, "already_initialized")
        return {"user_id": "usr_admin", "user_key": user_key}


def test_generate_key_format():
    key = generate_key()
    assert key.startswith("sk-mem-")
    assert len(key) == len("sk-mem-") + 32
    assert generate_key() != generate_key()  # 随机性


async def test_first_boot_generates_and_persists(tmp_path):
    client = StubMetaClient()
    key_file = tmp_path / "admin-key"

    key = await ensure_admin_key(client, key_file)

    assert key is not None and key.startswith("sk-mem-")
    assert client.init_calls == [("admin", key)]
    assert key_file.read_text().strip() == key


async def test_second_boot_reuses_valid_key(tmp_path):
    key_file = tmp_path / "admin-key"
    key_file.write_text("sk-mem-" + "a" * 32)
    client = StubMetaClient(verify_valid=True)
    # verify 只在 init 过后认 key；这里模拟「文件 key 与库中一致」
    client.init_calls.append(("admin", "sk-mem-" + "a" * 32))

    key = await ensure_admin_key(client, key_file)

    assert key == "sk-mem-" + "a" * 32
    assert len(client.init_calls) == 1  # 未再 init（只有预设的那次）


async def test_stale_key_reinits_with_same_value(tmp_path):
    """数据被清但 key 文件还在：verify 失败 → 用同一 key 值重新 init-admin
    （init-admin 尊重传入的 user_key）。"""
    old_key = "sk-mem-" + "b" * 32
    key_file = tmp_path / "admin-key"
    key_file.write_text(old_key)
    client = StubMetaClient(verify_valid=False)

    key = await ensure_admin_key(client, key_file)

    assert key == old_key
    assert client.init_calls == [("admin", old_key)]


async def test_conflict_returns_none(tmp_path):
    """库里已有 admin 且 key 与文件不符（如文件丢失后重建）→ None，由上层告警。"""
    key_file = tmp_path / "admin-key"
    key_file.write_text("sk-mem-" + "c" * 32)
    client = StubMetaClient(verify_valid=False, init_conflict=True)

    assert await ensure_admin_key(client, key_file) is None
