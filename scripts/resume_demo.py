"""从产品包组装继续演示数据引导。

设计流水线（风险评估、条款、定价）已经完成并冻结时使用。
会接着组装并发布产品，再跑投保核保、监测预警、事故响应和定损理赔。
"""

import json
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)

from techins.seed.demo import continue_demo  # noqa: E402

result = continue_demo()
print(json.dumps(result, ensure_ascii=False, indent=1))
