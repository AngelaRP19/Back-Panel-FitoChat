"""Identificadores legibles con prefijo (CAS-00000001, PRO-000001, CF001...).

Los genera la aplicación a partir de una secuencia de PostgreSQL por tabla y de la función SQL
fn_generar_id (creada en la migración inicial). PREFIJOS_ID es la única fuente de verdad de los
prefijos: la migración crea las secuencias desde aquí y el generador formatea con estos datos.

Formato: <prefijo><separador><número con ceros>, con un largo total igual al de la columna.
- Columnas de 10 y 12: prefijo de 3 letras + '-' (PRO-000001, CAS-00000001).
- Columnas de 5 (catálogos): prefijo de 2 letras sin separador (CF001), para dejar 3 dígitos.
"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection, event, func, inspect, select
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class IdSpec:
    prefijo: str
    largo: int
    separador: str = "-"

    @property
    def digitos(self) -> int:
        return self.largo - len(self.prefijo) - len(self.separador)

    @property
    def valor_maximo(self) -> int:
        return 10**self.digitos - 1

    def formatear(self, numero: int) -> str:
        if not 1 <= numero <= self.valor_maximo:
            raise ValueError(f"El número {numero} no cabe en {self.digitos} dígitos para {self.prefijo}")
        return f"{self.prefijo}{self.separador}{numero:0{self.digitos}d}"


PREFIJOS_ID: dict[str, IdSpec] = {
    # Catálogos (VARCHAR2(5))
    "categorias_fitosanitarias": IdSpec("CF", 5, ""),
    "estados": IdSpec("ES", 5, ""),
    "especies": IdSpec("EP", 5, ""),
    # VARCHAR2(10)
    "productores": IdSpec("PRO", 10),
    "fincas": IdSpec("FIN", 10),
    "cultivos": IdSpec("CUL", 10),
    "tecnicos": IdSpec("TEC", 10),
    "lideres_tecnicos": IdSpec("LID", 10),
    "preguntas": IdSpec("PRE", 10),
    "parametros_epidemiologicos": IdSpec("PAR", 10),
    "alertas": IdSpec("ALR", 10),
    # VARCHAR2(12)
    "identificacion_preliminar": IdSpec("IDP", 12),
    "casos": IdSpec("CAS", 12),
    "eventos": IdSpec("EVE", 12),
    "diagnosticos": IdSpec("DIA", 12),
    "respuestas": IdSpec("RES", 12),
    "fotografias": IdSpec("FOT", 12),
    "auditoria": IdSpec("AUD", 12),
    # Extensión de arquitectura
    "mensajes_whatsapp": IdSpec("MSG", 12),
    "conocimiento_embeddings": IdSpec("CON", 12),
}


def nombre_secuencia(tabla: str) -> str:
    return f"seq_{tabla}"


def id_expression(tabla: str) -> Any:
    """Expresión SQL fn_generar_id(...) para usar dentro de un INSERT (p. ej. en las semillas)."""
    spec = PREFIJOS_ID[tabla]
    return func.fn_generar_id(nombre_secuencia(tabla), spec.prefijo, spec.separador, spec.largo)


def siguiente_id(connection: Connection, tabla: str) -> str:
    """Reserva el siguiente id de la tabla (consume la secuencia)."""
    return str(connection.execute(select(id_expression(tabla))).scalar_one())


@event.listens_for(Session, "before_flush")
def _asignar_ids(session: Session, _flush_context: Any, _instances: Any) -> None:
    """Asigna el id a los objetos nuevos que no lo traen. Funciona también con AsyncSession."""
    for obj in session.new:
        mapper = inspect(obj).mapper
        tabla = mapper.local_table.name  # type: ignore[attr-defined]
        if tabla not in PREFIJOS_ID or len(mapper.primary_key) != 1:
            continue
        atributo = mapper.get_property_by_column(mapper.primary_key[0]).key
        if getattr(obj, atributo) is None:
            setattr(obj, atributo, siguiente_id(session.connection(), tabla))
