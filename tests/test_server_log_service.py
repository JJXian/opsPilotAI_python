import subprocess

import pytest

from app.services.server_log_service import ServerLogService


def test_fetch_reads_only_preconfigured_source():
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="Traceback (most recent call last):\nValueError: bad", stderr="")

    service = ServerLogService(
        [{"id": "prod-api", "name": "生产 API", "host": "10.0.0.8", "user": "ops", "log_path": "/var/log/api/error.log"}],
        runner=runner,
    )

    result = service.fetch("prod-api")

    assert result["source_name"] == "生产 API"
    assert result["log"].startswith("Traceback")
    assert calls[0][0][-2] == "ops@10.0.0.8"
    assert "tail -n" in calls[0][0][-1]


def test_fetch_rejects_unknown_or_unsafe_source():
    service = ServerLogService([{"id": "unsafe", "host": "host;id", "log_path": "/var/log/app.log"}])

    assert service.available_sources() == []
    with pytest.raises(ValueError, match="未找到"):
        service.fetch("unsafe")
