"""SQL de las cuentas del panel: líderes (`lideres_tecnicos`) y técnicos (`tecnicos`) juntos o por rol."""

import unicodedata
from dataclasses import dataclass
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.paginacion import ParametrosPagina
from app.core.passwords import unusable_password_hash
from app.core.roles import Rol
from app.infrastructure.db.models import LiderTecnico, Tecnico

ACTIVO = "ACTIVO"
DESHABILITADO = "DESHABILITADO"

# Nombres de tabla y columna propios (nunca vienen del usuario), para escribir el SQL una sola vez por rol.
_TABLAS: dict[Rol, dict[str, str]] = {
    Rol.TECNICO: {
        "tabla": "tecnicos",
        "id": "id_tecnico",
        "nombre": "nombre_tecnico",
        "correo": "correo_tecnico",
        "celular": "celular_tecnico",
        "estado": "estado_cuenta_tecnico",
        "sub": "cognito_sub",
    },
    Rol.LIDER_TECNICO: {
        "tabla": "lideres_tecnicos",
        "id": "id_lider_tecnico",
        "nombre": "nombre_lider_tecnico",
        "correo": "correo_lider_tecnico",
        "celular": "celular_lider_tecnico",
        "estado": "estado_cuenta_lider_tecnico",
        "sub": "cognito_sub",
    },
}
PREFIJO_ROL = {"TEC-": Rol.TECNICO, "LID-": Rol.LIDER_TECNICO}


@dataclass(frozen=True)
class CuentaFila:
    id: str
    nombre: str
    correo: str
    celular_e164: str
    rol: Rol
    activo: bool
    cognito_sub: str | None = None
    casos_activos: int = 0
    casos_pendientes: int = 0
    unica_activa_del_rol: bool = False


def rol_de_id(id_cuenta: str) -> Rol | None:
    return PREFIJO_ROL.get(id_cuenta[:4])


def normalizar_busqueda(texto: str) -> str:
    """Minúsculas y sin tildes, para buscar «perez» y encontrar «Pérez»."""
    sin_tildes = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in sin_tildes if unicodedata.category(c) != "Mn").strip()


async def bloquear(session: AsyncSession, clave: str) -> None:
    """Candado de transacción por clave: serializa «primer líder» y «última cuenta activa» entre solicitudes."""
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:clave))"), {"clave": clave})


async def hay_lider(session: AsyncSession) -> bool:
    return bool(await session.scalar(text("SELECT EXISTS (SELECT 1 FROM lideres_tecnicos)")))


async def correo_registrado(session: AsyncSession, correo: str, excluir_id: str | None = None) -> bool:
    """El correo es el usuario de Cognito: es único entre líderes y técnicos."""
    return bool(
        await session.scalar(
            text(
                "SELECT EXISTS ("
                " SELECT 1 FROM tecnicos WHERE lower(correo_tecnico) = :c AND id_tecnico IS DISTINCT FROM :x"
                " UNION ALL"
                " SELECT 1 FROM lideres_tecnicos WHERE lower(correo_lider_tecnico) = :c"
                " AND id_lider_tecnico IS DISTINCT FROM :x"
                ")"
            ),
            {"c": correo.lower(), "x": excluir_id},
        )
    )


async def celular_registrado(session: AsyncSession, rol: Rol, celular_e164: str, excluir_id: str | None = None) -> bool:
    """El celular es único dentro de la tabla del rol (el líder y su cuenta de técnico pueden compartir el suyo)."""
    t = _TABLAS[rol]
    return bool(
        await session.scalar(
            text(
                f"SELECT EXISTS (SELECT 1 FROM {t['tabla']} "
                f"WHERE {t['celular']} = :c AND {t['id']} IS DISTINCT FROM :x)"
            ),
            {"c": celular_e164, "x": excluir_id},
        )
    )


