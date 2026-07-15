import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_failed_migration_stops_chain_and_is_not_recorded(tmp_path):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "001_ok.sql").write_text("SELECT 1;\n")
    (migrations / "002_fail.sql").write_text("SELECT broken;\n")
    (migrations / "003_must_not_run.sql").write_text("SELECT 3;\n")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "psql.log"
    fake_psql = fake_bin / "psql"
    fake_psql.write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        "printf '%s\\n' \"$*\" >> \"$FAKE_PSQL_LOG\"\n"
        "case \"$*\" in\n"
        "  *'-f '*002_fail.sql*) exit 23 ;;\n"
        "esac\n"
    )
    fake_psql.chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "FAKE_PSQL_LOG": str(log),
        "MIG_DIR": str(migrations),
    }
    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / "apply_migrations.sh")],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    calls = log.read_text()
    assert result.returncode == 23
    assert "-f " + str(migrations / "001_ok.sql") in calls
    assert "-f " + str(migrations / "002_fail.sql") in calls
    assert "003_must_not_run.sql" not in calls
    assert "INSERT INTO schema_migrations(filename) VALUES ('001_ok.sql')" in calls
    assert "INSERT INTO schema_migrations(filename) VALUES ('002_fail.sql')" not in calls
