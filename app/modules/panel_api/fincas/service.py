"""Reglas de fincas y cultivos (guía §11, módulo 3).

- Solo se registra una finca para un productor activo.
- Una finca tiene varios cultivos y un cultivo, una o más especies (mínimo una).
- Quitar una especie que algún caso usa se rechaza con `409 ESPECIE_EN_USO` y la lista de casos afectados: el caso
  apunta al par (cultivo, especie) y ese par debe seguir existiendo mientras el caso se conserve.
- Las coordenadas de la finca las siguen sus cultivos (la distancia de las alertas se mide con ellas).
"""

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError
from app.api.paginacion import ParametrosPagina
from app.core.catalogos_api import normalizar_codigo
from app.core.security_cognito import UsuarioActual
from app.modules.panel_api.auditoria import auditar
from app.modules.panel_api.cuentas.repository import bloquear
from app.modules.panel_api.fincas import repository as repo
from app.modules.panel_api.fincas.repository import CultivoFila, FiltrosFincas, FincaFila
from app.modules.panel_api.fincas.schemas import CultivoEditar, CultivoEnFincaEditar, CultivoIn, FincaEditar, FincaIn
from app.modules.panel_api.territorio_comun import nombre_de_vereda, veredas_registradas

_NOMBRE_CULTIVO_REPETIDO = {"nombre": "Esta finca ya tiene un cultivo con ese nombre"}


@dataclass
class PlanCultivo:
    cultivo: CultivoFila
    nombre: str | None = None
    tipo: str | None = None
    agregar: list[str] = field(default_factory=list)
    quitar: list[str] = field(default_factory=list)

    @property
    def sin_cambios(self) -> bool:
        return self.nombre is None and self.tipo is None and not self.agregar and not self.quitar


def validacion(campos: dict[str, str]) -> ApiError:
    return ApiError(422, "VALIDACION", "Revisa los campos marcados.", campos)


