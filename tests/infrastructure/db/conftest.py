import pytest
from sqlalchemy import Connection

from app.infrastructure.db.seed import seed


@pytest.fixture
def semillas(conn: Connection) -> None:
    """Estados, categorías, especies y parámetro de Trips reales (con los nombres que usan los objetos SQL)."""
    seed(conn)
