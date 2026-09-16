"""
Session Management Routes

API endpoints for creating, controlling, and managing APGI simulation sessions.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, cast

import redis.asyncio as redis
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.database.connection import SessionLocal, get_db
from app.database.models import Session as SessionModel
from app.database.models import Task
from app.exceptions import ServiceUnavailableError, SessionNotFoundError, SessionStateConflictError
from app.models.schemas import (
    ErrorResponse,
    PaginationInfo,
    SessionActionResponse,
    SessionCreateRequest,
    SessionCreateResponse,
    SessionListResponse,
    SessionMetricsResponse,
    SessionResponse,
    SessionTaskListResponse,
    TaskStatusResponse,
)
from app.services.authorization import (
    Permission,
    Role,
    TokenPayload,
    get_current_user,
    has_any_role,
    require_permission,
)
from app.services.session_manager import SessionLifecycleState, SessionManager

logger = logging.getLogger(__name__)


class SessionRoutesState:
    """Encapsulate session routes state to avoid global mutable state."""

    def __init__(self) -> None:
        self.redis_client: Optional[redis.Redis] = None
        self.session_manager: Optional[SessionManager] = None


# Global state instance
_state = SessionRoutesState()


async def validate_session_ownership(
    session_id: str,
    user_id: str,
    manager: SessionManager,
    db_session: Session,
    is_admin: bool = False,
) -> SessionModel:
    """
    Validate that the current user owns the specified session.

    Args:
        session_id: Session identifier to validate
        user_id: Current user's ID
        manager: Session manager dependency
        db_session: Database session
        is_admin: If True, ownership check is bypassed (admin access).

    Returns:
        Session model if ownership is valid

    Raises:
        HTTPException: If session not found or user doesn't own it
    """

    # Blocking function for DB query
    def _blocking_validate() -> SessionModel | None:
        session = (
            db_session.query(SessionModel)
            .filter(SessionModel.session_id == session_id)
            .filter(SessionModel.is_deleted.is_(False))
            .first()
        )
        return session

    # Execute DB query in executor to avoid blocking event loop
    loop = asyncio.get_running_loop()
    session = await loop.run_in_executor(None, _blocking_validate)

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found"
        )

    # Admins bypass ownership checks (MF-012 / R-23)
    if is_admin:
        # Session should already be validated above, but ensure it's not None
        if session is None:  # pragma: no cover
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found"
            )
        return session

    # Check ownership
    if session.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: you do not own this session",
        )

    return session


# Create router
router = APIRouter(
    prefix="/v1/sessions",
    tags=["Sessions"],
    responses={
        404: {"model": ErrorResponse, "description": "Session not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)


# Redis client (will be initialized in main app)
# _redis_client: Optional[redis.Redis] = None
# _session_manager: Optional[SessionManager] = None


def get_redis_client() -> redis.Redis:
    """Get Redis client dependency."""
    if _state.redis_client is None:
        raise ServiceUnavailableError("Redis", "Redis client not initialized")
    return _state.redis_client


def get_session_manager() -> SessionManager:
    """Get SessionManager dependency."""
    if _state.session_manager is None:
        raise ServiceUnavailableError("SessionManager", "Session manager not initialized")
    return _state.session_manager


async def check_idempotency_key(
    request: Request,
    user_id: str,
    redis_client: redis.Redis,
    idempotency_key: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Check for idempotency key and return cached response if exists.

    Args:
        request: HTTP request
        user_id: User ID
        redis_client: Redis client
        idempotency_key: Idempotency key from header (optional)

    Returns:
        Cached response dict if found, None otherwise
    """
    if not idempotency_key:  # pragma: no branch
        # Check header
        idempotency_key = request.headers.get("Idempotency-Key")
        if not idempotency_key:
            return None

    # Validate key format (should be reasonably short)
    if len(idempotency_key) > 255:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Idempotency-Key header too long (max 255 characters)",
        )

    # Check cache
    cache_key = f"idempotency:{user_id}:{idempotency_key}"
    cached_response = await redis_client.get(cache_key)

    if cached_response:
        import json

        return cast(Dict[str, Any], json.loads(cached_response))

    return None


