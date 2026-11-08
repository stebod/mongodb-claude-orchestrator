import asyncio
import logging
import tempfile

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ResultError,
    ResultMessage,
    SystemMessage,
    query,
)
from claude_agent_sdk_mongodb_session_store import MongoDBSessionStore

from config import config
from models.mongo_collections import Session, SessionStatus, Task
from providers.git_handler import GitHandler
from providers.mongo_handler import mongo_handler

logger = logging.getLogger(__name__)


def report_is_complete(session: Session | None) -> bool:
    if session is None or session.status != SessionStatus.COMPLETED:
        return False
    return bool(session.commit_title and session.commit_title.strip()) and bool(
        session.summary and session.summary.strip()
    )


async def process_task(
    task: Task,
    system_prompt: str,
    session_store: MongoDBSessionStore,
) -> None:
    session_id: str | None = None
    try:
        git_handler = GitHandler(
            task.repo,
            task.branch,
            task.task_id,
            create_feature_branch=task.create_feature_branch,
        )
        feature_branch = git_handler.feature_branch
        with tempfile.TemporaryDirectory() as temp_dir:
            logger.info(
                "Processing task %s in %s",
                task.task_id,
                temp_dir,
            )
            git_handler.prepare_repository(temp_dir)
            agent_failed = False
            try:
                agent_messages = query(
                    prompt=task.description,
                    options=ClaudeAgentOptions(
                        model=config.claude_model,
                        cwd=temp_dir,
                        system_prompt=system_prompt,
                        allowed_tools=["Read", "Edit", "Glob"],
                        permission_mode="acceptEdits",
                        session_store=session_store,
                        env={
                            "ANTHROPIC_API_KEY": config.claude_api_key,
                            "ANTHROPIC_BASE_URL": config.claude_api_url,
                        },
                    ),
                )
                async for message in agent_messages:
                    if (
                        isinstance(message, SystemMessage)
                        and message.subtype == "init"
                        and session_id is None
                    ):
                        raw_session_id = message.data.get("session_id")
                        if not isinstance(raw_session_id, str) or not raw_session_id:
                            continue
                        session_id = raw_session_id
                        await mongo_handler.insert_session(
                            Session(
                                session_id=session_id,
                                status=SessionStatus.IN_PROGRESS,
                                task_id=task.task_id,
                                feature_branch=feature_branch,
                            )
                        )
                        logger.info(
                            "Session %s started for task %s on %s",
                            session_id,
                            task.task_id,
                            feature_branch,
                        )
                    elif session_id is not None:
                        await mongo_handler.touch_session(session_id)
                    if (
                        isinstance(message, ResultMessage)
                        and message.subtype == "success"
                        and not message.is_error
                        and session_id is not None
                    ):
                        summary = (message.result or "").strip()
                        if not summary:
                            continue
                        updated = await mongo_handler.complete_session(
                            session_id=session_id,
                            commit_title=task.task_id,
                            summary=summary,
                        )
                        if updated:
                            logger.info(
                                "Session %s completed for task %s",
                                session_id,
                                task.task_id,
                            )
                        else:
                            logger.error(
                                "Session %s for task %s could not be marked COMPLETED",
                                session_id,
                                task.task_id,
                            )
            except ResultError as error:
                agent_failed = True
                error_details = " | ".join(error.errors) if error.errors else error.result
                logger.error("Process terminated. Reason: %s", error.terminal_reason)
                logger.error("Error output: %s", error_details)

            if agent_failed or session_id is None:
                logger.error(
                    "Task %s failed before a successful result was recorded",
                    task.task_id,
                )
                await mongo_handler.mark_failed(task.task_id, session_id)
                return

            session = await mongo_handler.get_session(session_id)
            if not report_is_complete(session):
                logger.error("Task %s ended without a successful result", task.task_id)
                await mongo_handler.mark_failed(task.task_id, session_id)
                return

            assert session is not None
            assert session.commit_title is not None
            assert session.summary is not None
            git_handler.commit_and_push(
                session.commit_title,
                task.description,
                session.summary,
            )
            await mongo_handler.mark_task_done(task.task_id)
            logger.info("Task %s done on %s", task.task_id, feature_branch)
    except Exception:
        logger.exception("Task %s failed", task.task_id)
        try:
            await mongo_handler.mark_failed(task.task_id, session_id)
        except Exception:
            logger.exception("Could not mark task %s as FAILED", task.task_id)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    session_store = MongoDBSessionStore(
        client=mongo_handler.mongo_client,
        db_name=config.claude_session_db_name,
    )
    await session_store.create_schema()
    await mongo_handler.ensure_indexes()

    announce_polling = True
    while True:
        if announce_polling:
            logger.info("Polling for tasks")
            announce_polling = False
        try:
            task = await mongo_handler.claim_next_task()
        except Exception:
            logger.exception("Failed to poll tasks")
            await asyncio.sleep(config.poll_interval_seconds)
            continue
        if task is None:
            await asyncio.sleep(config.poll_interval_seconds)
            continue
        logger.info(
            "Claimed task %s on %s@%s (priority %s)",
            task.task_id,
            task.repo,
            task.branch,
            task.priority,
        )
        await process_task(task, config.system_prompt, session_store)
        announce_polling = True


if __name__ == "__main__":
    asyncio.run(main())
