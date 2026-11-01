from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.database import (
    close_db_engine,
    get_db_engine,
    get_db_session,
    get_session_factory,
    ping_database,
)


@pytest.mark.asyncio
async def test_ping_database_success() -> None:
    """Test ping_database returns True and latency when query succeeds."""
    mock_conn = AsyncMock()
    mock_conn.execute.return_value = None

    mock_engine = MagicMock()
    mock_engine.connect.return_value.__aenter__.return_value = mock_conn
    mock_engine.connect.return_value.__aexit__.return_value = None

    with patch("app.core.database.get_db_engine", return_value=mock_engine):
        is_healthy, details = await ping_database()
        assert is_healthy is True
        assert details is not None
        assert "latency_ms" in details


@pytest.mark.asyncio
async def test_ping_database_failure() -> None:
    """Test ping_database returns False and error message when connection fails."""
    mock_engine = MagicMock()
    mock_engine.connect.side_effect = ConnectionRefusedError("Database offline")

    with patch("app.core.database.get_db_engine", return_value=mock_engine):
        is_healthy, details = await ping_database()
        assert is_healthy is False
        assert details is not None
        assert "Database offline" in details["error"]


@pytest.mark.asyncio
async def test_get_db_session_lifecycle() -> None:
    """Test get_db_session commits on success and closes the session."""
    mock_session = AsyncMock()
    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__.return_value = mock_session
    mock_session_factory.return_value.__aexit__.return_value = None

    with patch("app.core.database.get_session_factory", return_value=mock_session_factory):
        gen = get_db_session()
        session = await anext(gen)
        assert session == mock_session

        # Advance generator to completion (success path)
        with pytest.raises(StopAsyncIteration):
            await anext(gen)

        mock_session.commit.assert_awaited_once()
        mock_session.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_db_session_rollback_on_error() -> None:
    """Test get_db_session rolls back on exception and closes the session."""
    mock_session = AsyncMock()
    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__.return_value = mock_session
    mock_session_factory.return_value.__aexit__.return_value = None

    with patch("app.core.database.get_session_factory", return_value=mock_session_factory):
        gen = get_db_session()
        session = await anext(gen)
        assert session == mock_session

        # Simulate exception thrown back into generator (like FastAPI does on route error)
        with pytest.raises(RuntimeError, match="Transaction failed"):
            await gen.athrow(RuntimeError("Transaction failed"))

        mock_session.rollback.assert_awaited_once()
        mock_session.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_engine_initialization_and_close() -> None:
    """Test get_db_engine singleton behavior and close_db_engine cleanup."""
    engine = get_db_engine()
    assert engine is not None
    factory = get_session_factory()
    assert factory is not None

    await close_db_engine()