async def cache_idempotency_response(
    user_id: str,
    idempotency_key: str,
    response_data: Dict[str, Any],
    redis_client: redis.Redis,
    ttl_seconds: int = 86400,  # 24 hours
) -> None:
    """
    Cache response for idempotency key.

    Args:
        user_id: User ID
        idempotency_key: Idempotency key
        response_data: Response data to cache
        redis_client: Redis client
        ttl_seconds: TTL for cache
    """
    import json

    cache_key = f"idempotency:{user_id}:{idempotency_key}"
    await redis_client.setex(cache_key, ttl_seconds, json.dumps(response_data))


def init_session_routes(redis_client: redis.Redis) -> None:
    """
    Initialize session routes with Redis client.

    Args:
        redis_client: Async Redis client instance
    """
    _state.redis_client = redis_client
    _state.session_manager = SessionManager(redis_client, SessionLocal)
    logger.info("Session routes initialized")


@router.get(
    "",
    response_model=SessionListResponse,
    summary="List sessions",
    description="Retrieve list of user's sessions with pagination and optional state filtering",
    dependencies=[Depends(require_permission(Permission.SESSION_READ))],
)
async def list_sessions(
    page: int = 1,
    per_page: int = 10,
    state: Optional[str] = None,
    manager: SessionManager = Depends(get_session_manager),
    current_user: TokenPayload = Depends(get_current_user),
) -> SessionListResponse:
    """
    List sessions with pagination.

    Args:
        page: Page number (1-based)
        per_page: Items per page (max 100)
        state: Optional state filter
        manager: Session manager dependency

    Returns:
        SessionListResponse with sessions and pagination info

    Raises:
        HTTPException: If pagination parameters are invalid
    """
    if page < 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Page must be >= 1")
    if per_page < 1 or per_page > 100:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Per page must be between 1 and 100"
        )

    try:
        # Get sessions with filtering
        sessions_data = await manager.list_sessions(
            user_id=current_user.user_id,
            page=page,
            per_page=per_page,
            state=state,
        )

        # Convert to response format
        sessions = [
            SessionResponse(
                session_id=session.session_id,
                status=session.state,
                created_at=session.created_at,
                updated_at=session.updated_at,
                config=session.config,
                description=session.description,
            )
            for session in sessions_data["sessions"]
        ]

        pagination = PaginationInfo(
            page=page,
            per_page=per_page,
            total=sessions_data["total"],
        )

        logger.info(f"Listed {len(sessions)} sessions for user {current_user.user_id}")

        return SessionListResponse(sessions=sessions, pagination=pagination)

    except Exception as e:
        logger.error(f"Failed to list sessions: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list sessions",
        )


@router.post(
    "",
    response_model=SessionCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create new simulation session",
    description="Initialize a new APGI simulation session with provided configuration",
    dependencies=[Depends(require_permission(Permission.SESSION_CREATE))],
)
async def create_session(
    request: SessionCreateRequest,
    req: Request,
    manager: SessionManager = Depends(get_session_manager),
    redis_client: redis.Redis = Depends(get_redis_client),
    current_user: TokenPayload = Depends(get_current_user),
) -> SessionCreateResponse:
    """
    Create new simulation session.

    Args:
        request: Session creation request with configuration
        req: HTTP request for idempotency key
        manager: Session manager dependency
        redis_client: Redis client for idempotency caching
        current_user: Current authenticated user

    Returns:
        SessionCreateResponse with session ID and details

    Raises:
        HTTPException: If session creation fails
    """
    # Check idempotency key
    cached_response = await check_idempotency_key(req, current_user.user_id, redis_client)
    if cached_response:  # pragma: no branch
        # Convert cached datetime string back to datetime object, preserving timezone
        if "created_at" in cached_response and isinstance(
            cached_response["created_at"], str
        ):  # pragma: no branch
            cached_response["created_at"] = datetime.fromisoformat(cached_response["created_at"])
        return SessionCreateResponse(**cached_response)

    # Create session
    session_id = await manager.create_session(request, user_id=current_user.user_id)

    # Get session details
    sim_session = await manager.get_session(session_id)

    logger.info(f"Session {session_id} created successfully")

    response_data = {
        "session_id": session_id,
        "status": sim_session.state.value,
        "created_at": sim_session.created_at,
        "config": sim_session.config,
    }

    # Cache response for idempotency
    idempotency_key = req.headers.get("Idempotency-Key")
    if idempotency_key:
        await cache_idempotency_response(
            current_user.user_id, idempotency_key, response_data, redis_client
        )

    return SessionCreateResponse(
        session_id=str(response_data["session_id"]),
        status=str(response_data["status"]),
        created_at=cast(datetime, response_data["created_at"]),
        config=cast(Dict[str, Any], response_data["config"]),
    )


