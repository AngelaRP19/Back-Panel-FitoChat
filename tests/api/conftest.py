"""Fixtures de las pruebas de la API de cuentas: Postgres real, Cognito simulado y datos CONFIRMADOS.

Estas pruebas confirman datos de verdad (la API hace `commit`), y la auditoría es inmutable: no se puede borrar fila por
fila. Por eso se vacían las tablas con TRUNCATE antes y después de cada prueba (solo en la base de pruebas).
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.core.roles import Rol
from app.core.telefonos import celular_a_e164
from app.infrastructure.cognito_admin_client import CognitoSimulado, get_cognito_admin
from app.infrastructure.consultas_store import _EN_MEMORIA as CONSULTAS_EN_MEMORIA
from app.infrastructure.db.models import LiderTecnico, Tecnico
from app.infrastructure.db.session import get_session
from app.infrastructure.documentos_store import _ARCHIVOS_EN_MEMORIA as ARCHIVOS_EN_MEMORIA
from app.infrastructure.documentos_store import _EN_MEMORIA as DOCUMENTOS_EN_MEMORIA
from app.infrastructure.notificaciones_store import _EN_MEMORIA as NOTIFICACIONES_EN_MEMORIA
from app.main import app
from app.modules.panel_api.cuentas.router_auth import LIMITADOR_VERIFICAR_CORREO
from tests.conftest import _sesion_por_solicitud, con_token
from tests.db_fixtures import reiniciar_secuencias_cortas

TABLAS_DE_CUENTAS = (
    "estados, categorias_fitosanitarias, especies, productores, tecnicos, lideres_tecnicos, auditoria, "
    "preguntas, identificacion_preliminar"
)


def vaciar(motor: Engine) -> None:
    """Vacía las tablas y reinicia las secuencias de catálogo (3 dígitos): cada prueba vuelve a sembrarlas y, como
    TRUNCATE no las reinicia, tras unos 140 siembras `seq_estados` (ES001..ES999) se agotaría."""
    with motor.begin() as conexion:
        conexion.execute(text(f"TRUNCATE {TABLAS_DE_CUENTAS} CASCADE"))
        reiniciar_secuencias_cortas(conexion)


@dataclass
class Cuenta:
    id: str
    rol: Rol
    correo: str
    celular: str  # 10 dígitos, como en la API
    sub: str
    nombre: str

    @property
    def headers(self) -> dict[str, str]:
        return con_token(self.sub)


@pytest.fixture
def cognito() -> CognitoSimulado:
    return CognitoSimulado()


@pytest.fixture
def api(migrated: Engine, cognito: CognitoSimulado) -> Iterator[TestClient]:
    vaciar(migrated)
    NOTIFICACIONES_EN_MEMORIA.vaciar()  # las cuentas se recrean con los mismos ids en cada prueba
    CONSULTAS_EN_MEMORIA.vaciar()
    DOCUMENTOS_EN_MEMORIA.vaciar()
    ARCHIVOS_EN_MEMORIA.vaciar()
    app.dependency_overrides[get_session] = _sesion_por_solicitud
    app.dependency_overrides[get_cognito_admin] = lambda: cognito
    LIMITADOR_VERIFICAR_CORREO.reiniciar()
    with TestClient(app, raise_server_exceptions=False) as cliente:
        yield cliente
    app.dependency_overrides.clear()
    vaciar(migrated)


NuevaCuenta = Callable[..., Cuenta]


@pytest.fixture
def nueva_cuenta(migrated: Engine, cognito: CognitoSimulado) -> NuevaCuenta:
    """Crea una cuenta en la base Y en el Cognito simulado, enlazadas por el mismo `sub`."""

    def crear(
        rol: Rol,
        n: int = 1,
        *,
        activo: bool = True,
        nombre: str | None = None,
        celular: str | None = None,
        correo: str | None = None,
    ) -> Cuenta:
        etiqueta = "lider" if rol is Rol.LIDER_TECNICO else "tecnico"
        nombre = nombre or f"{etiqueta.title()} {n}"
        correo = correo or f"{etiqueta}{n}@anuc.org"
        celular = celular or f"31{n:08d}"
        sub = cognito.crear_usuario(correo, nombre, celular_a_e164(celular))
        estado = "ACTIVO" if activo else "DESHABILITADO"
        with Session(migrated) as sesion:
            fila: Tecnico | LiderTecnico
            if rol is Rol.TECNICO:
                fila = Tecnico(
                    nombre_tecnico=nombre,
                    correo_tecnico=correo,
                    celular_tecnico=celular_a_e164(celular),
                    contrasena_tecnico="hash-inutilizable",
                    estado_cuenta_tecnico=estado,
                    fecha_registro_tecnico=date(2026, 10, 1),
                    cognito_sub=sub,
                )
            else:
                fila = LiderTecnico(
                    nombre_lider_tecnico=nombre,
                    correo_lider_tecnico=correo,
                    celular_lider_tecnico=celular_a_e164(celular),
                    contrasena_lider_tecnico="hash-inutilizable",
                    estado_cuenta_lider_tecnico=estado,
                    cognito_sub=sub,
                )
            sesion.add(fila)
            sesion.commit()
            id_cuenta = fila.id_tecnico if isinstance(fila, Tecnico) else fila.id_lider_tecnico
        if not activo:
            cognito.deshabilitar(correo)
        return Cuenta(id_cuenta, rol, correo, celular, sub, nombre)

    return crear


def consultar(motor: Engine, sql: str, **parametros: Any) -> list[dict[str, Any]]:
    with motor.connect() as conexion:
        return [dict(f) for f in conexion.execute(text(sql), parametros).mappings()]


def auditoria_de(motor: Engine) -> list[dict[str, Any]]:
    return consultar(motor, "SELECT * FROM auditoria ORDER BY id_auditoria")


class CognitoQueFalla(CognitoSimulado):
    """Cognito simulado que lanza un error de servicio en las operaciones indicadas."""

    def __init__(self, *fallan: str) -> None:
        super().__init__()
        self._fallan = set(fallan)

    def _revisar(self, operacion: str) -> None:
        from app.infrastructure.cognito_admin_client import ErrorCognito

        if operacion in self._fallan:
            raise ErrorCognito("ThrottlingException")

    def crear_usuario(self, correo: str, nombre: str, celular_e164: str, contrasena: str | None = None) -> str:
        self._revisar("crear")
        return super().crear_usuario(correo, nombre, celular_e164, contrasena)

    def deshabilitar(self, correo: str) -> None:
        self._revisar("deshabilitar")
        super().deshabilitar(correo)

    def habilitar(self, correo: str) -> None:
        self._revisar("habilitar")
        super().habilitar(correo)

    def actualizar_atributos(self, correo: str, **kwargs: Any) -> None:
        self._revisar("actualizar")
        super().actualizar_atributos(correo, **kwargs)

    def cambiar_contrasena(self, access_token: str, actual: str, nueva: str) -> None:
        self._revisar("cambiar_contrasena")
        super().cambiar_contrasena(access_token, actual, nueva)


@pytest.fixture
def catalogos(api: TestClient, migrated: Engine) -> None:
    """Estados, categorías y especies reales (la API empieza cada prueba con las tablas vacías)."""
    from app.infrastructure.db.seed import seed

    with migrated.begin() as conexion:
        seed(conexion)


@dataclass
class Mundo:
    """Un líder, dos técnicos y dos productores con una finca y un cultivo cada uno, sobre catálogos reales."""

    lider: Cuenta
    t1: Cuenta
    t2: Cuenta
    productor1: dict[str, Any]
    productor2: dict[str, Any]
    finca1: dict[str, Any]
    finca2: dict[str, Any]
    cultivo1: str  # La Esperanza (Centro): gulupa y maracuyá
    cultivo2: str  # El Roble (San Luis): maracuyá


@pytest.fixture
def mundo(api: TestClient, catalogos: None, nueva_cuenta: NuevaCuenta) -> Mundo:
    from tests.api.territorio import crear_cultivo, crear_finca, crear_productor

    lider = nueva_cuenta(Rol.LIDER_TECNICO, nombre="Laura Líder")
    t1 = nueva_cuenta(Rol.TECNICO, 1, nombre="Ana Técnica")
    t2 = nueva_cuenta(Rol.TECNICO, 2, nombre="Beto Técnico")
    p1 = crear_productor(api, lider, 1, nombre="Ana Pérez")
    p2 = crear_productor(api, lider, 2, nombre="Álvaro Díaz")
    f1 = crear_finca(api, lider, p1["id"], "La Esperanza", "Centro")
    f2 = crear_finca(api, lider, p2["id"], "El Roble", "San Luis")
    c1 = crear_cultivo(api, lider, f1["id"], "Lote 1", "CAMPO_ABIERTO", ("GULUPA", "MARACUYA"))["cultivos"][0]["id"]
    c2 = crear_cultivo(api, lider, f2["id"], "Lote 1", "INVERNADERO", ("MARACUYA",))["cultivos"][0]["id"]
    return Mundo(lider, t1, t2, p1, p2, f1, f2, c1, c2)
