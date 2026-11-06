import re

import pytest
from sqlalchemy import Connection, String
from sqlalchemy.orm import Session

from app.infrastructure.db.base import Base
from app.infrastructure.db.ids import PREFIJOS_ID, IdSpec, siguiente_id
from app.infrastructure.db.models import Especie, Productor

# --- sin base de datos -----------------------------------------------------------------------


def test_cada_tabla_con_pk_simple_tiene_prefijo() -> None:
    con_pk_simple = {t.name for t in Base.metadata.tables.values() if len(t.primary_key.columns) == 1}
    assert con_pk_simple == set(PREFIJOS_ID)


@pytest.mark.parametrize("tabla", sorted(PREFIJOS_ID))
def test_el_id_ocupa_exactamente_el_largo_de_la_columna(tabla: str) -> None:
    spec = PREFIJOS_ID[tabla]
    [pk] = Base.metadata.tables[tabla].primary_key.columns
    assert isinstance(pk.type, String)
    assert spec.largo == pk.type.length
    assert len(spec.formatear(1)) == spec.largo
    assert len(spec.formatear(spec.valor_maximo)) == spec.largo


def test_prefijos_unicos() -> None:
    prefijos = [s.prefijo for s in PREFIJOS_ID.values()]
    assert len(prefijos) == len(set(prefijos))


def test_formatos() -> None:
    assert PREFIJOS_ID["casos"].formatear(1) == "CAS-00000001"
    assert PREFIJOS_ID["productores"].formatear(1) == "PRO-000001"
    assert PREFIJOS_ID["categorias_fitosanitarias"].formatear(7) == "CF007"


def test_desborde() -> None:
    with pytest.raises(ValueError):
        IdSpec("CF", 5, "").formatear(1000)


# --- con base de datos -----------------------------------------------------------------------


def test_siguiente_id_usa_la_secuencia(conn: Connection) -> None:
    primero = siguiente_id(conn, "casos")
    segundo = siguiente_id(conn, "casos")
    assert re.fullmatch(r"CAS-\d{8}", primero)
    assert int(segundo[4:]) == int(primero[4:]) + 1


def test_la_sesion_asigna_ids_al_hacer_flush(session: Session) -> None:
    especie = Especie(nombre_especie="Con id automático")
    session.add(especie)
    session.flush()
    assert re.fullmatch(r"EP\d{3}", especie.id_especie)


def test_respeta_un_id_explicito(session: Session) -> None:
    from datetime import date

    productor = Productor(
        id_productor="PRO-999999",
        nombre_productor="Con id propio",
        telefono_productor="573009998877",
        estado_cuenta_productor="ACTIVO",
        fecha_registro_productor=date(2026, 10, 1),
    )
    session.add(productor)
    session.flush()
    assert productor.id_productor == "PRO-999999"
