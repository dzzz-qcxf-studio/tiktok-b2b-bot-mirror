from __future__ import annotations

import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
START_SCRIPT = PROJECT_ROOT / "start-services.ps1"


def test_start_services_dry_run_describes_both_services_without_starting_them():
    assert START_SCRIPT.exists(), "项目根目录应提供一键启动脚本"

    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(START_SCRIPT),
            "-DryRun",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    output = result.stdout
    assert "python -m uvicorn tiktok_bot_api.main:app --env-file .env --port 8000" in output
    assert "npm.cmd run dev -- --host 127.0.0.1" in output
    assert "http://127.0.0.1:8000/api/health" in output
    assert "http://127.0.0.1:5173" in output


def test_start_services_uses_hidden_processes_logs_and_health_waits():
    source = START_SCRIPT.read_text(encoding="utf-8")

    assert "Start-Process" in source
    assert "-WindowStyle Hidden" in source
    assert "-RedirectStandardOutput" in source
    assert "-RedirectStandardError" in source
    assert "Wait-ServiceHealth" in source
    assert "data\\logs" in source
