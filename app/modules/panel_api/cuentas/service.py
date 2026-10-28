"""Reglas de negocio de las cuentas (guía §11, módulo 2).

Crear, editar o deshabilitar una cuenta toca dos sistemas, Cognito y la base, que no comparten transacción. El orden
garantiza que ninguno quede a medias: primero lo que se puede deshacer, después el `commit`; si el `commit` falla, se
deshace lo que ya se hizo en Cognito. Toda acción de cuenta se audita (D-09), sin contraseñas ni teléfonos completos.
"""

import json
import logging
from collections.abc import Callable
from typing import Any

from fastapi.concurrency import run_in_threadpool
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError
from app.api.paginacion import ParametrosPagina
from app.core.roles import Rol
from app.core.security_cognito import UsuarioActual
from app.core.telefonos import celular_a_e164, celular_de_e164
from app.infrastructure.cognito_admin_client import (
    CognitoAdmin,
    ContrasenaActualIncorrecta,
    ContrasenaNoValida,
    ErrorCognito,
    UsuarioYaExiste,
)
from app.infrastructure.db import procedimientos as sp
from app.modules.panel_api.cuentas import repository as repo
from app.modules.panel_api.cuentas.repository import CuentaFila
from app.modules.panel_api.cuentas.schemas import (
    ActualizarMe,
    CambiarContrasenaIn,
    CuentaEditar,
    CuentaIn,
    PrimerLiderIn,
)

logger = logging.getLogger(__name__)

_ORIGEN = {Rol.LIDER_TECNICO: "LIDER", Rol.TECNICO: "TECNICO"}
_MENSAJE_DUPLICADO = "Hay datos que ya están registrados."
_MENSAJE_SERVICIO = "No pudimos completar la operación con el servicio de identidad. Intenta de nuevo."
_CAMPO_DE_RESTRICCION = {
    "uq_tec_correo": "correo",
    "uq_lid_tec_correo": "correo",
    "uq_tec_celular": "celular",
    "uq_lid_tec_celular": "celular",
}


def duplicado(campos: dict[str, str]) -> ApiError:
    return ApiError(409, "DUPLICADO", _MENSAJE_DUPLICADO, campos)


