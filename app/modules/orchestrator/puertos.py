"""Lo que el orquestador necesita de otros módulos, como interfaces. Las implementaciones reales están en el módulo 11
(`image_pipeline.pipeline` e `identification.servicio`).

- `PipelineImagenes`: descarga la foto de Meta, la valida (dominio y calidad), la convierte a WebP y la guarda en S3.
- `Identificador`: calcula la identificación preliminar del caso (RAG + reglas del 50 % y 15 puntos).

Mientras tanto hay versiones simuladas: aceptan todas las fotos y no identifican nada, de modo que un caso puede
recorrer el flujo completo y llegar al panel sin identificación preliminar (D-03 la permite nula).
"""

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.orm import Session


@dataclass(frozen=True)
class ResultadoImagen:
    util: bool
    tipo: str | None = None  # HOJA, ENVES, FRUTO o TALLO
    clave_s3: str | None = None
    motivo: str | None = None  # por qué se rechazó, en palabras para el productor


class PipelineImagenes(Protocol):
    def procesar(self, id_caso: str, media_id: str, descripcion: str | None) -> ResultadoImagen: ...

    def eliminar(self, clave_s3: str) -> None:
        """Borra una foto ya guardada que al final no se usa (por ejemplo, por pasar el límite de su tipo)."""
        ...


@dataclass(frozen=True)
class Identificacion:
    tipo_resultado: str  # PRELIMINAR, AMBIGUO o NO_IDENTIFICADO
    explicacion: str
    porcentajes: dict[str, float]  # código de categoría → porcentaje de coincidencia de síntomas
    detalle: dict[str, dict[str, list[str]]] | None = None


class Identificador(Protocol):
    def identificar(self, s: Session, id_caso: str) -> Identificacion | None:
        """Corre dentro de la transacción del mensaje. Devuelve None si no hay con qué (o la IA no respondió)."""
        ...


def tipo_de_evidencia(descripcion: str | None) -> str:
    """El tipo sale de lo que el productor escribió junto a la foto («fruto», «tallo», «envés»…); sin pista, HOJA."""
    texto = (descripcion or "").lower()
    return next(
        (
            t
            for clave, t in (("fruto", "FRUTO"), ("tallo", "TALLO"), ("envés", "ENVES"), ("enves", "ENVES"))
            if clave in texto
        ),
        "HOJA",
    )


class PipelineSimulado:
    """Acepta toda foto como útil. El tipo sale de la descripción («hoja», «fruto»…) y, si no, es HOJA. No usa S3."""

    def __init__(self) -> None:
        self.recibidas: list[tuple[str, str]] = []
        self.rechazar: dict[str, str] = {}  # media_id → motivo, para las pruebas
        self.eliminadas: list[str] = []

    def procesar(self, id_caso: str, media_id: str, descripcion: str | None) -> ResultadoImagen:
        self.recibidas.append((id_caso, media_id))
        if media_id in self.rechazar:
            return ResultadoImagen(util=False, motivo=self.rechazar[media_id])
        return ResultadoImagen(
            util=True, tipo=tipo_de_evidencia(descripcion), clave_s3=f"simulado/{id_caso}/{media_id}.webp"
        )

    def eliminar(self, clave_s3: str) -> None:
        self.eliminadas.append(clave_s3)


class IdentificadorSimulado:
    """No identifica nada: el caso llega al panel sin identificación preliminar."""

    def identificar(self, s: Session, id_caso: str) -> Identificacion | None:
        return None
