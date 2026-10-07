"""迁移脚本测试：在临时副本 DB 上验证（含 FTS 同步、profiles 改名、备份、dry-run）。"""

import sqlite3
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from migrate_isolation import migrate


def build_fixture(tmp_path: Path) -> Path:
    """造一个与真实库同构的最小 vectors.db + profiles 目录。"""
    gw = tmp_path / "gateway-data"
    gw.mkdir()
    db = gw / "vectors.db"
    conn = sqlite3.connect(str(db))
    for t in ("l1_records", "l0_conversations", "l1_fts", "l0_fts"):
        conn.execute(
            f"CREATE TABLE {t}(record_id TEXT, team_id TEXT, agent_id TEXT, user_id TEXT)"
        )
        conn.execute(
            f"INSERT INTO {t} VALUES ('r1', 'default', 'default', 'u_astrbot')"
        )
        conn.execute(
            f"INSERT INTO {t} VALUES ('r2', 'default', 'other', 'u_astrbot')"
        )  # 不匹配 old_agent，应保留
    conn.commit()
    conn.close()
    old_dir = (
        gw / "profiles" / urllib.parse.quote("team:default|agent:default", safe="")
    )
    (old_dir / "scene_blocks").mkdir(parents=True)
    (old_dir / "persona.md").write_text("画像", encoding="utf-8")
    return gw


def test_migrate_updates_all_tables_and_renames_profiles(tmp_path):
    gw = build_fixture(tmp_path)

    result = migrate(gw, new_team="team-new", new_agent="agt-new", new_user="usr-admin")

    assert result["tables"] == {
        "l1_records": 1,
        "l0_conversations": 1,
        "l1_fts": 1,
        "l0_fts": 1,
    }
    conn = sqlite3.connect(str(gw / "vectors.db"))
    for t in ("l1_records", "l0_conversations", "l1_fts", "l0_fts"):
        moved = conn.execute(
            f"SELECT team_id, agent_id, user_id FROM {t} WHERE record_id='r1'"
        ).fetchone()
        kept = conn.execute(f"SELECT team_id FROM {t} WHERE record_id='r2'").fetchone()
        assert moved == ("team-new", "agt-new", "usr-admin"), (t, moved)
        assert kept == ("default",), (t, kept)  # 不匹配的行不受影响
    conn.close()

    new_dir = (
        gw / "profiles" / urllib.parse.quote("team:team-new|agent:agt-new", safe="")
    )
    assert (new_dir / "persona.md").read_text(encoding="utf-8") == "画像"
    assert Path(result["backup"]).is_dir()
    assert (Path(result["backup"]) / "vectors.db").is_file()


def test_migrate_dry_run_changes_nothing(tmp_path):
    gw = build_fixture(tmp_path)

    result = migrate(gw, new_team="t", new_agent="a", new_user="u", dry_run=True)

    assert result["tables"]["l1_records"] == 1  # 统计照旧
    assert result["backup"] is None
    conn = sqlite3.connect(str(gw / "vectors.db"))
    row = conn.execute("SELECT team_id FROM l1_records WHERE record_id='r1'").fetchone()
    conn.close()
    assert row == ("default",)  # 数据未动
    assert not (gw / ".backup").exists()
