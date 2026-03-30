Python 3.9 + pytest-asyncio 0.24 quirks:
- `asyncio_default_fixture_loop_scope = function` in pytest.ini (not session) — each test owns its loop lifecycle
- Schema setup uses a sync fixture with `asyncio.run()` to stay outside pytest-asyncio's loop management
- Each test gets a fresh `create_async_engine(NullPool)` — no shared connection state
- sse_starlette stores `AppStatus.should_exit_event` as a class-level `anyio.Event` bound to the first loop — reset to `None` before each test via autouse fixture in conftest
- AsyncMock `side_effect=[list]` raises StopIteration → RuntimeError inside LangGraph async generators on Python 3.9. Use a dispatch coroutine as `side_effect` instead.
- Lazy-import `graph` inside the chat stream handler to avoid LangGraph `compile()` conflicting with pytest-asyncio function-scoped event loops
