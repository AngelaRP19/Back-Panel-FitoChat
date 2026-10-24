from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tests.infrastructure.db.factories import escenario


def _punto(session: Session, tabla: str, id_col: str, id_valor: str) -> tuple[float, float, int]:
    fila = session.execute(
        text(f"SELECT ST_X(ubicacion), ST_Y(ubicacion), ST_SRID(ubicacion) FROM {tabla} WHERE {id_col} = :id"),
        {"id": id_valor},
    ).one()
    return float(fila[0]), float(fila[1]), int(fila[2])


def test_ubicacion_de_finca_se_calcula_de_longitud_y_latitud(session: Session) -> None:
    datos = escenario(session)
    x, y, srid = _punto(session, "fincas", "id_finca", datos.finca.id_finca)
    assert (x, y, srid) == (pytest.approx(-73.171), pytest.approx(5.2832), 4326)


def test_ubicacion_de_cultivo_se_calcula(session: Session) -> None:
    datos = escenario(session)
    x, y, srid = _punto(session, "cultivos", "id_cultivo", datos.cultivo.id_cultivo)
    assert (x, y, srid) == (pytest.approx(-73.1705), pytest.approx(5.284), 4326)


def test_ubicacion_se_recalcula_al_cambiar_coordenadas(session: Session) -> None:
    datos = escenario(session)
    datos.finca.latitud_finca = Decimal("5.300000")
    datos.finca.longitud_finca = Decimal("-73.150000")
    session.flush()
    x, y, _ = _punto(session, "fincas", "id_finca", datos.finca.id_finca)
    assert (x, y) == (pytest.approx(-73.15), pytest.approx(5.3))


def test_ubicacion_no_se_puede_escribir(session: Session) -> None:
    datos = escenario(session)
    with pytest.raises(DBAPIError, match="generated"):
        with session.begin_nested():
            session.execute(
                text("UPDATE fincas SET ubicacion = ST_SetSRID(ST_MakePoint(0, 0), 4326) WHERE id_finca = :id"),
                {"id": datos.finca.id_finca},
            )


def test_distancia_entre_fincas_con_postgis(session: Session) -> None:
    """La geometría sirve para el radio de las alertas (p. ej. 3,5 km)."""
    datos = escenario(session)
    km = session.execute(
        text(
            "SELECT ST_Distance(f.ubicacion::geography, c.ubicacion::geography) / 1000 "
            "FROM fincas f JOIN cultivos c ON c.id_finca = f.id_finca WHERE f.id_finca = :id"
        ),
        {"id": datos.finca.id_finca},
    ).scalar_one()
    assert 0 < km < 0.2
