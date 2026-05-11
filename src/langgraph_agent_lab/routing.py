"""Routing functions for conditional edges."""

from __future__ import annotations

from .state import AgentState, Route


def route_after_classify(state: AgentState) -> str:
    """Map classified route to the next graph node.

    Unknown routes fail closed to clarification instead of answering with unsupported assumptions.
    """
    route = state.get("route", Route.SIMPLE.value)
    mapping = {
        Route.SIMPLE.value: "answer",
        Route.TOOL.value: "tool",
        Route.MISSING_INFO.value: "clarify",
        Route.RISKY.value: "risky_action",
        Route.ERROR.value: "retry",
    }
    return mapping.get(route, "clarify")


def route_after_retry(state: AgentState) -> str:
    """Decide whether to retry, fallback, or dead-letter.

    The retry node has already incremented attempt. If the budget is exhausted, the graph terminates
    through dead_letter; otherwise it loops back to the tool node.
    """
    if int(state.get("attempt", 0)) >= int(state.get("max_attempts", 3)):
        return "dead_letter"
    return "tool"


def route_after_evaluate(state: AgentState) -> str:
    """Decide whether tool result is satisfactory or needs retry.

    This is the done-check that enables retry loops. Only an explicit needs_retry value loops; all
    other evaluation states proceed to answer.
    """
    if state.get("evaluation_result") == "needs_retry":
        return "retry"
    return "answer"


def route_after_approval(state: AgentState) -> str:
    """Continue only if approved.

    Rejected approval returns to clarification so the workflow still terminates safely.
    """
    approval = state.get("approval") or {}
    return "tool" if approval.get("approved") else "clarify"
