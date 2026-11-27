"""Check the global context that Codex exec loads independently of project settings."""

import os
import stat
from pathlib import Path


class CodexClientProfileError(ValueError):
    pass


def check_client_profile():
    configured = os.environ.get("CODEX_HOME")
    try:
        root = Path(configured).expanduser() if configured else Path.home() / ".codex"
        if not root.is_absolute():
            raise CodexClientProfileError("Codex 登录目录需要使用绝对路径。请在已单独登录的研究环境中运行。")
        root_status = root.stat()
        if not stat.S_ISDIR(root_status.st_mode) or not os.access(root, os.R_OK | os.X_OK):
            raise CodexClientProfileError("无法核实此 Codex 登录目录，请从已登录的研究环境运行。")
        for filename in ("AGENTS.override.md", "AGENTS.md"):
            try:
                metadata = (root / filename).lstat()
            except FileNotFoundError:
                continue
            if (not stat.S_ISREG(metadata.st_mode) or metadata.st_size
                    or not os.access(root / filename, os.R_OK)):
                raise CodexClientProfileError("Codex 会自动附加此登录环境的全局指令。请用已单独登录、"
                    "全局指令为空的研究环境运行，或将此授权交给自己的 MCP 客户端。")
    except (OSError, RuntimeError) as error:
        raise CodexClientProfileError("无法核实此 Codex 登录环境的全局指令。请从专用研究环境运行。") from error
