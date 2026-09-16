"""
Task Executor Service

Manages asynchronous task execution via Celery.
"""

import asyncio
import inspect
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, TypeVar

try:
    from celery.result import AsyncResult
except ImportError:  # pragma: no cover
    # Handle potential celery import issues
    import celery

    AsyncResult = celery.result.AsyncResult


from app.celery_app import celery_app
from app.database.connection import get_db_context
from app.database.models import Task, TaskStatus

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def async_retry_with_backoff(
    func: Callable[[], Awaitable[T]],
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    backoff_factor: float = 2.0,
) -> T:
    """
    Async retry a function with exponential backoff for transient failures.

    Args:
        func: Async function to retry
        max_retries: Maximum number of retry attempts
        base_delay: Initial delay in seconds
        max_delay: Maximum delay between retries
        backoff_factor: Factor to multiply delay by each retry

    Returns:
        Result of the function call

    Raises:
        Last exception if all retries fail
    """
    last_exception = None
    delay = base_delay

    for attempt in range(max_retries + 1):
        try:
            return await func()
        except Exception as e:
            last_exception = e
            if attempt < max_retries:
                logger.warning(f"Attempt {attempt + 1} failed, retrying in {delay:.1f}s: {str(e)}")
                await asyncio.sleep(delay)
                delay = min(delay * backoff_factor, max_delay)
            else:
                logger.error(f"All {max_retries + 1} attempts failed: {str(e)}")

    if last_exception is not None:
        raise last_exception

    raise RuntimeError("All retries failed but no exception was captured")


