from __future__ import annotations

import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
START_SCRIPT = PROJECT_ROOT / "start-services.ps1"
VITE_CONFIG = PROJECT_ROOT / "tiktok_bot_console" / "ui" / "vite.config.ts"


def _run_dry_run(*extra_args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(START_SCRIPT),
            "-DryRun",
            *extra_args,
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        check=False,
    )


def test_dry_run_describes_deterministic_backend_fallback_and_bound_frontend():
    result = _run_dry_run()

    assert result.returncode == 0, result.stderr
    output = result.stdout
    assert "Backend port candidates: 8000,8400,8401,8402,8403,8404,8405,8406,8407,8408,8409" in output
    assert "python -m uvicorn tiktok_bot_api.main:app --env-file .env --port <auto>" in output
    assert "VITE_API_BASE=http://127.0.0.1:<auto>" in output
    assert "npm.cmd run dev -- --host 127.0.0.1 --port 5173 --strictPort" in output
    assert "http://127.0.0.1:5173/__tiktok-bot-runtime" in output


def test_dry_run_accepts_an_explicit_backend_port():
    result = _run_dry_run("-BackendPort", "8407")

    assert result.returncode == 0, result.stderr
    output = result.stdout
    assert "python -m uvicorn tiktok_bot_api.main:app --env-file .env --port 8407" in output
    assert "VITE_API_BASE=http://127.0.0.1:8407" in output
    assert "http://127.0.0.1:8407/api/health" in output


def test_script_probes_real_bindability_and_validates_the_frontend_target():
    source = START_SCRIPT.read_text(encoding="utf-8")

    assert "Test-PortBindable" in source
    assert "System.Net.Sockets.TcpListener" in source
    assert "Test-BackendService" in source
    assert "Get-FrontendRuntime" in source
    assert "/__tiktok-bot-runtime" in source
    assert "$env:VITE_API_BASE = $BackendBaseUrl" in source
    assert '"--strictPort"' in source


def test_vite_exposes_a_no_store_runtime_marker_for_backend_binding():
    source = VITE_CONFIG.read_text(encoding="utf-8")

    assert "/__tiktok-bot-runtime" in source
    assert "tiktok-b2b-bot-ui" in source
    assert "application/json" in source
    assert "no-store" in source
    assert "apiBase" in source
