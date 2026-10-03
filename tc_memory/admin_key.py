"""本地实例 admin user_key 引导（local 模式面板登录用）。

standalone gateway 首启没有任何用户，面板登录需要 user_key。
策略（对齐官方 Docker 部署 start-memory-core.sh）：
- key 持久化在插件数据目录 admin-key 文件
- 每次启动先 verify 文件里的 key，合法则直接用（幂等）
- 失效（如 gateway 数据被清）则用同一 key 值重新 init-admin（接口尊重传入值）
- init-admin 返回 409 且 verify 不过 → 库里存在另一个 admin key，无法自动恢复，
  返回 None 由上层给出人工指引
"""

import secrets
import string
from pathlib import Path

from .client import TdMemoryClient
from .errors import TDAMError

KEY_PREFIX = "sk-mem-"
ADMIN_USERNAME = "admin"


def generate_key() -> str:
    alphabet = string.ascii_letters + string.digits
    return KEY_PREFIX + "".join(secrets.choice(alphabet) for _ in range(32))


async def ensure_admin_key(client: TdMemoryClient, key_file: Path) -> str | None:
    """确保本地实例存在可用 admin key，返回 key；无法自动恢复返回 None。"""
    existing = key_file.read_text().strip() if key_file.exists() else ""

    if existing:
        try:
            if await client.meta_auth_verify(existing):
                return existing
        except TDAMError:
            pass  # verify 服务异常也走重建路径
        # key 失效：用同一值重建（init-admin 尊重传入 user_key）
        try:
            await client.internal_init_admin(ADMIN_USERNAME, existing)
            return existing
        except TDAMError as e:
            if e.code == 409:
                return None
            raise

    key = generate_key()
    try:
        await client.internal_init_admin(ADMIN_USERNAME, key)
    except TDAMError as e:
        if e.code == 409:
            return None
        raise
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.write_text(key, encoding="utf-8")
    return key
