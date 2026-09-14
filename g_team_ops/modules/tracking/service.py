"""物流表头初始化，复用工作簿锁与操作审计。"""
from __future__ import annotations

import hashlib
import json

from ...errors import ConfigurationError
from ..operations.shared_table import SharedTableOperationManager


def header_signature(preview):
    value = {key: preview.get(key) for key in ("plans", "snapshots")}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def initialize_headers(repository, profile_id, shop_id, country_id, client, signature, request_key):
    manager = SharedTableOperationManager(repository)
    batch = repository.create_batch(
        profile_id, "tracking", "tracking_headers_setup", shop_id=shop_id,
        country_id=country_id, resource_key=manager.resource_key(profile_id, shop_id),
        idempotency_key=request_key, reversible=False,
    )

    def before():
        preview = client.preview_headers()
        if header_signature(preview) != signature:
            raise ConfigurationError("表格已发生变化，请重新预览需要补齐的表头")
        return preview["snapshots"]

    try:
        return manager.execute(
            profile_id=profile_id, module_name="tracking", operation_type="tracking_headers_setup",
            shop_id=shop_id, country_id=country_id, idempotency_key=request_key,
            snapshot_before=before, apply=client.apply_headers,
            snapshot_after=client.snapshot_targets, serialize_result=lambda value: value,
            restore_result=lambda value: value, is_partial=lambda value: False,
            initial_summary={"action": "补齐物流表头；保留现有列，表头初始化不执行数据撤销"},
        )
    finally:
        repository.set_reversible(profile_id, batch.id, False)
