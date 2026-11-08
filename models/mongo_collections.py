from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class TaskStatus(str, Enum):
    TODO = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    DONE = "DONE"
    FAILED = "FAILED"


class SessionStatus(str, Enum):
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class Task(BaseModel):
    """One unit of work claimed from the tasks collection."""

    task_id: str
    status: TaskStatus
    priority: int
    repo: str
    branch: str
    description: str
    create_feature_branch: bool = True


class Session(BaseModel):
    """Metadata for one agent run against a task.

    Transcripts stay in the Claude session store. This document tracks the
    run's status, the task it belongs to, and the feature branch that receives
    the commit.

    ``updated_at`` is the session healthcheck. The worker writes it when the
    session is inserted, refreshes it on each later message from ``query()``
    while the session is still ``IN_PROGRESS``, and writes it again when the
    session becomes ``COMPLETED`` or ``FAILED``.
    """

    session_id: str
    status: SessionStatus
    task_id: str
    feature_branch: str
    commit_title: str | None = None
    summary: str | None = None
    updated_at: datetime | None = None
