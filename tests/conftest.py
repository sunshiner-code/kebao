"""测试继续使用各自的临时 SQLite，不连接 .env 里配置的 MySQL。"""

from __future__ import annotations

import os

os.environ.setdefault("TECHINS_DATABASE_URL", "")
