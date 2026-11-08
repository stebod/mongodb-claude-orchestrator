import logging
from datetime import datetime, timezone

from pydantic import ValidationError
from pymongo import AsyncMongoClient, ReturnDocument

from config import config
from models.mongo_collections import Session, SessionStatus, Task, TaskStatus

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MongoHandler:
    def __init__(self) -> None:
        self.mongo_client = AsyncMongoClient(config.mongo_conn_str)
        self._db = self.mongo_client[config.mongo_app_db_name]
        self.tasks = self._db["tasks"]
        self.sessions = self._db["sessions"]

    async def ensure_indexes(self) -> None:
        """Create the indexes the worker and an external closer can use. Idempotent."""
        task_indexes = await self.tasks.index_information()
        if "status_updated_at_idx" in task_indexes:
            await self.tasks.drop_index("status_updated_at_idx")
        await self.tasks.create_index(
            [("status", 1), ("priority", -1)],
            name="status_priority_idx",
        )
        await self.tasks.create_index(
            [("task_id", 1)],
            name="task_id_idx",
            unique=True,
        )
        await self.sessions.create_index(
            [("session_id", 1)],
            name="session_id_idx",
            unique=True,
        )
        await self.sessions.create_index(
            [("status", 1), ("updated_at", 1)],
            name="status_updated_at_idx",
        )

    async def claim_next_task(self) -> Task | None:
        """Claim the highest-priority TODO task by setting it IN_PROGRESS.

        The update is atomic, so two workers cannot claim the same document.
        A claimed document that does not match the task model is marked FAILED,
        so one bad document does not block the queue. A document with no
        task_id is closed by ``_id``.
        """
        while True:
            document = await self.tasks.find_one_and_update(
                {"status": TaskStatus.TODO.value},
                {"$set": {"status": TaskStatus.IN_PROGRESS.value}},
                sort=[("priority", -1)],
                return_document=ReturnDocument.AFTER,
            )
            if document is None:
                return None
            try:
                return Task.model_validate(document)
            except ValidationError as error:
                task_id = document.get("task_id")
                if not isinstance(task_id, str) or not task_id:
                    await self.tasks.update_one(
                        {
                            "_id": document["_id"],
                            "status": TaskStatus.IN_PROGRESS.value,
                        },
                        {"$set": {"status": TaskStatus.FAILED.value}},
                    )
                    logger.error(
                        "Claimed task is invalid, has no task_id, and was marked FAILED: %s",
                        error,
                    )
                    continue
                logger.error("Task %s is invalid and was marked FAILED: %s", task_id, error)
                await self.mark_failed(task_id, None)

    async def insert_session(self, session: Session) -> None:
        document = session.model_dump(mode="json", exclude={"updated_at"})
        document["updated_at"] = _utcnow()
        await self.sessions.insert_one(document)

    async def complete_session(
        self,
        session_id: str,
        commit_title: str,
        summary: str,
    ) -> bool:
        """Mark a session COMPLETED with the commit title and summary.

        The orchestrator calls this when the agent run ends with a successful result.
        """
        result = await self.sessions.update_one(
            {"session_id": session_id},
            {
                "$set": {
                    "status": SessionStatus.COMPLETED.value,
                    "commit_title": commit_title,
                    "summary": summary,
                    "updated_at": _utcnow(),
                }
            },
        )
        return result.matched_count == 1

    async def get_session(self, session_id: str) -> Session | None:
        document = await self.sessions.find_one({"session_id": session_id})
        if document is None:
            return None
        return Session.model_validate(document)

    async def touch_session(self, session_id: str) -> None:
        """Refresh updated_at while the session is still IN_PROGRESS."""
        await self.sessions.update_one(
            {"session_id": session_id, "status": SessionStatus.IN_PROGRESS.value},
            {"$set": {"updated_at": _utcnow()}},
        )

    async def mark_task_done(self, task_id: str) -> None:
        await self.tasks.update_one(
            {"task_id": task_id},
            {"$set": {"status": TaskStatus.DONE.value}},
        )

    async def mark_failed(self, task_id: str, session_id: str | None) -> None:
        await self.tasks.update_one(
            {"task_id": task_id},
            {"$set": {"status": TaskStatus.FAILED.value}},
        )
        if session_id is None:
            return
        await self.sessions.update_one(
            {"session_id": session_id},
            {
                "$set": {
                    "status": SessionStatus.FAILED.value,
                    "updated_at": _utcnow(),
                }
            },
        )


mongo_handler = MongoHandler()
