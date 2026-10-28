"""Fail-closed configuration checks for preproduction worker roles."""

from app.core.config import settings


def migrated_schedule_kinds() -> set[str]:
    return {
        item.strip()
        for item in settings.SCHEDULER_MIGRATED_KINDS.split(",")
        if item.strip()
    }


def assert_agent_worker_enabled() -> None:
    if settings.AGENT_EXECUTION_MODE != "worker":
        raise RuntimeError(
            "Agent Worker profile 与 API 配置冲突：必须设置 AGENT_EXECUTION_MODE=worker"
        )


def assert_scheduler_preproduction_safe() -> None:
    migrated = migrated_schedule_kinds()
    if not settings.SCHEDULER_ROLE_ENABLED:
        raise RuntimeError("预生产阶段 API 未迁移 cron 必须保持开启")
    if settings.SCHEDULER_PAID_EXECUTION_ENABLED:
        raise RuntimeError("预生产阶段必须关闭调度付费执行")
    if "financial_check" not in migrated:
        raise RuntimeError("预生产阶段必须迁移已验证的 financial_check")
    if "full_refresh" in migrated:
        raise RuntimeError("预生产阶段不允许迁移 full_refresh")
    unsupported = migrated - {"financial_check"}
    if unsupported:
        raise RuntimeError(
            "预生产调度只允许已验证的 financial_check；未验证任务类型："
            + ",".join(sorted(unsupported))
        )


def assert_test_delivery_sink() -> None:
    if settings.OUTBOX_DELIVERY_SINK != "test":
        raise RuntimeError("预生产独立 Delivery 必须使用测试 sink")
