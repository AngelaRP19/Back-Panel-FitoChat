"""Códigos estables de la API y su relación con lo que se guarda en la base (guía §6.2).

La API expone códigos (`PENDIENTE_REVISION`, `TRIPS`...) que coinciden con `types/enums.ts` del frontend. Los
catálogos con nombre propio (estados, categorías y especies) guardan en la base el nombre legible
(«Pendiente de revisión»); el resto guarda el código tal cual.

- Entrada: un código desconocido se rechaza con `CodigoDesconocido` (la capa HTTP lo vuelve 422).
- Salida: un nombre desconocido no rompe la respuesta; se normaliza (`Plaga` -> `PLAGA`).

Nunca se escribe en el código el id de un catálogo (`ES003`, `CF004`): se resuelve por nombre.
"""

import re
import unicodedata
from collections.abc import Mapping
from enum import StrEnum
from typing import Generic, TypeVar


class CodigoDesconocido(ValueError):
    """El código recibido no pertenece al catálogo."""

    def __init__(self, catalogo: str, codigo: str) -> None:
        super().__init__(f"Código desconocido en {catalogo}: {codigo!r}")
        self.catalogo = catalogo
        self.codigo = codigo


def normalizar_codigo(texto: str) -> str:
    """'San Luis' -> 'SAN_LUIS': sin tildes, en mayúsculas y con guion bajo (también para las veredas)."""
    sin_tildes = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^A-Z0-9]+", "_", sin_tildes.upper()).strip("_")


class EstadoCaso(StrEnum):
    CREADO = "CREADO"
    EN_ANALISIS = "EN_ANALISIS"
    PENDIENTE_REVISION = "PENDIENTE_REVISION"
    EN_REVISION = "EN_REVISION"
    REGISTRADO = "REGISTRADO"
    CERRADO = "CERRADO"
    DESCARTADO = "DESCARTADO"


class CategoriaFito(StrEnum):
    BOTRYTIS = "BOTRYTIS"
    RONA = "RONA"
    DEFICIENCIA_MAGNESIO = "DEFICIENCIA_MAGNESIO"
    TRIPS = "TRIPS"
    FUSARIUM = "FUSARIUM"


class EspecieCultivo(StrEnum):
    GULUPA = "GULUPA"
    GRANADILLA = "GRANADILLA"
    MARACUYA = "MARACUYA"


class EstadoCuenta(StrEnum):
    ACTIVO = "ACTIVO"
    DESHABILITADO = "DESHABILITADO"


class ResultadoAnalisis(StrEnum):
    PRELIMINAR = "PRELIMINAR"
    AMBIGUO = "AMBIGUO"
    NO_IDENTIFICADO = "NO_IDENTIFICADO"


class ResultadoTecnico(StrEnum):
    CONFIRMADO = "CONFIRMADO"
    OTRA_PATOLOGIA = "OTRA_PATOLOGIA"
    NO_IDENTIFICADO = "NO_IDENTIFICADO"
    REQUIERE_VISITA = "REQUIERE_VISITA"


class EstadoAlerta(StrEnum):
    GENERADA = "GENERADA"
    EN_REVISION = "EN_REVISION"
    CONFIRMADA = "CONFIRMADA"
    COMUNICADA = "COMUNICADA"
    DESCARTADA = "DESCARTADA"
    CORREGIDA = "CORREGIDA"


class TipoEnvio(StrEnum):
    ALERTA = "ALERTA"
    CORRECCION = "CORRECCION"


class TipoEvidencia(StrEnum):
    HOJA = "HOJA"
    ENVES = "ENVES"
    FRUTO = "FRUTO"
    TALLO = "TALLO"


class TipoEvento(StrEnum):
    CASO_CREADO = "CASO_CREADO"
    ANALISIS_CHATBOT = "ANALISIS_CHATBOT"
    ENVIADO_A_REVISION = "ENVIADO_A_REVISION"
    TECNICO_ASIGNADO = "TECNICO_ASIGNADO"
    TECNICO_REASIGNADO = "TECNICO_REASIGNADO"
    DIAGNOSTICO_REGISTRADO = "DIAGNOSTICO_REGISTRADO"
    CASO_CERRADO = "CASO_CERRADO"
    CASO_DESCARTADO = "CASO_DESCARTADO"


