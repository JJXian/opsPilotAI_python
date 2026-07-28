"""Bug 修复 Agent 的只读代码诊断工具。"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from app.config import config

PYTHON_FRAME_PATTERN = re.compile(
    r'^\s*File "(?P<path>.+?)", line (?P<line>\d+), in (?P<function>.+?)\s*$', re.MULTILINE
)
JAVA_FRAME_PATTERN = re.compile(
    r"^\s*at\s+(?P<class>[\w.$]+)\.(?P<function>[\w$<>]+)\((?P<file>[\w$]+\.java):(?P<line>\d+)\)\s*$",
    re.MULTILINE,
)
EXCEPTION_PATTERN = re.compile(
    r"^(?:Caused by:\s*)?(?P<type>[A-Za-z_][\w.$]*(?:Error|Exception|Warning|Interrupt)|AssertionError):?\s*(?P<message>.*)$"
)
SOURCE_GLOBS = (
    "*.py", "*.java", "*.kt", "*.xml", "*.properties", "*.yml", "*.yaml", "*.json", "*.toml", "*.ini", "*.cfg",
)


class BugFixRepository:
    """受限于项目根目录的只读仓库访问器。"""

    def __init__(self, root: str | Path | None = None) -> None:
        configured_root = root or config.bugfix_repository_root
        self.root = Path(configured_root).resolve()

    def parse_python_traceback(self, log: str) -> dict[str, Any]:
        """兼容旧调用；同时支持 Python 与 Java/Spring Boot 异常堆栈。"""
        frames = []
        language = "python"
        for match in PYTHON_FRAME_PATTERN.finditer(log):
            resolved = self._resolve_path(match.group("path"))
            frames.append(
                {
                    "path": self._display_path(resolved, match.group("path")),
                    "line": int(match.group("line")),
                    "function": match.group("function").strip(),
                    "in_repository": resolved is not None and resolved.is_file(),
                }
            )

        if not frames:
            language = "java"
            for match in JAVA_FRAME_PATTERN.finditer(log):
                resolved = self._resolve_java_source(match.group("class"), match.group("file"))
                frames.append(
                    {
                        "path": self._display_path(resolved, match.group("file")),
                        "line": int(match.group("line")),
                        "function": match.group("function"),
                        "in_repository": resolved is not None and resolved.is_file(),
                    }
                )

        exception_type = "UnknownError"
        exception_message = ""
        for line in reversed([item.strip() for item in log.splitlines() if item.strip()]):
            match = EXCEPTION_PATTERN.match(line)
            if match:
                exception_type = match.group("type")
                exception_message = match.group("message")
                break

        return {
            "language": language,
            "exception_type": exception_type,
            "exception_message": exception_message,
            "frames": frames,
            "repository_frames": [frame for frame in frames if frame["in_repository"]],
        }

    def _resolve_java_source(self, class_name: str, filename: str) -> Path | None:
        """从 Java 包名优先定位源码，兼容标准 Maven/Gradle 目录结构。"""
        package_path = Path(*class_name.split(".")[:-1]) / filename
        for source_root in (self.root / "src/main/java", self.root / "src/test/java"):
            candidate = (source_root / package_path).resolve()
            if candidate.is_file():
                return candidate
        matches = list(self.root.rglob(filename))
        for candidate in matches:
            if any(part in {".git", ".venv", "target", "build", "node_modules"} for part in candidate.parts):
                continue
            try:
                candidate.relative_to(self.root)
                return candidate.resolve()
            except ValueError:
                continue
        return None

    def read_context(self, path: str, line: int, radius: int = 10) -> dict[str, Any]:
        resolved = self._resolve_path(path)
        if resolved is None or not resolved.is_file():
            return {"found": False, "path": path, "reason": "文件不在允许的项目根目录内或不存在"}
        try:
            lines = resolved.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as error:
            return {"found": False, "path": path, "reason": str(error)}

        start = max(1, line - radius)
        end = min(len(lines), line + radius)
        snippet = "\n".join(
            f"{'>>' if number == line else '  '} {number:4d} | {lines[number - 1]}"
            for number in range(start, end + 1)
        )
        return {
            "found": True,
            "path": self._display_path(resolved, path),
            "line": line,
            "start_line": start,
            "end_line": end,
            "snippet": snippet,
        }

    def search_code(self, query: str) -> list[dict[str, Any]]:
        query = query.strip()[:240]
        if not query:
            return []
        command = ["rg", "--no-heading", "--line-number", "--color", "never", "--fixed-strings", query]
        for pattern in SOURCE_GLOBS:
            command.extend(["--glob", pattern])
        command.extend(["--glob", "!volumes/**", "--glob", "!uploads/**", "--glob", "!htmlcov/**", str(self.root)])
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=5, check=False)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return self._fallback_search(query)

        matches = []
        for raw_line in result.stdout.splitlines()[: config.bugfix_max_search_results]:
            parts = raw_line.split(":", 2)
            if len(parts) != 3 or not parts[1].isdigit():
                continue
            resolved = self._resolve_path(parts[0])
            if resolved is None:
                continue
            matches.append(
                {"path": self._display_path(resolved, parts[0]), "line": int(parts[1]), "content": parts[2].strip()}
            )
        return matches

    def git_history(self, paths: list[str]) -> list[dict[str, str]]:
        safe_paths = [
            self._display_path(self._resolve_path(path), path)
            for path in paths
            if self._resolve_path(path) is not None
        ]
        command = ["git", "-C", str(self.root), "log", "--all", "--oneline", "--max-count", "12"]
        if safe_paths:
            command.append("--")
            command.extend(safe_paths[:5])
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=5, check=False)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return []
        return [
            {"commit": line.split(" ", 1)[0], "summary": line.split(" ", 1)[1] if " " in line else ""}
            for line in result.stdout.splitlines()
            if line.strip()
        ]

    def release_history(self) -> list[dict[str, str]]:
        """读取最近发布标签与提交装饰，作为版本变更证据。"""
        command = [
            "git",
            "-C",
            str(self.root),
            "log",
            "--all",
            "--decorate=short",
            "--date=iso",
            "--pretty=format:%h%x09%ad%x09%d%x09%s",
            "--max-count",
            "20",
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=5, check=False)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return []
        records = []
        for line in result.stdout.splitlines():
            parts = line.split("\t", 3)
            if len(parts) == 4:
                records.append(
                    {
                        "commit": parts[0],
                        "committed_at": parts[1],
                        "refs": parts[2],
                        "summary": parts[3],
                    }
                )
        return records

    def find_related_tests(self, paths: list[str]) -> list[str]:
        tests_root = self.root / "tests"
        if not tests_root.is_dir():
            return []
        stems = {Path(path).stem.replace("_service", "") for path in paths}
        matches = []
        for test_file in tests_root.rglob("test_*.py"):
            if any(stem and stem in test_file.stem for stem in stems):
                matches.append(str(test_file.relative_to(self.root)))
        return matches[: config.bugfix_max_search_results]

    def run_related_tests(self, paths: list[str]) -> dict[str, Any]:
        """仅运行自动发现的测试文件；不接受模型提供任意命令或参数。"""
        tests = self.find_related_tests(paths)
        if not tests:
            return {"executed": False, "reason": "没有自动发现可安全执行的相关测试", "tests": []}
        command = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--disable-warnings",
            *tests[:5],
        ]
        try:
            result = subprocess.run(
                command,
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=config.bugfix_tool_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return {"executed": True, "timed_out": True, "tests": tests[:5]}
        except OSError as error:
            return {"executed": False, "reason": str(error), "tests": tests[:5]}
        return {
            "executed": True,
            "return_code": result.returncode,
            "tests": tests[:5],
            "stdout": result.stdout[-6000:],
            "stderr": result.stderr[-3000:],
        }

    def _resolve_path(self, raw_path: str) -> Path | None:
        candidate = Path(raw_path)
        try:
            resolved = candidate.resolve() if candidate.is_absolute() else (self.root / candidate).resolve()
            resolved.relative_to(self.root)
            return resolved
        except (OSError, ValueError):
            return None

    def _display_path(self, resolved: Path | None, fallback: str) -> str:
        if resolved is None:
            return fallback
        return str(resolved.relative_to(self.root))

    def _fallback_search(self, query: str) -> list[dict[str, Any]]:
        matches = []
        for pattern in SOURCE_GLOBS:
            for file_path in self.root.rglob(pattern):
                if any(part in {".git", ".venv", "volumes", "uploads", "htmlcov"} for part in file_path.parts):
                    continue
                try:
                    for line_number, line in enumerate(file_path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                        if query in line:
                            matches.append({"path": str(file_path.relative_to(self.root)), "line": line_number, "content": line.strip()})
                            if len(matches) >= config.bugfix_max_search_results:
                                return matches
                except OSError:
                    continue
        return matches


bugfix_repository = BugFixRepository()