async def crear(session: AsyncSession, rol: Rol, nombre: str, correo: str, celular_e164: str, cognito_sub: str) -> str:
    """Inserta la cuenta (el id con prefijo lo asigna la sesión al hacer flush). La contraseña real vive en Cognito."""
    cuenta: Tecnico | LiderTecnico
    if rol is Rol.TECNICO:
        cuenta = Tecnico(
            nombre_tecnico=nombre,
            correo_tecnico=correo,
            celular_tecnico=celular_e164,
            contrasena_tecnico=unusable_password_hash(),
            estado_cuenta_tecnico=ACTIVO,
            fecha_registro_tecnico=date.today(),
            cognito_sub=cognito_sub,
        )
    else:
        cuenta = LiderTecnico(
            nombre_lider_tecnico=nombre,
            correo_lider_tecnico=correo,
            celular_lider_tecnico=celular_e164,
            contrasena_lider_tecnico=unusable_password_hash(),
            estado_cuenta_lider_tecnico=ACTIVO,
            cognito_sub=cognito_sub,
        )
    session.add(cuenta)
    await session.flush()
    return cuenta.id_tecnico if isinstance(cuenta, Tecnico) else cuenta.id_lider_tecnico


_CUENTAS_CTE = """
    cuentas AS (
        SELECT id_lider_tecnico AS id, nombre_lider_tecnico AS nombre, correo_lider_tecnico AS correo,
               celular_lider_tecnico AS celular, 'LIDER_TECNICO' AS rol, cognito_sub,
               estado_cuenta_lider_tecnico = 'ACTIVO' AS activo
        FROM lideres_tecnicos
        UNION ALL
        SELECT id_tecnico, nombre_tecnico, correo_tecnico, celular_tecnico, 'TECNICO', cognito_sub,
               estado_cuenta_tecnico = 'ACTIVO'
        FROM tecnicos
    ),
    carga AS (
        SELECT a.id_tecnico AS id,
               count(*) FILTER (WHERE e.nombre_estado IN ('En revisión', 'Registrado')) AS activos,
               count(*) FILTER (WHERE e.nombre_estado = 'En revisión') AS pendientes
        FROM asignaciones a
        JOIN casos c ON c.id_caso = a.id_caso
        JOIN estados e ON e.id_estado = c.id_estado
        WHERE a.fecha_fin IS NULL
        GROUP BY a.id_tecnico
    ),
    base AS (
        SELECT c.*, COALESCE(k.activos, 0) AS casos_activos, COALESCE(k.pendientes, 0) AS casos_pendientes,
               (c.activo AND count(*) FILTER (WHERE c.activo) OVER (PARTITION BY c.rol) = 1) AS unica_activa
        FROM cuentas c LEFT JOIN carga k ON k.id = c.id
    )
"""
_COLUMNAS = "id, nombre, correo, celular, rol, activo, cognito_sub, casos_activos, casos_pendientes, unica_activa"


def _fila(r: dict[str, object]) -> CuentaFila:
    return CuentaFila(
        id=str(r["id"]),
        nombre=str(r["nombre"]),
        correo=str(r["correo"]),
        celular_e164=str(r["celular"]),
        rol=Rol(str(r["rol"])),
        activo=bool(r["activo"]),
        cognito_sub=None if r["cognito_sub"] is None else str(r["cognito_sub"]),
        casos_activos=int(r["casos_activos"]),  # type: ignore[call-overload]
        casos_pendientes=int(r["casos_pendientes"]),  # type: ignore[call-overload]
        unica_activa_del_rol=bool(r["unica_activa"]),
    )


async def obtener(session: AsyncSession, id_cuenta: str) -> CuentaFila | None:
    if rol_de_id(id_cuenta) is None:
        return None
    resultado = await session.execute(
        text(f"WITH {_CUENTAS_CTE} SELECT {_COLUMNAS} FROM base WHERE id = :id"), {"id": id_cuenta}
    )
    fila = resultado.mappings().first()
    return _fila(dict(fila)) if fila else None


