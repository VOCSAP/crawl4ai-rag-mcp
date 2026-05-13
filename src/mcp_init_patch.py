"""
Monkey-patch ServerSession._received_request to tolerate the SSE init race
documented in upstream issues:
  - modelcontextprotocol/python-sdk#1844 (OPEN, mentions Claude Code)
  - modelcontextprotocol/python-sdk#2214 (closed not-planned)

Two failure modes are covered, both currently produce
"RuntimeError: Received request before initialization was complete" with the
stock library and silently break the SSE session for Claude Code:

  1. Init race on a fresh session: the client sends InitializeRequest then
     a tools/call without waiting for the InitializeResult. The server is
     still in Initializing state when the tools/call lands. We poll for the
     state to flip to Initialized before delegating to the original handler.

  2. Silent reconnect after a server restart or network drop: the client
     reuses an existing SSE conversation and never re-sends InitializeRequest.
     The server-side session is brand new in NotInitialized state. We wait
     up to INIT_WAIT_TIMEOUT_S and then force the state to Initialized so
     the request can proceed. This is non-conformant strictly speaking, but
     safe for a FastMCP server that exposes a static set of tools and does
     not depend on client-advertised capabilities.

The patch must be imported BEFORE FastMCP instantiates any ServerSession.
See KNOWN_ISSUES.md section I for the broader context.
"""

import asyncio
import logging

from mcp import types
from mcp.server.session import InitializationState, ServerSession

log = logging.getLogger(__name__)

INIT_WAIT_TIMEOUT_S = 10.0
INIT_POLL_INTERVAL_S = 0.1

_original_received_request = ServerSession._received_request


async def _patched_received_request(self, responder):
    is_init_request = isinstance(responder.request.root, types.InitializeRequest)
    if is_init_request or self._initialization_state == InitializationState.Initialized:
        return await _original_received_request(self, responder)

    waited = 0.0
    while waited < INIT_WAIT_TIMEOUT_S:
        await asyncio.sleep(INIT_POLL_INTERVAL_S)
        waited += INIT_POLL_INTERVAL_S
        if self._initialization_state == InitializationState.Initialized:
            return await _original_received_request(self, responder)

    log.warning(
        "session %s never reached Initialized after %.1fs; auto-initializing",
        id(self),
        INIT_WAIT_TIMEOUT_S,
    )
    self._initialization_state = InitializationState.Initialized
    return await _original_received_request(self, responder)


ServerSession._received_request = _patched_received_request
log.info("ServerSession._received_request patched (SSE init race workaround)")
