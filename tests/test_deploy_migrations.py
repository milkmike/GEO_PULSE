import os
import subprocess
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def _fake_deploy_env(tmp_path, *, head="aaaaaaaa", fail_first_migration=False):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    calls = tmp_path / "calls.log"
    attempts = tmp_path / "migration-attempts"
    head_file = tmp_path / "git-head"
    head_file.write_text(head)

    git = fake_bin / "git"
    git.write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        "case \"$*\" in\n"
        "  'diff --quiet'|'diff --cached --quiet'|'fetch -q origin main') exit 0 ;;\n"
        "  'rev-parse HEAD') cat \"$DEPLOY_TEST_HEAD\" ;;\n"
        "  'rev-parse origin/main') echo bbbbbbbb ;;\n"
        "  'merge --ff-only origin/main') echo bbbbbbbb > \"$DEPLOY_TEST_HEAD\" ;;\n"
        "  *) exit 90 ;;\n"
        "esac\n"
    )
    git.chmod(0o755)

    docker = fake_bin / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        "printf '%s\\n' \"$*\" >> \"$DEPLOY_TEST_CALLS\"\n"
        "if [ \"$*\" = 'compose run --rm migrate' ]; then\n"
        "  count=0\n"
        "  [ ! -f \"$DEPLOY_TEST_ATTEMPTS\" ] || count=\"$(cat \"$DEPLOY_TEST_ATTEMPTS\")\"\n"
        "  count=$((count + 1))\n"
        "  printf '%s' \"$count\" > \"$DEPLOY_TEST_ATTEMPTS\"\n"
        "  if [ \"${DEPLOY_TEST_FAIL_FIRST:-0}\" = 1 ] && [ \"$count\" = 1 ]; then exit 42; fi\n"
        "fi\n"
        "exit 0\n"
    )
    docker.chmod(0o755)

    date = fake_bin / "date"
    date.write_text("#!/usr/bin/env bash\necho 2026-07-15T12:00:00+03:00\n")
    date.chmod(0o755)

    nice = fake_bin / "nice"
    nice.write_text("#!/usr/bin/env bash\nshift 2\nexec \"$@\"\n")
    nice.chmod(0o755)

    ionice = fake_bin / "ionice"
    ionice.write_text("#!/usr/bin/env bash\nshift\nexec \"$@\"\n")
    ionice.chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "APP_DIR": str(tmp_path),
        "DEPLOY_LOG": str(tmp_path / "deploy.log"),
        "DEPLOY_TEST_CALLS": str(calls),
        "DEPLOY_TEST_ATTEMPTS": str(attempts),
        "DEPLOY_TEST_HEAD": str(head_file),
        "DEPLOY_TEST_FAIL_FIRST": "1" if fail_first_migration else "0",
    }
    return env, calls, attempts


def _run_auto_update(tmp_path, env):
    return subprocess.run(
        ["bash", str(ROOT / "deploy" / "auto-update.sh")],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_auto_update_retries_after_migration_failure_then_records_success(tmp_path):
    env, calls, attempts = _fake_deploy_env(
        tmp_path,
        fail_first_migration=True,
    )
    marker = tmp_path / ".deploy-state" / "last-successful-commit"

    first = _run_auto_update(tmp_path, env)
    assert first.returncode == 42
    assert (tmp_path / "git-head").read_text().strip() == "bbbbbbbb"
    assert not marker.exists()
    assert calls.read_text().splitlines() == ["compose run --rm migrate"]

    second = _run_auto_update(tmp_path, env)
    assert second.returncode == 0
    assert attempts.read_text() == "2"
    assert calls.read_text().splitlines() == [
        "compose run --rm migrate",
        "compose run --rm migrate",
        "compose build",
        "compose up -d",
    ]
    assert marker.read_text().strip() == "bbbbbbbb"

    third = _run_auto_update(tmp_path, env)
    assert third.returncode == 0
    assert attempts.read_text() == "2"
    assert len(calls.read_text().splitlines()) == 4


def test_auto_update_without_marker_deploys_current_head_once(tmp_path):
    env, calls, attempts = _fake_deploy_env(tmp_path, head="bbbbbbbb")
    marker = tmp_path / ".deploy-state" / "last-successful-commit"

    first = _run_auto_update(tmp_path, env)
    assert first.returncode == 0
    assert attempts.read_text() == "1"
    assert marker.read_text().strip() == "bbbbbbbb"
    assert calls.read_text().splitlines() == [
        "compose run --rm migrate",
        "compose build",
        "compose up -d",
    ]

    second = _run_auto_update(tmp_path, env)
    assert second.returncode == 0
    assert len(calls.read_text().splitlines()) == 3


def test_every_database_consumer_waits_for_successful_migration():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    services = compose["services"]

    consumers = {
        name
        for name, service in services.items()
        if "DATABASE_URL" in (service.get("environment") or {})
    }
    assert consumers
    for name in sorted(consumers):
        assert services[name]["depends_on"]["migrate"] == {
            "condition": "service_completed_successfully"
        }, name
