"""受限的服务器日志读取器：仅允许读取配置中的日志源。"""

from __future__ import annotations

import re
import shlex
import subprocess
from collections.abc import Callable
from typing import Any

from app.config import config

_SOURCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_HOST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.:-]{0,253}$")
_REMOTE_PATH = re.compile(r"^/[A-Za-z0-9_./-]+$")


class ServerLogService:
    """通过 SSH 只读拉取 allowlist 中日志文件的末尾内容。"""

    def __init__(
        self,
        sources: list[dict[str, Any]] | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self.sources = sources if sources is not None else config.bugfix_log_sources
        self.runner = runner

    def available_sources(self) -> list[dict[str, str]]:
        return [
            {"id": source["id"], "name": source.get("name", source["id"])}
            for source in self.sources
            if self._is_valid(source)
        ]

    def fetch(self, source_id: str) -> dict[str, str]:
        source = next((item for item in self.sources if item.get("id") == source_id), None)
        if source is None or not self._is_valid(source):
            raise ValueError("未找到可用日志源；请由管理员在 BUGFIX_LOG_SOURCES 中预先配置。")

        line_count = min(max(int(source.get("tail_lines", config.bugfix_log_tail_lines)), 20), 5000)
        target = f"{source.get('user', 'root')}@{source['host']}"
        remote_command = f"tail -n {line_count} -- {shlex.quote(source['log_path'])}"
        command = ["ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={config.bugfix_log_fetch_timeout_seconds}"]
        if source.get("port"):
            command.extend(["-p", str(int(source["port"]))])
        command.extend([target, remote_command])
        try:
            result = self.runner(
                command,
                capture_output=True,
                text=True,
                timeout=config.bugfix_log_fetch_timeout_seconds + 3,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RuntimeError(f"读取服务器日志失败：{error}") from error
        if result.returncode != 0:
            detail = (result.stderr or "SSH 命令返回非零状态").strip()[:500]
            raise RuntimeError(f"读取服务器日志失败：{detail}")
        output = result.stdout.strip()
        if not output:
            raise RuntimeError("服务器日志为空，或当前账号没有读取权限。")
        return {"source_id": source_id, "source_name": source.get("name", source_id), "log": output[-30000:]}

    @staticmethod
    def _is_valid(source: dict[str, Any]) -> bool:
        return bool(
            isinstance(source.get("id"), str)
            and _SOURCE_ID.fullmatch(source["id"])
            and isinstance(source.get("host"), str)
            and _HOST.fullmatch(source["host"])
            and isinstance(source.get("log_path"), str)
            and _REMOTE_PATH.fullmatch(source["log_path"])
            and (not source.get("user") or _SOURCE_ID.fullmatch(str(source["user"])))
        )


server_log_service = ServerLogService()