class CuentasService:
    def __init__(self, session: AsyncSession, cognito: CognitoAdmin) -> None:
        self.session = session
        self.cognito = cognito

    # --- Cognito -----------------------------------------------------------------------------------------------------

    async def _cognito(
        self, funcion: Callable[..., Any], *args: Any, campo_clave: str = "password", **kwargs: Any
    ) -> Any:
        """Llama a Cognito en un hilo y traduce sus errores al formato de la API."""
        try:
            return await run_in_threadpool(funcion, *args, **kwargs)
        except UsuarioYaExiste:
            raise duplicado({"correo": "Este correo ya está registrado"}) from None
        except ContrasenaNoValida:
            raise ApiError(
                422,
                "VALIDACION",
                "Revisa los campos marcados.",
                {campo_clave: "Mínimo 8 caracteres, con letras y números."},
            ) from None
        except ContrasenaActualIncorrecta:
            raise ApiError(
                422, "VALIDACION", "Revisa los campos marcados.", {"actual": "La contraseña actual no es correcta"}
            ) from None
        except ErrorCognito as error:
            logger.error("Cognito rechazó la operación", extra={"detalle": str(error)})
            raise ApiError(503, "ERROR_INTERNO", _MENSAJE_SERVICIO) from None

    async def _deshacer(self, funcion: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        """Compensación en Cognito: nunca oculta el error original."""
        try:
            await run_in_threadpool(funcion, *args, **kwargs)
        except Exception:
            logger.exception("No se pudo deshacer un cambio en Cognito; revisar la cuenta a mano")

    # --- Auditoría ---------------------------------------------------------------------------------------------------

    async def _auditar(
        self,
        rol: Rol,
        id_actor: str,
        accion: str,
        entidad: str,
        descripcion: str,
        anterior: dict[str, Any] | None = None,
        nuevo: dict[str, Any] | None = None,
    ) -> None:
        await self.session.execute(
            sp.REGISTRAR_AUDITORIA,
            {
                "origen": _ORIGEN[rol],
                "id_actor": id_actor,
                "accion": accion,
                "entidad": entidad,
                "descripcion": descripcion,
                "valor_anterior": json.dumps(anterior, ensure_ascii=False) if anterior else None,
                "valor_nuevo": json.dumps(nuevo, ensure_ascii=False) if nuevo else None,
                "resultado": "EXITO",
                "fecha": None,
            },
        )

    # --- Unicidad ----------------------------------------------------------------------------------------------------

    async def _exigir_unicos(
        self, rol: Rol, correo: str | None, celular_e164: str | None, excluir_id: str | None
    ) -> None:
        campos: dict[str, str] = {}
        if correo is not None and await repo.correo_registrado(self.session, correo, excluir_id):
            campos["correo"] = "Este correo ya está registrado"
        if celular_e164 is not None and await repo.celular_registrado(self.session, rol, celular_e164, excluir_id):
            campos["celular"] = "Este número de celular ya está registrado"
        if campos:
            raise duplicado(campos)

    @staticmethod
    def _campos_duplicados(error: IntegrityError) -> dict[str, str]:
        restriccion = getattr(getattr(error.orig, "diag", None), "constraint_name", None)
        campo = _CAMPO_DE_RESTRICCION.get(restriccion or "")
        if campo == "correo":
            return {"correo": "Este correo ya está registrado"}
        if campo == "celular":
            return {"celular": "Este número de celular ya está registrado"}
        return {}

    async def _confirmar(self, deshacer: Callable[[], Any] | None = None) -> None:
        """`commit`; si falla, deshace lo hecho en Cognito y traduce las carreras de unicidad."""
        try:
            await self.session.commit()
        except IntegrityError as error:
            await self.session.rollback()
            if deshacer:
                await deshacer()
            campos = self._campos_duplicados(error)
            if campos:
                raise duplicado(campos) from None
            raise
        except Exception:
            await self.session.rollback()
            if deshacer:
                await deshacer()
            raise

    # --- Autenticación pública ---------------------------------------------------------------------------------------

    async def hay_lider(self) -> bool:
        return await repo.hay_lider(self.session)

    async def primer_lider(self, datos: PrimerLiderIn) -> str:
        """Alta del primer líder (RQF01). Solo mientras no exista ninguno; un candado evita que dos ganen a la vez."""
        await repo.bloquear(self.session, "primer_lider")
        if await repo.hay_lider(self.session):
            raise ApiError(409, "YA_EXISTE_LIDER", "El panel ya tiene un líder técnico.")
        celular = celular_a_e164(datos.celular)
        await self._exigir_unicos(Rol.LIDER_TECNICO, datos.correo, celular, None)
        sub = await self._cognito(self.cognito.crear_usuario, datos.correo, datos.nombre, celular, datos.password)

        async def deshacer() -> None:
            await self._deshacer(self.cognito.eliminar_usuario, datos.correo)

        try:
            id_cuenta = await repo.crear(self.session, Rol.LIDER_TECNICO, datos.nombre, datos.correo, celular, sub)
            await self._auditar(
                Rol.LIDER_TECNICO,
                id_cuenta,
                "ALTA_PRIMER_LIDER",
                "lideres_tecnicos",
                f"Alta del primer líder {id_cuenta}",
            )
        except Exception:
            await self.session.rollback()
            await deshacer()
            raise
        await self._confirmar(deshacer)
        return id_cuenta

    async def correo_registrado(self, correo: str) -> bool:
        return await repo.correo_registrado(self.session, correo)

    # --- Mi cuenta ---------------------------------------------------------------------------------------------------

    async def actualizar_mi_cuenta(self, usuario: UsuarioActual, datos: ActualizarMe) -> CuentaFila:
        cuenta = await self._obtener_o_404(usuario.id)
        return await self._aplicar_cambios(cuenta, datos.nombre, None, datos.celular, usuario, "EDITAR_MI_CUENTA")

    async def cambiar_contrasena(self, usuario: UsuarioActual, access_token: str, datos: CambiarContrasenaIn) -> None:
        await self._cognito(
            self.cognito.cambiar_contrasena, access_token, datos.actual, datos.nueva, campo_clave="nueva"
        )
        await self._auditar(
            usuario.rol, usuario.id, "CAMBIAR_CLAVE", _tabla(usuario.rol), f"Cambio de contraseña de {usuario.id}"
        )
        await self._confirmar()

    # --- Cuentas (solo el líder) -------------------------------------------------------------------------------------

    async def listar(
        self, pagina: ParametrosPagina, *, q: str | None, rol: Rol | None, activo: bool | None, carga: str | None
    ) -> tuple[list[CuentaFila], int]:
        return await repo.listar(self.session, pagina, q=q, rol=rol, activo=activo, carga=carga)

    async def tecnicos_con_carga(self) -> list[CuentaFila]:
        return await repo.tecnicos_con_carga(self.session)

    async def obtener(self, id_cuenta: str) -> CuentaFila:
        return await self._obtener_o_404(id_cuenta)

    async def _obtener_o_404(self, id_cuenta: str) -> CuentaFila:
        cuenta = await repo.obtener(self.session, id_cuenta)
        if cuenta is None:
            raise ApiError(404, "NO_ENCONTRADO", "La cuenta no existe.")
        return cuenta

    async def crear(self, datos: CuentaIn, actor: UsuarioActual) -> CuentaFila:
        """Alta de un técnico o de otro líder: Cognito envía la contraseña temporal por correo (RQF11)."""
        celular = celular_a_e164(datos.celular)
        await self._exigir_unicos(datos.rol, datos.correo, celular, None)
        sub = await self._cognito(self.cognito.crear_usuario, datos.correo, datos.nombre, celular)

        async def deshacer() -> None:
            await self._deshacer(self.cognito.eliminar_usuario, datos.correo)

        try:
            id_cuenta = await repo.crear(self.session, datos.rol, datos.nombre, datos.correo, celular, sub)
            await self._auditar(
                actor.rol,
                actor.id,
                "CREAR_CUENTA",
                _tabla(datos.rol),
                f"Cuenta {id_cuenta} creada con el rol {datos.rol.value}",
            )
        except IntegrityError as error:
            await self.session.rollback()
            await deshacer()
            raise duplicado(self._campos_duplicados(error) or {"correo": "Este correo ya está registrado"}) from None
        except Exception:
            await self.session.rollback()
            await deshacer()
            raise
        await self._confirmar(deshacer)
        return await self._obtener_o_404(id_cuenta)

    async def editar(self, id_cuenta: str, datos: CuentaEditar, actor: UsuarioActual) -> CuentaFila:
        cuenta = await self._obtener_o_404(id_cuenta)
        if cuenta.rol is Rol.LIDER_TECNICO:  # P-11: el líder se edita solo desde Mi cuenta
            raise ApiError(403, "SIN_PERMISO", "Los datos del líder técnico se editan desde Mi cuenta.")
        if datos.rol is not None and datos.rol is not cuenta.rol:
            raise ApiError(
                422, "VALIDACION", "Revisa los campos marcados.", {"rol": "No se puede cambiar el rol de una cuenta"}
            )
        return await self._aplicar_cambios(cuenta, datos.nombre, datos.correo, datos.celular, actor, "EDITAR_CUENTA")

    async def _aplicar_cambios(
        self,
        cuenta: CuentaFila,
        nombre: str | None,
        correo: str | None,
        celular: str | None,
        actor: UsuarioActual,
        accion: str,
    ) -> CuentaFila:
        celular_e164 = celular_a_e164(celular) if celular is not None else None
        nuevo_nombre = nombre if nombre is not None and nombre != cuenta.nombre else None
        nuevo_correo = correo if correo is not None and correo != cuenta.correo.lower() else None
        nuevo_celular = celular_e164 if celular_e164 is not None and celular_e164 != cuenta.celular_e164 else None
        if nuevo_nombre is None and nuevo_correo is None and nuevo_celular is None:
            return cuenta  # nada cambió: no se toca Cognito ni se audita

        await self._exigir_unicos(cuenta.rol, nuevo_correo, nuevo_celular, cuenta.id)
        await repo.actualizar(
            self.session, cuenta, nombre=nuevo_nombre, correo=nuevo_correo, celular_e164=nuevo_celular
        )
        anterior = {"nombre": nuevo_nombre and cuenta.nombre, "correo": nuevo_correo and cuenta.correo}
        nuevo = {"nombre": nuevo_nombre, "correo": nuevo_correo, "celular": nuevo_celular and "actualizado"}
        await self._auditar(
            actor.rol,
            actor.id,
            accion,
            _tabla(cuenta.rol),
            f"Datos de la cuenta {cuenta.id} actualizados",
            {k: v for k, v in anterior.items() if v},
            {k: v for k, v in nuevo.items() if v},
        )
        await self._cognito(
            self.cognito.actualizar_atributos,
            cuenta.correo,
            nombre=nuevo_nombre,
            celular_e164=nuevo_celular,
            correo_nuevo=nuevo_correo,
        )

        async def deshacer() -> None:
            await self._deshacer(
                self.cognito.actualizar_atributos,
                nuevo_correo or cuenta.correo,
                nombre=cuenta.nombre if nuevo_nombre else None,
                celular_e164=cuenta.celular_e164 if nuevo_celular else None,
                correo_nuevo=cuenta.correo if nuevo_correo else None,
            )

        await self._confirmar(deshacer)
        return await self._obtener_o_404(cuenta.id)

    async def cambiar_estado(self, id_cuenta: str, activo: bool, actor: UsuarioActual) -> CuentaFila:
        """Habilita o deshabilita (RQF12, RQF13). Un rol nunca se queda sin cuentas activas."""
        cuenta = await self._obtener_o_404(id_cuenta)
        if cuenta.activo == activo:
            return cuenta  # ya estaba así: repetirlo no hace nada
        if not activo:
            await repo.bloquear(self.session, f"ultima_cuenta:{cuenta.rol.value}")
            if await repo.contar_activas(self.session, cuenta.rol) <= 1:
                raise ApiError(409, "UNICA_CUENTA_ACTIVA", "Es la única cuenta activa de su rol.")

        operacion, inversa = (
            (self.cognito.habilitar, self.cognito.deshabilitar)
            if activo
            else (self.cognito.deshabilitar, self.cognito.habilitar)
        )
        await self._cognito(operacion, cuenta.correo)

        async def deshacer() -> None:
            await self._deshacer(inversa, cuenta.correo)

        try:
            await repo.cambiar_estado(self.session, cuenta, activo)
            await self._auditar(
                actor.rol,
                actor.id,
                "CAMBIAR_ESTADO",
                _tabla(cuenta.rol),
                f"Cuenta {cuenta.id} {'habilitada' if activo else 'deshabilitada'}",
                {"activo": cuenta.activo},
                {"activo": activo},
            )
        except Exception:
            await self.session.rollback()
            await deshacer()
            raise
        await self._confirmar(deshacer)
        return await self._obtener_o_404(cuenta.id)


def _tabla(rol: Rol) -> str:
    return "lideres_tecnicos" if rol is Rol.LIDER_TECNICO else "tecnicos"


def a_celular_api(cuenta: CuentaFila) -> str:
    return celular_de_e164(cuenta.celular_e164)
