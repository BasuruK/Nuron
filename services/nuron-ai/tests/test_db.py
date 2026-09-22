from unittest.mock import MagicMock

import psycopg
import pytest
from psycopg import sql

from nuron_ai import db


@pytest.mark.parametrize("lease_seconds", [0.0, -1.0, float("nan"), float("inf")])
def test_claim_rejects_non_positive_or_non_finite_lease(lease_seconds: float) -> None:
    conn = MagicMock(spec=psycopg.Connection)

    with pytest.raises(ValueError, match="lease_seconds"):
        db.claim(conn, "worker-1", "landed", lease_seconds=lease_seconds)

    conn.execute.assert_not_called()


def test_claim_rolls_back_database_error_before_reraising() -> None:
    conn = MagicMock(spec=psycopg.Connection)
    conn.execute.side_effect = psycopg.DataError("invalid state")

    with pytest.raises(psycopg.DataError, match="invalid state"):
        db.claim(conn, "worker-1", "not-a-state", lease_seconds=60.0)

    conn.rollback.assert_called_once_with()


def test_release_rolls_back_database_error_before_reraising() -> None:
    conn = MagicMock(spec=psycopg.Connection)
    conn.execute.side_effect = psycopg.DataError("bad graph")

    with pytest.raises(psycopg.DataError, match="bad graph"):
        db.release(conn, "abc", "worker-1", 1, sql.SQL("state = 'compiled'"), {})

    conn.rollback.assert_called_once_with()
    conn.commit.assert_not_called()


def test_release_attaches_rollback_failure_to_original_database_error() -> None:
    conn = MagicMock(spec=psycopg.Connection)
    conn.execute.side_effect = psycopg.DataError("bad graph")
    conn.rollback.side_effect = psycopg.OperationalError("connection closed")

    with pytest.raises(psycopg.DataError, match="bad graph") as raised:
        db.release(conn, "abc", "worker-1", 1, sql.SQL("state = 'compiled'"), {})

    assert raised.value.__notes__ == [
        "rollback after release failure also failed: OperationalError: connection closed",
    ]