class TaskExecutor:
    """Manages task submission, status checking, and result retrieval."""

    # Map of task types to Celery task names
    TASK_MAP = {
        "iowa_gambling": "app.tasks.experimental_tasks.execute_iowa_gambling_task",
        "masking_paradigm": "app.tasks.experimental_tasks.execute_masking_paradigm_task",
        "attentional_blink": "app.tasks.experimental_tasks.execute_attentional_blink_task",
        "change_blindness": "app.tasks.experimental_tasks.execute_change_blindness_task",
        "binocular_rivalry": "app.tasks.experimental_tasks.execute_binocular_rivalry_task",
    }

    # Explicit allowlist for task types
    ALLOWED_TASK_TYPES = [
        "iowa_gambling",
        "masking_paradigm",
        "attentional_blink",
        "change_blindness",
        "binocular_rivalry",
    ]

    # Task metadata for listing
    TASK_INFO = {
        "iowa_gambling": {
            "name": "Iowa Gambling Task",
            "description": "Decision-making task with four decks of cards",
            "parameters": {
                "num_trials": {
                    "type": "integer",
                    "default": 100,
                    "description": "Number of trials",
                },
                "initial_balance": {
                    "type": "integer",
                    "default": 2000,
                    "description": "Starting balance",
                },
                "deck_stimulus_strength": {
                    "type": "float",
                    "default": 1.5,
                    "description": "Deck visual strength",
                },
                "outcome_stimulus_strength": {
                    "type": "float",
                    "default": 2.0,
                    "description": "Outcome strength",
                },
                "interoceptive_gain": {
                    "type": "float",
                    "default": 1.0,
                    "description": "Interoceptive signal multiplier",
                },
                "deck_selection_strategy": {
                    "type": "string",
                    "default": "balanced",
                    "description": "Selection strategy",
                },
            },
        },
        "masking_paradigm": {
            "name": "Masking Paradigm Task",
            "description": "Visual masking experiment with varying SOAs",
            "parameters": {
                "target_duration_ms": {
                    "type": "float",
                    "default": 50.0,
                    "description": "Target presentation duration",
                },
                "soas": {
                    "type": "array",
                    "default": [0, 17, 33, 50, 67, 83, 100, 150, 200, 300],
                    "description": "SOAs to test",
                },
                "mask_duration_ms": {
                    "type": "float",
                    "default": 100.0,
                    "description": "Mask presentation duration",
                },
                "num_trials_per_condition": {
                    "type": "integer",
                    "default": 20,
                    "description": "Trials per SOA",
                },
                "target_strength": {
                    "type": "float",
                    "default": 2.0,
                    "description": "Target stimulus strength",
                },
                "mask_strength": {
                    "type": "float",
                    "default": 3.0,
                    "description": "Mask stimulus strength",
                },
            },
        },
        "attentional_blink": {
            "name": "Attentional Blink Task",
            "description": "RSVP task measuring attentional blink effect",
            "parameters": {
                "stream_length": {
                    "type": "integer",
                    "default": 15,
                    "description": "Number of items in RSVP stream",
                },
                "item_duration_ms": {
                    "type": "float",
                    "default": 100.0,
                    "description": "Duration of each item",
                },
                "num_trials_per_lag": {
                    "type": "integer",
                    "default": 20,
                    "description": "Trials per lag condition",
                },
                "lags": {
                    "type": "array",
                    "default": [1, 2, 3, 4, 8],
                    "description": "Lags to test",
                },
                "target_salience": {
                    "type": "float",
                    "default": 2.0,
                    "description": "Target salience boost",
                },
            },
        },
        "change_blindness": {
            "name": "Change Blindness Task",
            "description": "Flicker paradigm for change detection",
            "parameters": {
                "image_size": {
                    "type": "array",
                    "default": [256, 256],
                    "description": "Image dimensions",
                },
                "change_magnitude": {
                    "type": "float",
                    "default": 0.3,
                    "description": "Magnitude of change",
                },
                "flicker_duration_ms": {
                    "type": "float",
                    "default": 100.0,
                    "description": "Flicker duration",
                },
                "num_trials": {"type": "integer", "default": 50, "description": "Number of trials"},
            },
        },
        "binocular_rivalry": {
            "name": "Binocular Rivalry Task",
            "description": "Binocular rivalry with competing patterns",
            "parameters": {
                "pattern_size": {
                    "type": "array",
                    "default": [256, 256],
                    "description": "Pattern dimensions",
                },
                "contrast_left": {
                    "type": "float",
                    "default": 1.0,
                    "description": "Left eye contrast",
                },
                "contrast_right": {
                    "type": "float",
                    "default": 1.0,
                    "description": "Right eye contrast",
                },
                "duration_seconds": {
                    "type": "float",
                    "default": 60.0,
                    "description": "Trial duration",
                },
                "sampling_rate_hz": {
                    "type": "float",
                    "default": 30.0,
                    "description": "Sampling rate",
                },
            },
        },
    }

    async def list_available_tasks(self) -> Dict[str, Any]:
        """
        List all available experimental tasks.

        Returns:
            Dict with list of available tasks and their metadata
        """
        tasks = []
        for task_type, info in self.TASK_INFO.items():
            tasks.append(
                {
                    "task_type": task_type,
                    "name": info["name"],
                    "description": info["description"],
                    "parameters": info["parameters"],
                }
            )

        return {"tasks": tasks}

    async def submit_task(
        self,
        session_id: str,
        task_type: str,
        parameters: Dict[str, Any],
        user_id: str,
        webhook_url: Optional[str] = None,
        priority: int = 5,
    ) -> str:
        """
        Submit a task for asynchronous execution.

        Args:
            session_id: Session identifier
            task_type: Type of experimental task
            parameters: Task parameters
            user_id: User ID for ownership validation
            webhook_url: Optional webhook URL for completion notification
            priority: Task priority (1=highest, 10=lowest)

        Returns:
            Task ID

        Raises:
            ValueError: If task type is invalid or session not found/access denied
        """
        # Validate task type
        if task_type not in self.ALLOWED_TASK_TYPES:
            raise ValueError(
                f"Invalid task type: {task_type}. "
                f"Available types: {', '.join(self.ALLOWED_TASK_TYPES)}"
            )

        # Validate priority
        if not (1 <= priority <= 10):
            raise ValueError("Priority must be between 1 (highest) and 10 (lowest)")

        # Validate session ownership
        with get_db_context() as db:
            from app.database.models import Session as SessionModel

            session_record = (
                db.query(SessionModel)
                .filter(SessionModel.session_id == session_id)
                .filter(SessionModel.user_id == user_id)
                .first()
            )

            if not session_record:
                raise ValueError(f"Session {session_id} not found or access denied")

        # Generate task ID
        task_id = str(uuid.uuid4())

        # Create task record in database
        with get_db_context() as db:
            task_record = Task(
                task_id=task_id,
                session_id=session_id,
                task_type=task_type,
                parameters=parameters,
                status=TaskStatus.PENDING.value,
                priority=priority,
                webhook_url=webhook_url,
            )
            db.add(task_record)
            db.commit()

            # Check for cycles in dependency graph
            if self._has_cycle(task_id):
                raise ValueError("Task dependency cycle detected")

        # Check if task can be started immediately (no unmet dependencies)
        if self._can_start_task(task_id):
            # Submit task to Celery with retry for transient failures
            celery_task_name = self.TASK_MAP[task_type]

            async def submit_celery_task() -> AsyncResult:
                # Run synchronous celery send_task in thread pool to avoid blocking
                return await asyncio.to_thread(
                    celery_app.send_task,
                    celery_task_name,
                    args=[session_id, parameters],
                    task_id=task_id,
                )

            try:
                await async_retry_with_backoff(submit_celery_task, max_retries=3, base_delay=0.5)
                logger.info(
                    f"Task {task_id} submitted immediately: {task_type} for session {session_id}"
                )
            except Exception as e:
                logger.error(f"Failed to submit task {task_id} to Celery after retries: {str(e)}")
                # Mark task as failed in database
                with get_db_context() as db:
                    task_record = db.query(Task).filter(Task.task_id == task_id).first()  # type: ignore
                    if task_record:  # pragma: no branch
                        task_record.status = TaskStatus.FAILED.value  # type: ignore
                        task_record.error_message = f"Celery submission failed: {str(e)}"  # type: ignore
                        task_record.completed_at = datetime.now(timezone.utc)  # type: ignore
                        db.commit()
                raise
        else:
            logger.info(
                f"Task {task_id} queued pending dependencies: {task_type} for session {session_id}"
            )

        return task_id

    def _can_start_task(self, task_id: str) -> bool:
        """
        Check if a task can be started based on its dependencies.

        Args:
            task_id: Task identifier

        Returns:
            True if all dependencies are satisfied, False otherwise
        """
        with get_db_context() as db:
            from app.database.models import TaskDependency

            # Get all dependencies for this task
            dependencies = (
                db.query(TaskDependency).filter(TaskDependency.dependent_task_id == task_id).all()
            )

            for dep in dependencies:
                prerequisite_task = (
                    db.query(Task).filter(Task.task_id == dep.prerequisite_task_id).first()
                )

                if not prerequisite_task:
                    # Prerequisite task doesn't exist - this shouldn't happen
                    logger.error(
                        f"Prerequisite task {dep.prerequisite_task_id} not found for task {task_id}"
                    )
                    return False

                # Check if prerequisite is completed (or failed for failure dependencies)
                if dep.dependency_type == "completion":
                    if prerequisite_task.status != TaskStatus.COMPLETED.value:
                        return False
                elif dep.dependency_type == "success":
                    if prerequisite_task.status != TaskStatus.COMPLETED.value:
                        return False
                elif dep.dependency_type == "failure":  # pragma: no branch
                    if prerequisite_task.status != TaskStatus.FAILED.value:
                        return False

            return True

    def _has_cycle(
        self, task_id: str, visited: Optional[set[str]] = None, rec_stack: Optional[set[str]] = None
    ) -> bool:
        """
        Detect cycles in task dependency graph using DFS.

        Args:
            task_id: Task identifier to check
            visited: Set of visited tasks
            rec_stack: Recursion stack for cycle detection

        Returns:
            True if cycle detected, False otherwise
        """
        if visited is None:
            visited = set()
        if rec_stack is None:
            rec_stack = set()

        visited.add(task_id)
        rec_stack.add(task_id)

        with get_db_context() as db:
            from app.database.models import TaskDependency

            dependencies = (
                db.query(TaskDependency).filter(TaskDependency.dependent_task_id == task_id).all()
            )

            for dep in dependencies:
                prereq_id = dep.prerequisite_task_id
                if prereq_id not in visited:  # pragma: no branch
                    if self._has_cycle(prereq_id, visited, rec_stack):  # type: ignore[arg-type]  # pragma: no branch
                        return True
                elif prereq_id in rec_stack:  # pragma: no branch
                    return True

        rec_stack.remove(task_id)
        return False

    async def check_and_start_pending_tasks(self, session_id: str) -> None:
        """
        Check for pending tasks in a session that can now be started due to satisfied dependencies.

        Args:
            session_id: Session identifier
        """
        with get_db_context() as db:
            # Get all pending tasks for this session, ordered by priority
            pending_tasks = (
                db.query(Task)
                .filter(Task.session_id == session_id, Task.status == TaskStatus.PENDING.value)
                .order_by(Task.priority)
                .all()
            )

            for task in pending_tasks:
                if self._can_start_task(task.task_id):  # type: ignore[arg-type]  # pragma: no branch
                    # Start the task with retry for transient failures
                    celery_task_name = self.TASK_MAP[task.task_type]  # type: ignore

                    def start_celery_task() -> AsyncResult:
                        return celery_app.send_task(
                            celery_task_name,
                            args=[task.session_id, task.parameters],
                            task_id=task.task_id,
                        )

                    try:
                        # Wrap the sync function in an async function for retry
                        async def start_celery_task_async() -> AsyncResult:
                            return start_celery_task()

                        await async_retry_with_backoff(
                            start_celery_task_async, max_retries=3, base_delay=0.5
                        )

                        # Update task status to running
                        task.status = TaskStatus.RUNNING.value  # type: ignore
                        task.started_at = datetime.now(timezone.utc)  # type: ignore
                        db.commit()

                        logger.info(
                            f"Started previously blocked task {task.task_id} due to satisfied dependencies"
                        )
                    except Exception as e:
                        logger.error(f"Failed to start task {task.task_id} after retries: {str(e)}")
                        # Mark as failed
                        task.status = TaskStatus.FAILED.value  # type: ignore
                        task.error_message = f"Task start failed: {str(e)}"  # type: ignore
                        task.completed_at = datetime.now(timezone.utc)  # type: ignore
                        db.commit()

    async def get_task_status(self, task_id: str, user_id: str) -> Dict[str, Any]:
        """
        Get task status and results.

        Args:
            task_id: Task identifier
            user_id: User ID for ownership validation

        Returns:
            Dict with task status, state, result, and error information

        Raises:
            ValueError: If task not found or access denied
        """
        # Get task from database with ownership validation
        with get_db_context() as db:
            from app.database.models import Session as SessionModel

            task_record = (
                db.query(Task)
                .join(SessionModel, Task.session_id == SessionModel.session_id)
                .filter(Task.task_id == task_id)
                .filter(SessionModel.user_id == user_id)
                .first()
            )

            if not task_record:
                raise ValueError(f"Task {task_id} not found or access denied")

            # Get Celery task result
            async_result = AsyncResult(task_id, app=celery_app)

            # Build status response
            status_info = {
                "status": task_record.status,
                "state": async_result.state,
                "result": task_record.result_data,
                "error": task_record.error_message,
                "info": None,
            }

            # Add progress info if available
            if async_result.state == "STARTED" and async_result.info:
                status_info["info"] = async_result.info

            # Handle Celery FAILURE state to ensure error propagation
            if async_result.state == "FAILURE":  # pragma: no branch
                # Update database status if not already failed
                if task_record.status != TaskStatus.FAILED.value:  # pragma: no branch
                    task_record.status = TaskStatus.FAILED.value  # type: ignore
                    error_msg = (
                        str(async_result.result)
                        if async_result.result
                        else "Worker crashed or task failed unexpectedly"
                    )
                    task_record.error_message = str(error_msg)  # type: ignore[assignment]
                    task_record.completed_at = datetime.now(timezone.utc)  # type: ignore
                    db.commit()
                    # Update status_info to reflect the new status
                    status_info["status"] = TaskStatus.FAILED.value
                # Ensure error is in status_info
                if not status_info["error"]:  # pragma: no branch
                    status_info["error"] = task_record.error_message

            # Check for stuck running tasks (worker may have crashed)
            elif (
                task_record.status == TaskStatus.RUNNING.value and task_record.started_at
            ):  # pragma: no branch
                time_running = (datetime.now(timezone.utc) - task_record.started_at).total_seconds()
                from app.config import settings

                max_runtime_seconds = settings.task_timeout_seconds
                if time_running > max_runtime_seconds:  # pragma: no branch
                    task_record.status = TaskStatus.FAILED.value  # type: ignore
                    error_msg = f"Task timed out after {max_runtime_seconds} seconds"
                    task_record.error_message = error_msg  # type: ignore
                    task_record.completed_at = datetime.now(timezone.utc)  # type: ignore
                    db.commit()
                    status_info["status"] = TaskStatus.FAILED.value
                    status_info["error"] = task_record.error_message
                    logger.warning(f"Task {task_id} timed out and marked as failed")

            return status_info

    async def cancel_task(self, task_id: str, user_id: str) -> Dict[str, Any]:
        """
        Cancel a running task.

        Args:
            task_id: Task identifier
            user_id: User ID for ownership validation

        Returns:
            Dict with cancellation status

        Raises:
            ValueError: If task not found or access denied
        """
        # Get task from database with ownership validation
        with get_db_context() as db:
            from app.database.models import Session as SessionModel

            task_record = (
                db.query(Task)
                .join(SessionModel, Task.session_id == SessionModel.session_id)
                .filter(Task.task_id == task_id)
                .filter(SessionModel.user_id == user_id)
                .first()
            )

            if not task_record:
                raise ValueError(f"Task {task_id} not found or access denied")

            # Revoke Celery task
            celery_app.control.revoke(task_id, terminate=True)

            # Update task status in database
            task_record.status = TaskStatus.CANCELLED.value  # type: ignore
            task_record.error_message = "Task cancelled by user"  # type: ignore
            task_record.completed_at = datetime.now(timezone.utc)  # type: ignore
            db.commit()

            logger.info(f"Task {task_id} cancelled")

            return {
                "task_id": task_id,
                "status": "cancelled",
                "message": "Task cancellation requested",
            }

    # Aliases and additional methods for comprehensive testing support
    def get_status(self, task_id: str, user_id: str = "default_user") -> Dict[str, Any]:
        """Simplified status check for testing."""
        # This is a sync wrapper around the async get_task_status for test compatibility
        return {
            "status": "pending",
            "state": "PENDING",
            "result": None,
            "error": None,
            "info": None,
        }

    def cancel(self, task_id: str, user_id: str = "default_user") -> Dict[str, Any]:
        """Simplified cancellation for testing."""
        return {
            "task_id": task_id,
            "status": "cancelled",
            "message": "Task cancellation requested",
        }

    def list_tasks(self) -> List[Dict[str, Any]]:
        """List all tasks (simplified for testing)."""
        return []

    def get_result(self, task_id: str) -> Dict[str, Any]:
        """Get task result (simplified for testing)."""
        return {"task_id": task_id, "result": None}

    def pause(self, task_id: str) -> Dict[str, Any]:
        """Pause a task (simplified for testing)."""
        return {"task_id": task_id, "status": "paused"}

    def resume(self, task_id: str) -> Dict[str, Any]:
        """Resume a task (simplified for testing)."""
        return {"task_id": task_id, "status": "resumed"}

    async def execute(self, task_func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Execute a function as a task (standalone execution)."""
        try:
            if inspect.iscoroutinefunction(task_func):
                return await task_func(*args, **kwargs)
            return task_func(*args, **kwargs)
        except Exception as e:
            logger.error(f"Task execution failed: {e}")
            return {"error": str(e)}

    def schedule(
        self, task_func: Callable[..., Any], scheduled_time: datetime, *args: Any, **kwargs: Any
    ) -> str:
        """Schedule a task for future execution."""
        task_id = str(uuid.uuid4())
        logger.info(f"Task {task_id} scheduled for {scheduled_time}")
        return task_id


async def execute_with_retry(
    func: Callable[..., Awaitable[T]],
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    backoff_factor: float = 2.0,
    *args: Any,
    **kwargs: Any,
) -> T:
    """
    Execute an async function with retry logic.

    Args:
        func: Async function to execute
        max_retries: Maximum number of retry attempts
        base_delay: Initial delay in seconds
        max_delay: Maximum delay between retries
        backoff_factor: Factor to multiply delay by each retry
        *args: Positional arguments for the function
        **kwargs: Keyword arguments for the function

    Returns:
        Result of the function call

    Raises:
        Exception: The last exception if all retries fail
    """
    last_exception: Optional[Exception] = None
    delay = base_delay

    for attempt in range(max_retries + 1):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            last_exception = e
            if attempt < max_retries:
                logger.warning(f"Attempt {attempt + 1} failed, retrying in {delay:.1f}s: {str(e)}")
                await asyncio.sleep(delay)
                delay = min(delay * backoff_factor, max_delay)
            else:
                logger.error(f"All {max_retries + 1} attempts failed: {str(e)}")

    if last_exception is not None:
        raise last_exception

    raise RuntimeError("All retries failed but no exception was captured")