class TipoCultivo(StrEnum):
    INVERNADERO = "INVERNADERO"
    CAMPO_ABIERTO = "CAMPO_ABIERTO"


E = TypeVar("E", bound=StrEnum)


class Catalogo(Generic[E]):  # noqa: UP046  (TypeVar clásico: mypy estricto lo resuelve igual y es más simple)
    """Un catálogo: códigos de la API y, si lo hay, el nombre que se guarda en la base."""

    def __init__(self, nombre: str, enum: type[E], nombres_bd: Mapping[E, str] | None = None) -> None:
        if nombres_bd is not None and set(nombres_bd) != set(enum):
            raise ValueError(f"El catálogo {nombre} debe dar un nombre para cada código")
        self.nombre = nombre
        self.enum = enum
        self._a_nombre: dict[E, str] = dict(nombres_bd) if nombres_bd else {m: m.value for m in enum}
        self._a_codigo: dict[str, E] = {v: k for k, v in self._a_nombre.items()}
        if len(self._a_codigo) != len(self._a_nombre):
            raise ValueError(f"El catálogo {nombre} repite nombres")

    @property
    def codigos(self) -> list[str]:
        return [m.value for m in self.enum]

    @property
    def nombres(self) -> list[str]:
        """Nombres tal como se guardan en la base, en el orden del enum."""
        return [self._a_nombre[m] for m in self.enum]

    def parsear(self, codigo: str) -> E:
        """Entrada de la API: devuelve el miembro o rechaza el código."""
        try:
            return self.enum(codigo)
        except ValueError:
            raise CodigoDesconocido(self.nombre, codigo) from None

    def a_nombre(self, codigo: str | E) -> str:
        """Código de la API -> valor guardado en la base."""
        return self._a_nombre[self.parsear(str(codigo))]

    def a_codigo(self, nombre_bd: str) -> str:
        """Valor guardado en la base -> código de la API. Un valor desconocido se normaliza en vez de fallar."""
        miembro = self._a_codigo.get(nombre_bd)
        return miembro.value if miembro is not None else normalizar_codigo(nombre_bd)

    def conoce_nombre(self, nombre_bd: str) -> bool:
        return nombre_bd in self._a_codigo


ESTADOS_CASO = Catalogo(
    "estado del caso",
    EstadoCaso,
    {
        EstadoCaso.CREADO: "Caso creado",
        EstadoCaso.EN_ANALISIS: "En análisis",
        EstadoCaso.PENDIENTE_REVISION: "Pendiente de revisión",
        EstadoCaso.EN_REVISION: "En revisión",
        EstadoCaso.REGISTRADO: "Registrado",
        EstadoCaso.CERRADO: "Cerrado",
        EstadoCaso.DESCARTADO: "Descartado",
    },
)
CATEGORIAS = Catalogo(
    "categoría",
    CategoriaFito,
    {
        CategoriaFito.BOTRYTIS: "Botrytis",
        CategoriaFito.RONA: "Roña",
        CategoriaFito.DEFICIENCIA_MAGNESIO: "Deficiencia de magnesio",
        CategoriaFito.TRIPS: "Trips",
        CategoriaFito.FUSARIUM: "Fusarium",
    },
)
ESPECIES = Catalogo(
    "especie",
    EspecieCultivo,
    {
        EspecieCultivo.GULUPA: "Gulupa",
        EspecieCultivo.GRANADILLA: "Granadilla",
        EspecieCultivo.MARACUYA: "Maracuyá",
    },
)
ESTADOS_CUENTA = Catalogo("estado de la cuenta", EstadoCuenta)
RESULTADOS_ANALISIS = Catalogo("resultado del análisis", ResultadoAnalisis)
RESULTADOS_TECNICOS = Catalogo("resultado técnico", ResultadoTecnico)
ESTADOS_ALERTA = Catalogo("estado de la alerta", EstadoAlerta)
TIPOS_ENVIO = Catalogo("tipo de envío", TipoEnvio)
TIPOS_EVIDENCIA = Catalogo("tipo de evidencia", TipoEvidencia)
TIPOS_EVENTO = Catalogo("tipo de evento", TipoEvento)
TIPOS_CULTIVO = Catalogo("tipo de cultivo", TipoCultivo)
