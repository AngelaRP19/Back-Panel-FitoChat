"""Reglas de los productores (guía §11, módulo 3).

Desactivar un productor no borra sus casos: solo deja de poder iniciar casos y recibir mensajes. Toda acción se
audita sin el celular completo (D-09).
"""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError
from app.api.paginacion import ParametrosPagina
from app.core.security_cognito import UsuarioActual
from app.core.telefonos import celular_a_e164
from app.modules.panel_api.auditoria import auditar
from app.modules.panel_api.productores import repository as repo
from app.modules.panel_api.productores.repository import FiltrosProductores, ProductorFila
from app.modules.panel_api.productores.schemas import ProductorEditar, ProductorIn

_CELULAR_DUPLICADO = {"celular": "Este número de celular ya está registrado"}


def duplicado() -> ApiError:
    return ApiError(409, "DUPLICADO", "Hay datos que ya están registrados.", _CELULAR_DUPLICADO)


class ProductoresService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def listar(self, pagina: ParametrosPagina, filtros: FiltrosProductores) -> tuple[list[ProductorFila], int]:
        return await repo.listar(self.session, pagina, filtros)

    async def obtener(self, id_productor: str) -> ProductorFila:
        productor = await repo.obtener(self.session, id_productor)
        if productor is None:
            raise ApiError(404, "NO_ENCONTRADO", "El productor no existe.")
        return productor

    async def _confirmar(self) -> None:
        try:
            await self.session.commit()
        except IntegrityError:  # otra solicitud registró el mismo celular entre la revisión y el commit
            await self.session.rollback()
            raise duplicado() from None

    async def crear(self, datos: ProductorIn, actor: UsuarioActual) -> ProductorFila:
        celular = celular_a_e164(datos.celular)
        if await repo.celular_registrado(self.session, celular):
            raise duplicado()
        try:
            id_productor = await repo.crear(self.session, datos.nombre, celular, datos.activo)
        except IntegrityError:
            await self.session.rollback()
            raise duplicado() from None
        await auditar(self.session, actor, "CREAR_PRODUCTOR", "productores", f"Productor {id_productor} registrado")
        await self._confirmar()
        return await self.obtener(id_productor)

    async def editar(self, id_productor: str, datos: ProductorEditar, actor: UsuarioActual) -> ProductorFila:
        actual = await self.obtener(id_productor)
        celular = celular_a_e164(datos.celular) if datos.celular is not None else None
        nombre = datos.nombre if datos.nombre is not None and datos.nombre != actual.nombre else None
        nuevo_celular = celular if celular is not None and celular != actual.celular_e164 else None
        if nombre is None and nuevo_celular is None:
            return actual  # nada cambió: no se audita
        if nuevo_celular is not None and await repo.celular_registrado(self.session, nuevo_celular, id_productor):
            raise duplicado()
        await repo.actualizar(self.session, id_productor, nombre, nuevo_celular)
        await auditar(
            self.session,
            actor,
            "EDITAR_PRODUCTOR",
            "productores",
            f"Productor {id_productor} editado",
            anterior={"nombre": nombre and actual.nombre, "celular": nuevo_celular and "anterior"},
            nuevo={"nombre": nombre, "celular": nuevo_celular and "actualizado"},
        )
        await self._confirmar()
        return await self.obtener(id_productor)

    async def cambiar_estado(self, id_productor: str, activo: bool, actor: UsuarioActual) -> ProductorFila:
        actual = await self.obtener(id_productor)
        if actual.activo == activo:
            return actual  # idempotente: no se audita
        await repo.cambiar_estado(self.session, id_productor, activo)
        await auditar(
            self.session,
            actor,
            "ESTADO_PRODUCTOR",
            "productores",
            f"Productor {id_productor} {'habilitado' if activo else 'deshabilitado'}",
            anterior={"activo": actual.activo},
            nuevo={"activo": activo},
        )
        await self._confirmar()
        return await self.obtener(id_productor)