async def listar(
    session: AsyncSession,
    pagina: ParametrosPagina,
    *,
    q: str | None,
    rol: Rol | None,
    activo: bool | None,
    carga: str | None,
) -> tuple[list[CuentaFila], int]:
    """Líderes y técnicos juntos: los líderes primero y, dentro de cada rol, por nombre."""
    condiciones = ["TRUE"]
    parametros: dict[str, object] = {"limite": pagina.page_size, "desplazamiento": pagina.offset}
    if q and normalizar_busqueda(q):
        condiciones.append("translate(lower(nombre || ' ' || correo), 'áéíóúüñ', 'aeiouun') LIKE :q")
        parametros["q"] = f"%{normalizar_busqueda(q)}%"
    if rol is not None:
        condiciones.append("rol = :rol")
        parametros["rol"] = rol.value
    if activo is not None:
        condiciones.append("activo = :activo")
        parametros["activo"] = activo
    if carga == "pendientes":
        condiciones.append("casos_pendientes > 0")
    elif carga == "sin":
        condiciones.append("casos_activos = 0")
    consulta = (
        f"WITH {_CUENTAS_CTE} SELECT {_COLUMNAS}, count(*) OVER () AS total "
        f"FROM base WHERE {' AND '.join(condiciones)} "
        "ORDER BY (rol <> 'LIDER_TECNICO'), lower(nombre), id LIMIT :limite OFFSET :desplazamiento"
    )
    filas = (await session.execute(text(consulta), parametros)).mappings().all()
    total = int(filas[0]["total"]) if filas else 0
    if not filas and pagina.page > 1:  # página fuera de rango: el total igual debe ser verdadero
        sin_pagina = f"WITH {_CUENTAS_CTE} SELECT count(*) FROM base WHERE {' AND '.join(condiciones)}"
        total = int(await session.scalar(text(sin_pagina), parametros) or 0)
    return [_fila(dict(f)) for f in filas], total


async def tecnicos_con_carga(session: AsyncSession) -> list[CuentaFila]:
    """Cuentas de técnico ACTIVAS con su carga: las candidatas a recibir un caso (D-02: el líder no recibe casos)."""
    consulta = (
        f"WITH {_CUENTAS_CTE} SELECT {_COLUMNAS} FROM base WHERE rol = 'TECNICO' AND activo ORDER BY lower(nombre), id"
    )
    return [_fila(dict(f)) for f in (await session.execute(text(consulta))).mappings().all()]


async def contar_activas(session: AsyncSession, rol: Rol) -> int:
    t = _TABLAS[rol]
    return int(await session.scalar(text(f"SELECT count(*) FROM {t['tabla']} WHERE {t['estado']} = 'ACTIVO'")) or 0)


async def actualizar(
    session: AsyncSession, cuenta: CuentaFila, *, nombre: str | None, correo: str | None, celular_e164: str | None
) -> None:
    t = _TABLAS[cuenta.rol]
    cambios = {"nombre": nombre, "correo": correo, "celular": celular_e164}
    asignaciones = ", ".join(f"{t[campo]} = :{campo}" for campo, valor in cambios.items() if valor is not None)
    if not asignaciones:
        return
    parametros: dict[str, object] = {c: v for c, v in cambios.items() if v is not None}
    await session.execute(
        text(f"UPDATE {t['tabla']} SET {asignaciones} WHERE {t['id']} = :id"), {**parametros, "id": cuenta.id}
    )


async def cambiar_estado(session: AsyncSession, cuenta: CuentaFila, activo: bool) -> None:
    t = _TABLAS[cuenta.rol]
    await session.execute(
        text(f"UPDATE {t['tabla']} SET {t['estado']} = :estado WHERE {t['id']} = :id"),
        {"estado": ACTIVO if activo else DESHABILITADO, "id": cuenta.id},
    )


async def correo_existe(session: AsyncSession, correo: str) -> bool:
    return await correo_registrado(session, correo)
