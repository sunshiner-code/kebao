"""科技保险智能体业务平台（techins）。

提供 `techins` 命令行入口，默认启动 FastAPI 后端服务。
"""

from __future__ import annotations

import os


def main() -> None:
    """启动后端 API 服务。"""
    import uvicorn

    host = os.environ.get("TECHINS_HOST", "127.0.0.1")
    port = int(os.environ.get("TECHINS_PORT", "8742"))
    uvicorn.run("techins.api.app:app", host=host, port=port)
