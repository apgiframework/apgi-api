"""
State Access Routes

API endpoints for accessing APGI system state, ignition history, and subsystem data.
"""

import logging
from typing import Tuple

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.models.schemas import (
    AllostaticState,
    BodyState,
    ErrorResponse,
    IgnitionState,
    MetabolicState,
    MinimalSelfState,
    NarrativeSelfState,
    PrecisionState,
    SelfModelState,
    SystemStateResponse,
    WorkspaceState,
)
from app.modules.scoring.exposure import ExposureGate, GateStateResponse
from app.routes.sessions import get_session_manager, validate_session_ownership
from app.services.auth_manager import TokenPayload
from app.services.authorization import Permission, get_current_user, require_permission
from app.services.k7_gate import K7Parameter, is_cleared
from app.services.session_manager import SessionManager

logger = logging.getLogger(__name__)


# Create router
router = APIRouter(
    prefix="/v1/sessions",
    tags=["State Access"],
    responses={
        404: {"model": ErrorResponse, "description": "Session not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)


@router.get(
    "/{session_id}/state",
    response_model=SystemStateResponse,
    response_model_exclude_none=True,
    summary="Get complete system state",
    description="Retrieve the complete current state of all APGI subsystems for the specified session",
    dependencies=[Depends(require_permission(Permission.SESSION_READ))],
)
async def get_system_state(
    session_id: str,
    manager: SessionManager = Depends(get_session_manager),
    current_user: TokenPayload = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SystemStateResponse:
    """
    Get complete system state.

    Returns comprehensive state information including:
    - Ignition status and dynamics
    - Global workspace state
    - Interoceptive body state
    - Allostatic regulation
    - Precision weighting
    - Metabolic reserves
    - Self-model state

    Args:
        session_id: Unique session identifier
        manager: Session manager dependency

    Returns:
        SystemStateResponse with complete state

    Raises:
        HTTPException: If session not found or state cannot be retrieved
    """
    # Validate session ownership
    await validate_session_ownership(session_id, current_user.user_id, manager, db)

    try:
        # Get session with ownership validation
        sim_session = await manager.get_session(session_id, current_user.user_id)

        # Get complete state
        state = await sim_session.get_state()

        # Extract and format state components
        ignition_data = state.get("ignition", {})
        workspace_data = state.get("workspace", {})
        body_data = state.get("body", {})
        allostasis_data = state.get("allostasis", {})
        precision_data = state.get("precision", {})
        metabolism_data = state.get("metabolism", {})
        self_model_data = state.get("self_model", {})

        # Build response
        response = SystemStateResponse(
            time_ms=state.get("time", 0.0),
            ignition=IgnitionState(
                ignition_occurred=ignition_data.get("ignition_occurred", False),
                total_signal=ignition_data.get("total_signal", 0.0),
                threshold=(
                    ignition_data.get("threshold", 0.0)
                    if is_cleared(K7Parameter.IGNITION_THRESHOLD)
                    else None
                ),
                duration_ms=ignition_data.get("duration_ms"),
            ),
            workspace=WorkspaceState(
                is_broadcasting=workspace_data.get("is_broadcasting", False),
                content=workspace_data.get("content"),
                broadcast_duration_ms=workspace_data.get("broadcast_duration_ms"),
            ),
            body=BodyState(
                heart_rate=(
                    body_data.get("heart_rate", 70.0)
                    if is_cleared(K7Parameter.INTEROCEPTIVE_BODY_STATE)
                    else None
                ),
                cortisol=(
                    body_data.get("cortisol", 0.1)
                    if is_cleared(K7Parameter.INTEROCEPTIVE_BODY_STATE)
                    else None
                ),
                temperature=(
                    body_data.get("temperature", 37.0)
                    if is_cleared(K7Parameter.INTEROCEPTIVE_BODY_STATE)
                    else None
                ),
            ),
            allostasis=AllostaticState(allostatic_load=allostasis_data.get("allostatic_load", 0.0)),
            precision=PrecisionState(
                exteroceptive=(
                    precision_data.get("exteroceptive", 1.0)
                    if is_cleared(K7Parameter.PRECISION_EXTEROCEPTIVE)
                    else None
                ),
                interoceptive=(
                    precision_data.get("interoceptive", 1.0)
                    if is_cleared(K7Parameter.PRECISION_INTEROCEPTIVE)
                    else None
                ),
            ),
            metabolism=MetabolicState(
                reserves=metabolism_data.get("reserves", 1000.0),
                reserve_fraction=metabolism_data.get("reserve_fraction", 1.0),
            ),
            self_model=SelfModelState(
                minimal=MinimalSelfState(
                    coherence=self_model_data.get("minimal", {}).get("coherence", 0.5)
                ),
                narrative=NarrativeSelfState(
                    narrative=self_model_data.get("narrative", {}).get("narrative", "")
                ),
            ),
        )

        logger.info(f"Retrieved state for session {session_id}")
        return response

    except ValueError as e:
        logger.warning(f"Session {session_id} not found: {e}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found"
        )
    except Exception as e:
        logger.exception("Failed to get system state")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred",
        )


_GATED_ROUTES = {
    "ignition-history": ("ignition_state", "ignition_threshold"),
    "interoception": ("interoceptive_body_state",),
    "prediction-errors": ("prediction_error_exteroceptive", "prediction_error_interoceptive"),
    "somatic-markers": ("somatic_bias",),
    "state-vector": (
        "ignition_state",
        "ignition_threshold",
        "precision_exteroceptive",
        "precision_interoceptive",
        "prediction_error_exteroceptive",
        "prediction_error_interoceptive",
        "somatic_bias",
    ),
}


def _register_gated_route(name: str, parameters: Tuple[str, ...]) -> None:
    async def handler(
        session_id: str,
        manager: SessionManager = Depends(get_session_manager),
        current_user: TokenPayload = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> GateStateResponse:
        await validate_session_ownership(session_id, current_user.user_id, manager, db)
        return ExposureGate().gate_state(session_id, *parameters)

    handler.__name__ = f"get_{name.replace('-', '_')}_gate_state"
    router.add_api_route(
        f"/{{session_id}}/{name}",
        handler,
        methods=["GET"],
        response_model=GateStateResponse,
        summary=f"Identifiability gate state for {name}",
        description="Latent APGI quantities are withheld until each clears the identifiability "
        "gate; this route returns the gate state only, never values.",
        dependencies=[Depends(require_permission(Permission.SESSION_READ))],
    )


for _name, _params in _GATED_ROUTES.items():
    _register_gated_route(_name, _params)
