"""Node implementations for the LangGraph workflow.

Each function is small, testable, and returns a partial state update. None of the nodes mutate the
input state in place; LangGraph merges returned updates into the shared state.
"""

from __future__ import annotations

import re

from .state import AgentState, ApprovalDecision, Route, make_event


RISKY_KEYWORDS = {"refund", "delete", "send", "cancel", "remove", "revoke"}
TOOL_KEYWORDS = {"status", "order", "lookup", "check", "track", "find", "search"}
MISSING_INFO_PRONOUNS = {"it", "this", "that", "thing", "issue", "problem"}
ERROR_KEYWORDS = {"timeout", "fail", "failure", "error", "crash", "unavailable", "cannot recover"}


def _normalized_words(query: str) -> list[str]:
    """Split text into lowercase words so keyword checks do not match substrings."""
    return re.findall(r"[a-z0-9]+", query.lower())


def _contains_phrase(query: str, phrase: str) -> bool:
    """Match both single-word keywords and short phrases such as 'cannot recover'."""
    if " " in phrase:
        return phrase in query
    return phrase in _normalized_words(query)


def intake_node(state: AgentState) -> dict:
    """Normalize raw query into state fields.

    This node keeps the workflow deterministic: later nodes receive a stripped query and the
    append-only audit log records how the run started.
    """
    query = state.get("query", "").strip()
    return {
        "query": query,
        "messages": [f"intake:{query[:40]}"],
        "events": [make_event("intake", "completed", "query normalized")],
    }


def classify_node(state: AgentState) -> dict:
    """Classify the query into a route.

    Required routes: simple, tool, missing_info, risky, error. The policy is keyword-based rather
    than scenario-id-based, so hidden scenarios with similar wording still route correctly.
    """
    query = state.get("query", "").lower()
    clean_words = _normalized_words(query)
    route = Route.SIMPLE
    risk_level = "low"

    # Priority matters: destructive or external actions must be reviewed before any tool call.
    if any(keyword in clean_words for keyword in RISKY_KEYWORDS):
        route = Route.RISKY
        risk_level = "high"
    elif any(keyword in clean_words for keyword in TOOL_KEYWORDS):
        route = Route.TOOL
    elif len(clean_words) <= 5 and any(word in clean_words for word in MISSING_INFO_PRONOUNS):
        route = Route.MISSING_INFO
    elif any(_contains_phrase(query, keyword) for keyword in ERROR_KEYWORDS):
        route = Route.ERROR

    return {
        "route": route.value,
        "risk_level": risk_level,
        "events": [
            make_event(
                "classify",
                "completed",
                f"route={route.value}",
                matched_policy=route.value,
                risk_level=risk_level,
            )
        ],
    }


def ask_clarification_node(state: AgentState) -> dict:
    """Ask for missing information instead of hallucinating.

    Clarification is a terminating route in this lab: the agent asks for the missing detail and
    stops, instead of guessing or calling tools with incomplete input.
    """
    question = "Can you provide the order id or the missing context?"
    return {
        "pending_question": question,
        "final_answer": question,
        "events": [make_event("clarify", "completed", "missing information requested")],
    }


def tool_node(state: AgentState) -> dict:
    """Call a mock tool.

    Error-route scenarios fail on early attempts, then succeed when retry budget allows it. This
    demonstrates LangGraph loop behavior without depending on an external service.
    """
    attempt = int(state.get("attempt", 0))
    if state.get("route") == Route.ERROR.value and attempt < 2:
        scenario_id = state.get("scenario_id", "unknown")
        result = f"ERROR: transient failure attempt={attempt} scenario={scenario_id}"
    else:
        result = f"mock-tool-result for scenario={state.get('scenario_id', 'unknown')}"
    return {
        "tool_results": [result],
        "events": [make_event("tool", "completed", f"tool executed attempt={attempt}")],
    }


