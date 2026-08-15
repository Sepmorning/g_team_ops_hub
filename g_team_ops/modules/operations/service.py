from __future__ import annotations

from ...db.backup import DatabaseBackupService
from .repository import OperationRepository


class OperationHistoryService:
    def __init__(self, repository: OperationRepository, backups: DatabaseBackupService):
        self.repository = repository
        self.backups = backups

    def details_payload(self, profile_id: str, batch_id: str) -> dict:
        details = self.repository.details(profile_id, batch_id)
        return {"batch": details.batch.to_payload(), "events": [event.__dict__ for event in details.events], "items": [item.__dict__ for item in details.items], "changes": [change.__dict__ for change in details.changes], "snapshot_count": len(details.snapshots)}
