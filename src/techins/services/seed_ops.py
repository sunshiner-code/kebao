"""运维数据初始化：组织与权限包、配置中心、数据源、智能体配置版本、定时任务，以及审批流的业务回调。"""

from __future__ import annotations


def seed_operations() -> None:
    from . import agent_config, approvals, customers, evals, iam, knowledge_admin, ops, policy_admin, product_admin, scheduler
    from .config_center import config_center

    iam.seed_iam()
    config_center.load_all()
    ops.seed_data_sources()
    agent_config.seed_configs()
    scheduler.seed_jobs()
    iam.register_handlers()
    policy_admin.register_handlers()
    product_admin.register_handlers()
    knowledge_admin.register_handlers()
    evals.register_handlers()
    _ = (approvals, customers)
