from unittest.mock import MagicMock

import psycopg
import pytest

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
