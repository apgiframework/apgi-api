"""Test task executor service."""

from unittest.mock import patch

import pytest

from app.services.task_execution.task_executor import TaskExecutor


class TestTaskExecutor:
    """Test task executor functionality."""

    def test_initialization(self) -> None:
        """Test that TaskExecutor initializes correctly."""
        task_executor = TaskExecutor()
        assert task_executor.strategy_registry is not None
        assert task_executor.dependency_manager is not None
        assert task_executor.task_submitter is not None
        assert hasattr(task_executor, "_task_map")
        assert "iowa_gambling" in task_executor._task_map
        assert "masking_paradigm" in task_executor._task_map

    def test_allowed_task_types(self) -> None:
        """Test that ALLOWED_TASK_TYPES is properly defined."""
        task_executor = TaskExecutor()
        allowed_types = task_executor.ALLOWED_TASK_TYPES
        assert "iowa_gambling" in allowed_types
        assert "masking_paradigm" in allowed_types
        assert len(allowed_types) == 5

    async def test_list_available_tasks(self) -> None:
        """Test listing available tasks."""
        task_executor = TaskExecutor()
        result = await task_executor.list_available_tasks()

        assert "tasks" in result
        assert len(result["tasks"]) == 5
        task_names = [task["name"] for task in result["tasks"]]
        assert "Iowa Gambling Task" in task_names
        assert "Masking Paradigm Task" in task_names

    async def test_submit_task_invalid_type(self) -> None:
        """Test task submission with invalid task type."""
        session_id = "session123"
        task_type = "invalid_task"
        parameters = {"param1": "value1"}
        user_id = "user123"
        task_executor = TaskExecutor()

        with pytest.raises(ValueError, match="Invalid task type"):
            await task_executor.submit_task(session_id, task_type, parameters, user_id)

    async def test_submit_task_invalid_priority_low(self) -> None:
        """Test task submission with priority too low."""
        session_id = "session123"
        task_type = "iowa_gambling"
        parameters = {"num_trials": 50}
        user_id = "user123"
        task_executor = TaskExecutor()

        with pytest.raises(ValueError, match="Priority must be between"):
            await task_executor.submit_task(session_id, task_type, parameters, user_id, priority=0)

    async def test_submit_task_invalid_priority_high(self) -> None:
        """Test task submission with priority too high."""
        session_id = "session123"
        task_type = "iowa_gambling"
        parameters = {"num_trials": 50}
        user_id = "user123"
        task_executor = TaskExecutor()

        with pytest.raises(ValueError, match="Priority must be between"):
            await task_executor.submit_task(session_id, task_type, parameters, user_id, priority=11)

    def test_get_dependency_chain(self) -> None:
        """Test getting dependency chain for a task."""
        task_id = "task123"
        task_executor = TaskExecutor()

        with patch.object(
            task_executor.dependency_manager,
            "get_dependency_chain",
            return_value=["task1", "task2"],
        ):
            result = task_executor.get_dependency_chain(task_id)

        assert result == ["task1", "task2"]

    def test_get_dependent_tasks(self) -> None:
        """Test getting tasks that depend on the given task."""
        task_id = "task123"
        task_executor = TaskExecutor()

        with patch.object(
            task_executor.dependency_manager, "get_dependent_tasks", return_value=["task3", "task4"]
        ):
            result = task_executor.get_dependent_tasks(task_id)

        assert result == ["task3", "task4"]

    async def test_retry_task(self) -> None:
        """Test retrying a failed task."""
        task_id = "task123"
        session_id = "session123"
        task_type = "iowa_gambling"
        parameters = {"num_trials": 50}
        task_executor = TaskExecutor()

        with patch.object(task_executor.task_submitter, "retry_task"):
            result = await task_executor.retry_task(task_id, session_id, task_type, parameters)

        assert result["task_id"] == task_id
        assert result["status"] == "retrying"

    async def test_submit_task_with_cycle_detection(self) -> None:
        """Test task submission with cycle detection (lines 137-140)."""
        from unittest.mock import AsyncMock, MagicMock

        session_id = "session123"
        task_type = "iowa_gambling"
        parameters = {"num_trials": 50}
        user_id = "user123"
        task_executor = TaskExecutor()

        mock_db = MagicMock()
        mock_session = MagicMock()
        mock_db.query.return_value.filter.return_value.filter.return_value.first.return_value = (
            mock_session
        )
        mock_db.query.return_value.filter.return_value.first.return_value = MagicMock()

        with patch("app.services.task_execution.task_executor.get_db_context") as mock_ctx:
            mock_ctx.return_value.__enter__.return_value = mock_db
            with patch.object(
                task_executor.task_submitter,
                "submit_task",
                new_callable=AsyncMock,
                return_value="task-123",
            ):
                with patch.object(task_executor.dependency_manager, "has_cycle", return_value=True):
                    with pytest.raises(ValueError, match="Task dependency cycle detected"):
                        await task_executor.submit_task(session_id, task_type, parameters, user_id)

    async def test_submit_task_without_strategy(self) -> None:
        """Test task submission when strategy is not available (line 149)."""
        from unittest.mock import AsyncMock, MagicMock

        session_id = "session123"
        task_type = "iowa_gambling"
        parameters = {"num_trials": 50}
        user_id = "user123"
        task_executor = TaskExecutor()

        mock_db = MagicMock()
        mock_session = MagicMock()
        mock_db.query.return_value.filter.return_value.filter.return_value.first.return_value = (
            mock_session
        )

        with patch("app.services.task_execution.task_executor.get_db_context") as mock_ctx:
            mock_ctx.return_value.__enter__.return_value = mock_db
            with patch.object(
                task_executor.task_submitter,
                "submit_task",
                new_callable=AsyncMock,
                return_value="task-456",
            ):
                with patch.object(
                    task_executor.dependency_manager, "has_cycle", return_value=False
                ):
                    with patch.object(
                        task_executor.dependency_manager, "can_start_task", return_value=True
                    ):
                        with patch.object(
                            task_executor.strategy_registry, "get", return_value=None
                        ):
                            with patch.object(
                                task_executor.task_submitter,
                                "start_task_immediately",
                                new_callable=AsyncMock,
                            ) as mock_start:
                                await task_executor.submit_task(
                                    session_id, task_type, parameters, user_id
                                )
                                # Verify parameters were passed without validation
                                call_args = mock_start.call_args
                                assert call_args[1]["parameters"] == parameters
