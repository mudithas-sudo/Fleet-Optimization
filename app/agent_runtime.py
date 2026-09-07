"""Shared ADK plumbing for the programmatic (non-chat) agents.

`AgentRun` wraps an ADK `Agent` + `Runner` + `InMemorySessionService`. Each
call opens a fresh session with the given initial state, drives the agent to
completion over one user message, and returns the final session state — where
`output_key` results and tool-stashed data (e.g. `routes_api_raw`) live.
"""

import uuid

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

USER_ID = "system"


class AgentRun:
    def __init__(self, app_name: str, agent):
        self.app_name = app_name
        self._sessions = InMemorySessionService()
        self._runner = Runner(app_name=app_name, agent=agent,
                              session_service=self._sessions)

    async def __call__(self, prompt: str, state: dict | None = None) -> dict:
        session_id = f"{self.app_name}-{uuid.uuid4().hex[:8]}"
        await self._sessions.create_session(
            app_name=self.app_name, user_id=USER_ID, session_id=session_id,
            state=state or {})
        message = types.Content(role="user", parts=[types.Part(text=prompt)])
        async for _event in self._runner.run_async(
                user_id=USER_ID, session_id=session_id, new_message=message):
            pass
        session = await self._sessions.get_session(
            app_name=self.app_name, user_id=USER_ID, session_id=session_id)
        return dict(session.state)