def risky_action_node(state: AgentState) -> dict:
    """Prepare a risky action for approval.

    The proposed action is separated from approval so reviewers see exactly what would happen.
    """
    query = state.get("query", "the support request")
    return {
        "proposed_action": f"Review and approve risky support action for: {query}",
        "events": [
            make_event(
                "risky_action",
                "pending_approval",
                "approval required",
                risk_level=state.get("risk_level", "unknown"),
            )
        ],
    }


def approval_node(state: AgentState) -> dict:
    """Human approval step with optional LangGraph interrupt().

    The mock branch keeps tests offline. Setting LANGGRAPH_INTERRUPT=true switches to real HITL,
    where LangGraph pauses and resumes with an ApprovalDecision-compatible payload.
    """
    import os

    if os.getenv("LANGGRAPH_INTERRUPT", "").lower() == "true":
        from langgraph.types import interrupt

        value = interrupt({
            "proposed_action": state.get("proposed_action"),
            "risk_level": state.get("risk_level"),
        })
        if isinstance(value, dict):
            decision = ApprovalDecision(**value)
        else:
            decision = ApprovalDecision(approved=bool(value))
    else:
        decision = ApprovalDecision(approved=True, comment="mock approval for lab")

    return {
        "approval": decision.model_dump(),
        "events": [make_event("approval", "completed", f"approved={decision.approved}")],
    }


def retry_or_fallback_node(state: AgentState) -> dict:
    """Record a retry attempt or fallback decision.

    This node increments the attempt counter. The actual bound check lives in routing.py so the
    graph topology owns the loop/termination decision.
    """
    attempt = int(state.get("attempt", 0)) + 1
    backoff_ms = min(1000 * (2 ** max(attempt - 1, 0)), 8000)
    errors = [f"transient failure attempt={attempt}"]
    return {
        "attempt": attempt,
        "errors": errors,
        "events": [
            make_event(
                "retry",
                "completed",
                "retry attempt recorded",
                attempt=attempt,
                max_attempts=state.get("max_attempts", 3),
                backoff_ms=backoff_ms,
            )
        ],
    }


def answer_node(state: AgentState) -> dict:
    """Produce a final response.

    The answer is grounded in the last tool result when a tool was used; simple questions receive a
    safe canned response because this lab focuses on orchestration, not LLM generation quality.
    """
    if state.get("tool_results"):
        answer = f"I found: {state['tool_results'][-1]}"
    else:
        answer = "This is a safe mock answer. Replace with your agent response."
    return {
        "final_answer": answer,
        "events": [make_event("answer", "completed", "answer generated")],
    }


def evaluate_node(state: AgentState) -> dict:
    """Evaluate tool results and decide whether the workflow is done.

    A real system could use structured validation or an LLM judge. Here, an ERROR marker is enough
    to demonstrate the retry gate deterministically.
    """
    tool_results = state.get("tool_results", [])
    latest = tool_results[-1] if tool_results else ""
    if "ERROR" in latest:
        return {
            "evaluation_result": "needs_retry",
            "events": [
                make_event("evaluate", "completed", "tool result indicates failure, retry needed")
            ],
        }
    return {
        "evaluation_result": "success",
        "events": [make_event("evaluate", "completed", "tool result satisfactory")],
    }


def dead_letter_node(state: AgentState) -> dict:
    """Log unresolvable failures for manual review.

    In production this would write to a queue or ticket system. For the lab, the event and final
    answer are the evidence that the bounded retry loop terminated safely.
    """
    answer = (
        "Request could not be completed after maximum retry attempts. "
        "Logged for manual review."
    )
    return {
        "final_answer": answer,
        "errors": [f"dead_letter after attempt={state.get('attempt', 0)}"],
        "events": [
            make_event(
                "dead_letter",
                "completed",
                f"max retries exceeded, attempt={state.get('attempt', 0)}",
            )
        ],
    }


def finalize_node(state: AgentState) -> dict:
    """Finalize the run and emit a final audit event."""
    return {"events": [make_event("finalize", "completed", "workflow finished")]}