@router.get(
    "/{session_id}",
    response_model=SessionResponse,
    summary="Get session details",
    description="Retrieve detailed information about a specific simulation session",
    dependencies=[Depends(require_permission(Permission.SESSION_READ))],
)
async def get_session(
    session_id: str,
    manager: SessionManager = Depends(get_session_manager),
    current_user: TokenPayload = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SessionResponse:
    """
    Get session details.

    Args:
        session_id: Unique session identifier
        manager: Session manager dependency

    Returns:
        SessionResponse with session details

    Raises:
        HTTPException: If session not found
    """
    # Validate session ownership
    await validate_session_ownership(
        session_id,
        current_user.user_id,
        manager,
        db,
        is_admin=has_any_role(current_user.roles, [Role.ADMIN]),
    )

    try:
        sim_session = await manager.get_session(session_id)
    except ValueError:
        raise SessionNotFoundError(session_id)

    return SessionResponse(
        session_id=session_id,
        status=sim_session.state.value,
        created_at=sim_session.created_at,
        updated_at=sim_session.updated_at,
        config=sim_session.config,
        description=getattr(sim_session, "description", "No description"),
    )


@router.get(
    "/{session_id}/metrics",
    response_model=SessionMetricsResponse,
    summary="Get session metrics",
    description="Retrieve computed simulation metrics for a specific session",
    dependencies=[Depends(require_permission(Permission.SESSION_READ))],
)
async def get_session_metrics(
    session_id: str,
    manager: SessionManager = Depends(get_session_manager),
    current_user: TokenPayload = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SessionMetricsResponse:
    """
    Get session metrics.

    Args:
        session_id: Unique session identifier
        manager: Session manager dependency

    Returns:
        SessionMetricsResponse with computed metrics

    Raises:
        HTTPException: If session not found
    """
    # Validate session ownership
    await validate_session_ownership(
        session_id,
        current_user.user_id,
        manager,
        db,
        is_admin=has_any_role(current_user.roles, [Role.ADMIN]),
    )

    try:
        sim_session = await manager.get_session(session_id)
    except ValueError:
        raise SessionNotFoundError(session_id)

    # Get current session state
    state = await sim_session.get_state()

    # Compute metrics from state
    # Assuming state contains allostatic, body, ignition, etc.
    allostatic = state.get("allostatic", {})
    body = state.get("body", {})
    ignition = state.get("ignition", {})

    metrics = {
        "session_id": session_id,
        "ignition_frequency": ignition.get("intensity", 0.0),
        "free_energy": allostatic.get("load", 0.0),
        "metabolic_load": body.get("energy", 0.0),
        "additional_metrics": {
            "allostatic_threshold": allostatic.get("threshold", 0.0),
            "arousal": body.get("arousal", 0.0),
            "ignition_active": ignition.get("active", False),
        },
    }

    logger.info(f"Retrieved metrics for session {session_id}")

    return SessionMetricsResponse(**metrics)


@router.get(
    "/{session_id}/tasks",
    response_model=SessionTaskListResponse,
    summary="List tasks for session",
    description="Retrieve list of tasks associated with a specific session",
    dependencies=[Depends(require_permission(Permission.TASK_READ))],
)
async def get_session_tasks(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> SessionTaskListResponse:
    """
    Get tasks for a session.

    Args:
        session_id: Unique session identifier
        db: Database session

    Returns:
        SessionTaskListResponse with tasks for the session

    Raises:
        HTTPException: If session not found
    """

    # Blocking function for DB queries
    def _blocking_get_tasks() -> Optional[object]:
        # Verify session exists
        session = db.query(SessionModel).filter(SessionModel.session_id == session_id).first()
        if not session:
            return None

        # Verify session ownership with admin bypass
        is_admin = has_any_role(current_user.roles, [Role.ADMIN])
        if session.user_id != current_user.user_id and not is_admin:
            return "forbidden"

        # Get tasks for session
        tasks = db.query(Task).filter(Task.session_id == session_id).all()
        return tasks

    # Execute DB queries in executor to avoid blocking event loop
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _blocking_get_tasks)

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found"
        )

    if result == "forbidden":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    tasks = cast(List[Task], result)

    # Convert to response format
    task_responses = [
        TaskStatusResponse(
            task_id=str(task.task_id),
            status=str(task.status),
            state=str(task.status),  # Use status as state since Celery state not stored in DB
            result=dict(task.result_data) if task.result_data else None,
            error=str(task.error_message) if task.error_message else None,
            info=None,
        )
        for task in tasks
    ]

    logger.info(f"Retrieved {len(task_responses)} tasks for session {session_id}")

    return SessionTaskListResponse(tasks=task_responses)


@router.post(
    "/{session_id}/start",
    response_model=SessionActionResponse,
    summary="Start simulation",
    description="Start or resume simulation for specified session",
    dependencies=[Depends(require_permission(Permission.SESSION_CONTROL))],
)
async def start_session(
    session_id: str,
    manager: SessionManager = Depends(get_session_manager),
    current_user: TokenPayload = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SessionActionResponse:
    """
    Start simulation.

    Args:
        session_id: Unique session identifier
        manager: Session manager dependency

    Returns:
        SessionActionResponse with updated status

    Raises:
        HTTPException: If session not found or cannot be started
    """
    # Validate session ownership
    await validate_session_ownership(
        session_id,
        current_user.user_id,
        manager,
        db,
        is_admin=has_any_role(current_user.roles, [Role.ADMIN]),
    )

    try:
        sim_session = await manager.get_session(session_id)
    except ValueError:
        raise SessionNotFoundError(session_id)

    try:
        result = await sim_session.start()
    except ValueError:
        # State conflict - trying to start session in invalid state
        raise SessionStateConflictError(session_id, sim_session.state.value, "start")

    # Update state in database
    await manager.update_session_state(session_id, SessionLifecycleState.RUNNING)

    logger.info(f"Session {session_id} started")

    return SessionActionResponse(
        session_id=session_id, status=result["status"], timestamp=sim_session.updated_at
    )


@router.post(
    "/{session_id}/pause",
    response_model=SessionActionResponse,
    summary="Pause simulation",
    description="Pause simulation while preserving current state",
    dependencies=[Depends(require_permission(Permission.SESSION_CONTROL))],
)
async def pause_session(
    session_id: str,
    manager: SessionManager = Depends(get_session_manager),
    current_user: TokenPayload = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SessionActionResponse:
    """
    Pause simulation.

    Args:
        session_id: Unique session identifier
        manager: Session manager dependency

    Returns:
        SessionActionResponse with updated status

    Raises:
        HTTPException: If session not found or cannot be paused
    """
    # Validate session ownership
    await validate_session_ownership(
        session_id,
        current_user.user_id,
        manager,
        db,
        is_admin=has_any_role(current_user.roles, [Role.ADMIN]),
    )

    try:
        sim_session = await manager.get_session(session_id)
    except ValueError:
        raise SessionNotFoundError(session_id)

    try:
        result = await sim_session.pause()
    except ValueError:
        # State conflict - trying to pause session in invalid state
        raise SessionStateConflictError(session_id, sim_session.state.value, "pause")

    # Update state in database
    await manager.update_session_state(session_id, SessionLifecycleState.PAUSED)

    logger.info(f"Session {session_id} paused")

    return SessionActionResponse(
        session_id=session_id, status=result["status"], timestamp=sim_session.updated_at
    )


@router.post(
    "/{session_id}/stop",
    response_model=SessionActionResponse,
    summary="Stop simulation",
    description="Stop simulation for specified session",
    dependencies=[Depends(require_permission(Permission.SESSION_CONTROL))],
)
async def stop_session(
    session_id: str,
    manager: SessionManager = Depends(get_session_manager),
    current_user: TokenPayload = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SessionActionResponse:
    """
    Stop simulation.

    Args:
        session_id: Unique session identifier
        manager: Session manager dependency

    Returns:
        SessionActionResponse with updated status

    Raises:
        HTTPException: If session not found
    """
    # Validate session ownership
    await validate_session_ownership(
        session_id,
        current_user.user_id,
        manager,
        db,
        is_admin=has_any_role(current_user.roles, [Role.ADMIN]),
    )

    try:
        sim_session = await manager.get_session(session_id)
    except ValueError:
        raise SessionNotFoundError(session_id)

    try:
        result = await sim_session.stop()
    except ValueError:
        # State conflict - trying to stop session in invalid state
        raise SessionStateConflictError(session_id, sim_session.state.value, "stop")

    # Update state in database
    await manager.update_session_state(session_id, SessionLifecycleState.STOPPED)

    logger.info(f"Session {session_id} stopped")

    return SessionActionResponse(
        session_id=session_id, status=result["status"], timestamp=sim_session.updated_at
    )


@router.post(
    "/{session_id}/reset",
    response_model=SessionActionResponse,
    summary="Reset simulation",
    description="Reset simulation to initial conditions",
    dependencies=[Depends(require_permission(Permission.SESSION_CONTROL))],
)
async def reset_session(
    session_id: str,
    manager: SessionManager = Depends(get_session_manager),
    current_user: TokenPayload = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SessionActionResponse:
    """
    Reset simulation to initial state.

    Args:
        session_id: Unique session identifier
        manager: Session manager dependency

    Returns:
        SessionActionResponse with updated status

    Raises:
        HTTPException: If session not found
    """
    # Validate session ownership
    await validate_session_ownership(
        session_id,
        current_user.user_id,
        manager,
        db,
        is_admin=has_any_role(current_user.roles, [Role.ADMIN]),
    )

    try:
        sim_session = await manager.get_session(session_id)
    except ValueError:
        raise SessionNotFoundError(session_id)

    try:
        result = await sim_session.reset()
    except ValueError:
        # State conflict - trying to reset session in invalid state
        raise SessionStateConflictError(session_id, sim_session.state.value, "reset")

    # Update state in database
    await manager.update_session_state(session_id, SessionLifecycleState.CREATED)

    logger.info(f"Session {session_id} reset")

    return SessionActionResponse(
        session_id=session_id, status=result["status"], timestamp=sim_session.updated_at
    )


@router.delete(
    "/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete session",
    description="Delete session and clean up all associated resources",
    dependencies=[Depends(require_permission(Permission.SESSION_DELETE))],
)
async def delete_session(
    session_id: str,
    manager: SessionManager = Depends(get_session_manager),
    current_user: TokenPayload = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """
    Delete session and clean up resources.

    Args:
        session_id: Unique session identifier
        manager: Session manager dependency

    Returns:
        No content (204)

    Raises:
        HTTPException: If session not found or deletion fails
    """
    # Validate session ownership
    await validate_session_ownership(
        session_id,
        current_user.user_id,
        manager,
        db,
        is_admin=has_any_role(current_user.roles, [Role.ADMIN]),
    )

    try:
        # Verify session exists before deletion
        await manager.get_session(session_id)
    except ValueError:
        raise SessionNotFoundError(session_id)

    # Delete session
    await manager.delete_session(session_id)

    logger.info(f"Session {session_id} deleted")

    return None


@router.post(
    "/{session_id}/step",
    response_model=SessionActionResponse,
    summary="Step simulation",
    description="Execute single simulation step for specified session",
    dependencies=[Depends(require_permission(Permission.SESSION_CONTROL))],
)
async def step_session(
    session_id: str,
    extero_input: Optional[Dict[str, Any]] = None,
    manager: SessionManager = Depends(get_session_manager),
    current_user: TokenPayload = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SessionActionResponse:
    """
    Execute single simulation step.

    Args:
        session_id: Unique session identifier
        extero_input: Optional exteroceptive input for this step
        manager: Session manager dependency

    Returns:
        SessionActionResponse with updated status and state

    Raises:
        HTTPException: If session not found or cannot be stepped
    """
    # Validate session ownership
    await validate_session_ownership(
        session_id,
        current_user.user_id,
        manager,
        db,
        is_admin=has_any_role(current_user.roles, [Role.ADMIN]),
    )

    try:
        sim_session = await manager.get_session(session_id)
    except ValueError:
        raise SessionNotFoundError(session_id)

    try:
        # Execute step with provided input (default to empty dict if not provided)
        state = await sim_session.step(extero_input or {})

        logger.info(f"Session {session_id} stepped successfully")

        return SessionActionResponse(
            session_id=session_id, status="stepped", timestamp=sim_session.updated_at
        )
    except ValueError as e:
        # Session not in running state or other error
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Cannot step session: {str(e)}"
        )
