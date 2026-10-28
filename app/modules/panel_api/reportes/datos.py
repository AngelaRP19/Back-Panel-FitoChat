"""Datos de los reportes R1, R2, R4 y R5 (B-08): una foto de casos, productores con sus fincas y cuentas.

Se lee todo una vez por solicitud (los volúmenes de ANUC Zetaquirá son pequeños: cientos de casos al año) y los
reportes se arman con funciones puras en `generales.py`, igual que el de alertas. Los códigos de la API (estados,
especies, categorías, veredas) se resuelven aquí: los reportes no conocen los nombres de la base.
"""

from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.catalogos_api import CATEGORIAS, ESPECIES, ESTADOS_CASO, normalizar_codigo
from app.core.codigos import codigo_caso


@dataclass(frozen=True)
class CasoR:
    id: str
    codigo: str  # «#0241»
    estado: str  # código de la API
    creado_en: datetime
    enviado_en: datetime | None
    cerrado_en: datetime | None
    vereda: str  # código
    vereda_nombre: str
    finca_id: str
    cultivo_id: str
    tipo_cultivo: str
    especie: str  # código
    productor_id: str
    productor_nombre: str
    tecnico_id: str | None
    tecnico_nombre: str | None
    asignado_en: datetime | None  # primera asignación
    preliminar: str | None  # PRELIMINAR, AMBIGUO, NO_IDENTIFICADO o None (sin análisis)
    categoria_preliminar: str | None  # código de la categoría con más coincidencia
    porcentaje: float | None
    resultado: str | None  # resultado técnico o None (sin diagnóstico)
    diagnosticado_en: datetime | None

    @property
    def fecha(self) -> datetime:
        """Fecha de referencia del caso: envío al panel o, si aún no se envía, su creación."""
        return self.enviado_en or self.creado_en


@dataclass(frozen=True)
class FincaR:
    id: str
    nombre: str
    vereda: str  # código
    vereda_nombre: str
    productor_id: str
    cultivos: list[tuple[str, list[str]]] = field(default_factory=list)  # (tipo, especies)


@dataclass(frozen=True)
class ProductorR:
    id: str
    nombre: str
    activo: bool
    registrado: date
    ultima_consulta: datetime | None
    fincas: list[FincaR] = field(default_factory=list)


@dataclass(frozen=True)
class CuentaR:
    id: str
    nombre: str
    rol: str  # LIDER_TECNICO o TECNICO
    activo: bool


@dataclass(frozen=True)
class Datos:
    casos: list[CasoR]
    productores: list[ProductorR]
    cuentas: list[CuentaR]


async def cargar(session: AsyncSession) -> Datos:
    casos = [
        CasoR(
            id=r["id_caso"],
            codigo=codigo_caso(r["id_caso"]),
            estado=ESTADOS_CASO.a_codigo(r["nombre_estado"]),
            creado_en=r["fecha_creacion_caso"],
            enviado_en=r["fecha_envio_panel"],
            cerrado_en=r["fecha_cierre"],
            vereda=normalizar_codigo(r["vereda_sector_finca"]),
            vereda_nombre=r["vereda_sector_finca"],
            finca_id=r["id_finca"],
            cultivo_id=r["id_cultivo"],
            tipo_cultivo=r["tipo_cultivo"],
            especie=ESPECIES.a_codigo(r["nombre_especie"]),
            productor_id=r["id_productor"],
            productor_nombre=r["nombre_productor"],
            tecnico_id=r["id_tecnico"],
            tecnico_nombre=r["nombre_tecnico"],
            asignado_en=r["primera_asignacion"],
            preliminar=r["resultado_analisis"],
            categoria_preliminar=CATEGORIAS.a_codigo(r["nombre_categoria_principal"])
            if r["nombre_categoria_principal"]
            else None,
            porcentaje=None if r["porcentaje_principal"] is None else float(r["porcentaje_principal"]),
            resultado=r["resultado_tecnico"],
            diagnosticado_en=r["fecha_diagnostico"],
        )
        for r in (
            await session.execute(
                text(
                    """
                    SELECT v.*, (SELECT min(a.fecha_asignacion) FROM asignaciones a WHERE a.id_caso = v.id_caso)
                           AS primera_asignacion
                    FROM vw_casos_detalle v
                    """
                )
            )
        ).mappings()
    ]

    cultivos: dict[str, list[tuple[str, list[str]]]] = {}
    for r in (
        await session.execute(
            text(
                """
                SELECT cu.id_finca, cu.tipo_cultivo, array_agg(e.nombre_especie ORDER BY e.nombre_especie) AS especies
                FROM cultivos cu
                LEFT JOIN cultivo_especie ce ON ce.id_cultivo = cu.id_cultivo
                LEFT JOIN especies e ON e.id_especie = ce.id_especie
                GROUP BY cu.id_cultivo, cu.id_finca, cu.tipo_cultivo
                """
            )
        )
    ).mappings():
        especies = [ESPECIES.a_codigo(e) for e in r["especies"] if e]
        cultivos.setdefault(r["id_finca"], []).append((r["tipo_cultivo"], especies))

    fincas: dict[str, list[FincaR]] = {}
    for r in (
        await session.execute(
            text("SELECT id_finca, nombre_finca, vereda_sector_finca, id_productor FROM fincas ORDER BY id_finca")
        )
    ).mappings():
        fincas.setdefault(r["id_productor"], []).append(
            FincaR(
                id=r["id_finca"],
                nombre=r["nombre_finca"],
                vereda=normalizar_codigo(r["vereda_sector_finca"]),
                vereda_nombre=r["vereda_sector_finca"],
                productor_id=r["id_productor"],
                cultivos=cultivos.get(r["id_finca"], []),
            )
        )

    productores = [
        ProductorR(
            id=r["id_productor"],
            nombre=r["nombre_productor"],
            activo=r["estado_cuenta_productor"] == "ACTIVO",
            registrado=r["fecha_registro_productor"],
            ultima_consulta=r["ultima_consulta"],
            fincas=fincas.get(r["id_productor"], []),
        )
        for r in (
            await session.execute(
                text(
                    """
                    SELECT p.id_productor, p.nombre_productor, p.estado_cuenta_productor, p.fecha_registro_productor,
                           GREATEST(
                               (SELECT max(m.fecha_recepcion) FROM mensajes_whatsapp m
                                WHERE m.id_productor = p.id_productor),
                               (SELECT max(m.fecha_recepcion) FROM mensajes_whatsapp m
                                WHERE m.telefono = p.telefono_productor)
                           ) AS ultima_consulta
                    FROM productores p
                    ORDER BY p.nombre_productor
                    """
                )
            )
        ).mappings()
    ]

    cuentas = [
        CuentaR(id=r["id"], nombre=r["nombre"], rol=r["rol"], activo=r["activo"])
        for r in (
            await session.execute(
                text(
                    """
                    SELECT id_lider_tecnico AS id, nombre_lider_tecnico AS nombre, 'LIDER_TECNICO' AS rol,
                           estado_cuenta_lider_tecnico = 'ACTIVO' AS activo
                    FROM lideres_tecnicos
                    UNION ALL
                    SELECT id_tecnico, nombre_tecnico, 'TECNICO', estado_cuenta_tecnico = 'ACTIVO' FROM tecnicos
                    ORDER BY rol, nombre
                    """
                )
            )
        ).mappings()
    ]
    return Datos(casos=casos, productores=productores, cuentas=cuentas)