class FincasService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- Lectura -----------------------------------------------------------------------------------------------------

    async def listar(self, pagina: ParametrosPagina, filtros: FiltrosFincas) -> tuple[list[FincaFila], int]:
        return await repo.listar(self.session, pagina, filtros)

    async def obtener(self, id_finca: str) -> FincaFila:
        finca = await repo.obtener(self.session, id_finca)
        if finca is None:
            raise ApiError(404, "NO_ENCONTRADO", "La finca no existe.")
        return finca

    async def cultivos(self, id_finca: str) -> list[CultivoFila]:
        return (await self.obtener(id_finca)).cultivos

    async def obtener_cultivo(self, id_cultivo: str) -> CultivoFila:
        cultivo = await repo.obtener_cultivo(self.session, id_cultivo)
        if cultivo is None:
            raise ApiError(404, "NO_ENCONTRADO", "El cultivo no existe.")
        return cultivo

    async def _confirmar(self) -> None:
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            raise ApiError(409, "DUPLICADO", "Hay datos que ya están registrados.") from None

    async def _vereda_guardada(self, escrita: str) -> str:
        """Si la vereda ya existe con otra grafía (`SAN_LUIS` y `San Luis`), se usa la guardada: no se duplican."""
        return (await veredas_registradas(self.session)).get(normalizar_codigo(escrita)) or nombre_de_vereda(escrita)

    # --- Fincas ------------------------------------------------------------------------------------------------------

    async def crear(self, datos: FincaIn, actor: UsuarioActual) -> FincaFila:
        productor = await repo.productor(self.session, datos.productor_id)
        if productor is None:
            raise validacion({"productor_id": "Selecciona el productor."})
        if not productor[1]:
            raise validacion({"productor_id": "Solo se registran fincas de productores activos."})
        id_finca = await repo.crear(
            self.session,
            datos.nombre,
            await self._vereda_guardada(datos.vereda),
            datos.latitud,
            datos.longitud,
            datos.productor_id,
        )
        await auditar(
            self.session,
            actor,
            "CREAR_FINCA",
            "fincas",
            f"Finca {id_finca} registrada para el productor {datos.productor_id}",
        )
        await self._confirmar()
        return await self.obtener(id_finca)

    async def editar(self, id_finca: str, datos: FincaEditar, actor: UsuarioActual) -> FincaFila:
        await bloquear(self.session, f"finca:{id_finca}")
        actual = await self.obtener(id_finca)
        vereda = await self._vereda_guardada(datos.vereda) if datos.vereda is not None else None
        cambios = {
            "nombre": datos.nombre if datos.nombre not in (None, actual.nombre) else None,
            "vereda": vereda if vereda not in (None, actual.vereda) else None,
            "latitud": datos.latitud if datos.latitud not in (None, float(actual.latitud)) else None,
            "longitud": datos.longitud if datos.longitud not in (None, float(actual.longitud)) else None,
        }
        planes = self._planear_cultivos(actual, datos.cultivos or [])
        await self._rechazar_especies_en_uso(planes)
        planes = [p for p in planes if not p.sin_cambios]
        if all(v is None for v in cambios.values()) and not planes:
            return actual  # nada cambió: no se audita

        await repo.actualizar(
            self.session,
            id_finca,
            nombre=datos.nombre if cambios["nombre"] is not None else None,
            vereda=vereda if cambios["vereda"] is not None else None,
            latitud=datos.latitud if cambios["latitud"] is not None else None,
            longitud=datos.longitud if cambios["longitud"] is not None else None,
        )
        for plan in planes:
            await repo.actualizar_cultivo(
                self.session, plan.cultivo, nombre=None, tipo=plan.tipo, agregar=plan.agregar, quitar=plan.quitar
            )
        anterior: dict[str, Any] = {
            "nombre": cambios["nombre"] and actual.nombre,
            "vereda": cambios["vereda"] and actual.vereda,
            "latitud": cambios["latitud"] and float(actual.latitud),
            "longitud": cambios["longitud"] and float(actual.longitud),
        }
        nuevo: dict[str, Any] = {k: v for k, v in cambios.items() if v is not None}
        if planes:
            nuevo["cultivos"] = [p.cultivo.id for p in planes]
        await auditar(
            self.session,
            actor,
            "EDITAR_FINCA",
            "fincas",
            f"Finca {id_finca} editada",
            anterior={k: v for k, v in anterior.items() if v is not None},
            nuevo=nuevo,
        )
        await self._confirmar()
        return await self.obtener(id_finca)

    # --- Cultivos ----------------------------------------------------------------------------------------------------

    def _planear_cultivos(self, finca: FincaFila, ediciones: list[CultivoEnFincaEditar]) -> list[PlanCultivo]:
        por_id = {c.id: c for c in finca.cultivos}
        planes: list[PlanCultivo] = []
        for edicion in ediciones:
            cultivo = por_id.get(edicion.id)
            if cultivo is None:
                raise validacion({f"cultivos.{edicion.id}": "El cultivo no pertenece a esta finca."})
            planes.append(self._plan(cultivo, None, edicion.tipo, edicion.especies))
        return planes

    @staticmethod
    def _plan(cultivo: CultivoFila, nombre: str | None, tipo: str | None, especies: list[Any] | None) -> PlanCultivo:
        nuevas = [str(e.value if hasattr(e, "value") else e) for e in especies] if especies is not None else None
        tipo_nuevo = str(tipo.value if hasattr(tipo, "value") else tipo) if tipo is not None else None
        return PlanCultivo(
            cultivo=cultivo,
            nombre=nombre if nombre not in (None, cultivo.nombre) else None,
            tipo=tipo_nuevo if tipo_nuevo not in (None, cultivo.tipo) else None,
            agregar=[e for e in nuevas if e not in cultivo.especies] if nuevas is not None else [],
            quitar=[e for e in cultivo.especies if e not in nuevas] if nuevas is not None else [],
        )

    async def _rechazar_especies_en_uso(self, planes: list[PlanCultivo]) -> None:
        detalle: list[dict[str, Any]] = []
        for plan in planes:
            for especie, casos in (await repo.casos_por_especie(self.session, plan.cultivo.id, plan.quitar)).items():
                detalle.append({"cultivo": plan.cultivo.nombre, "especie": especie, "casos": casos})
        if detalle:
            raise ApiError(
                409,
                "ESPECIE_EN_USO",
                "No se puede quitar una especie que tienen casos de este cultivo.",
                detalle=detalle,
            )

    async def crear_cultivo(self, id_finca: str, datos: CultivoIn, actor: UsuarioActual) -> FincaFila:
        await bloquear(self.session, f"finca:{id_finca}")
        finca = await self.obtener(id_finca)
        if await repo.nombre_de_cultivo_en_uso(self.session, id_finca, datos.nombre):
            raise ApiError(409, "DUPLICADO", "Hay datos que ya están registrados.", _NOMBRE_CULTIVO_REPETIDO)
        id_cultivo = await repo.crear_cultivo(
            self.session, finca, datos.nombre, datos.tipo.value, [e.value for e in datos.especies]
        )
        await auditar(
            self.session, actor, "CREAR_CULTIVO", "cultivos", f"Cultivo {id_cultivo} agregado a la finca {id_finca}"
        )
        await self._confirmar()
        return await self.obtener(id_finca)

    async def editar_cultivo(self, id_cultivo: str, datos: CultivoEditar, actor: UsuarioActual) -> CultivoFila:
        cultivo = await self.obtener_cultivo(id_cultivo)
        await bloquear(self.session, f"finca:{cultivo.id_finca}")
        cultivo = await self.obtener_cultivo(
            id_cultivo
        )  # ya con el candado: nadie lo cambia entre la lectura y la escritura
        plan = self._plan(cultivo, datos.nombre, datos.tipo, datos.especies)
        if plan.sin_cambios:
            return cultivo
        if plan.nombre and await repo.nombre_de_cultivo_en_uso(self.session, cultivo.id_finca, plan.nombre, id_cultivo):
            raise ApiError(409, "DUPLICADO", "Hay datos que ya están registrados.", _NOMBRE_CULTIVO_REPETIDO)
        await self._rechazar_especies_en_uso([plan])
        await repo.actualizar_cultivo(
            self.session, cultivo, nombre=plan.nombre, tipo=plan.tipo, agregar=plan.agregar, quitar=plan.quitar
        )
        await auditar(
            self.session,
            actor,
            "EDITAR_CULTIVO",
            "cultivos",
            f"Cultivo {id_cultivo} editado",
            anterior={
                "nombre": plan.nombre and cultivo.nombre,
                "tipo": plan.tipo and cultivo.tipo,
                "especies": cultivo.especies,
            },
            nuevo={"nombre": plan.nombre, "tipo": plan.tipo, "agregar": plan.agregar, "quitar": plan.quitar},
        )
        await self._confirmar()
        return await self.obtener_cultivo(id_cultivo)
