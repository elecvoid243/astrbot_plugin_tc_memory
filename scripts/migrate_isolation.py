"""旧隔离桶 → 面板桶的一次性迁移（local 模式对齐面板时使用）。

背景：插件早期用 (team=default, agent=default, user=u_xxx) 写入；对齐面板后
改用面板桶 (team/agent/owner_user)。本脚本把历史数据搬到新桶，含：
- vectors.db：l1_records / l0_conversations 及其 FTS 镜像的隔离列
- profiles/：L2 场景与 L3 画像的目录名（URL 编码的 team|agent）

安全约定：
- 运行前必须停掉 gateway（脚本用排他锁探测，占用则中止）
- 自动备份 vectors.db 三个文件到 gateway-data/.backup/migrate-<ts>/
- --dry-run 只打印将要执行的变更

用法：
  python migrate_isolation.py --gateway-data <dir> \\
      --new-team team-xxx --new-agent agt-yyy --new-user usr-zzz \\
      [--old-team default --old-agent default] [--dry-run]
"""

import argparse
import shutil
import sqlite3
import sys
import time
import urllib.parse
from pathlib import Path

DB_NAME = "vectors.db"
FTS_TABLES = ("l1_fts", "l0_fts")
BASE_TABLES = ("l1_records", "l0_conversations")


def _profile_dir_name(team_id: str, agent_id: str) -> str:
    return urllib.parse.quote(f"team:{team_id}|agent:{agent_id}", safe="")


def _check_not_locked(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path), timeout=1)
    try:
        conn.execute("BEGIN EXCLUSIVE")
        conn.rollback()
    except sqlite3.OperationalError as e:
        raise SystemExit(f"数据库被占用（gateway 未停止？）：{e}") from e
    finally:
        conn.close()


def _backup(db_path: Path) -> Path:
    ts = time.strftime("%Y%m%d-%H%M%S")
    dest = db_path.parent / ".backup" / f"migrate-{ts}"
    dest.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        src = Path(str(db_path) + suffix)
        if src.exists():
            shutil.copy2(src, dest / src.name)
    return dest


def migrate(
    gateway_data: Path,
    *,
    new_team: str,
    new_agent: str,
    new_user: str,
    old_team: str = "default",
    old_agent: str = "default",
    dry_run: bool = False,
) -> dict:
    db_path = gateway_data / DB_NAME
    if not db_path.exists():
        raise SystemExit(f"未找到 {db_path}")

    if dry_run:
        _check_not_locked(db_path)
    else:
        _check_not_locked(db_path)

    result = {"tables": {}, "profiles_renamed": None, "backup": None}
    if not dry_run:
        result["backup"] = str(_backup(db_path))

    conn = sqlite3.connect(str(db_path))
    try:
        for table in BASE_TABLES + FTS_TABLES:
            n = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE team_id=? AND agent_id=?",
                (old_team, old_agent),
            ).fetchone()[0]
            result["tables"][table] = n
            if n and not dry_run:
                conn.execute(
                    f"UPDATE {table} SET team_id=?, agent_id=?, user_id=? "
                    f"WHERE team_id=? AND agent_id=?",
                    (new_team, new_agent, new_user, old_team, old_agent),
                )
        if not dry_run:
            conn.commit()
    finally:
        conn.close()

    # profiles 目录：L2/L3 的归属
    profiles = gateway_data / "profiles"
    old_dir = profiles / _profile_dir_name(old_team, old_agent)
    new_dir = profiles / _profile_dir_name(new_team, new_agent)
    if old_dir.exists():
        result["profiles_renamed"] = f"{old_dir.name} → {new_dir.name}"
        if not dry_run:
            if new_dir.exists():
                raise SystemExit(f"目标目录已存在，拒绝覆盖：{new_dir}（请人工合并）")
            old_dir.rename(new_dir)
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="旧隔离桶 → 面板桶迁移")
    ap.add_argument("--gateway-data", required=True, type=Path)
    ap.add_argument("--new-team", required=True)
    ap.add_argument("--new-agent", required=True)
    ap.add_argument("--new-user", required=True)
    ap.add_argument("--old-team", default="default")
    ap.add_argument("--old-agent", default="default")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    result = migrate(
        args.gateway_data,
        new_team=args.new_team,
        new_agent=args.new_agent,
        new_user=args.new_user,
        old_team=args.old_team,
        old_agent=args.old_agent,
        dry_run=args.dry_run,
    )
    print("备份:", result["backup"] or "(dry-run 不备份)")
    for t, n in result["tables"].items():
        print(f"  {t}: 迁移 {n} 行")
    print("profiles 目录:", result["profiles_renamed"] or "(无需迁移)")
    print("完成" + ("（dry-run）" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
