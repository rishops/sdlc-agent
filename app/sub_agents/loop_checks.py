"""Loop exit control.

A `LoopAgent` exits when a sub-agent yields an event with `actions.escalate=True`
(or `max_iterations` is hit). `EscalationChecker` is a tiny `BaseAgent` that
escalates once a named boolean field in a state slot becomes true — used to exit
the plan-approval loop (on `plan_approval.approved`) and the build loop (on
`test_report.passed`).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions


class EscalationChecker(BaseAgent):
    """Escalates (exits the enclosing loop) when ``state[state_key][bool_field]`` is true."""

    state_key: str
    bool_field: str

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        slot = ctx.session.state.get(self.state_key) or {}
        if slot.get(self.bool_field):
            yield Event(author=self.name, actions=EventActions(escalate=True))
        else:
            yield Event(author=self.name)


def create_escalation_checker(
    name: str, state_key: str, bool_field: str
) -> EscalationChecker:
    return EscalationChecker(name=name, state_key=state_key, bool_field=bool_field)
