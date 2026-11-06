"""Credenciales de la cuenta de IA: AssumeRole con ExternalId y renovación antes de que venzan."""

from typing import Any

import pytest

from app.core.config import get_settings
from app.infrastructure import ia_credenciales
from app.infrastructure.ia_credenciales import MARGEN_RENOVACION_SEGUNDOS, ProveedorClientesIA


class Reloj:
    def __init__(self) -> None:
        self.t = 1_000_000.0

    def __call__(self) -> float:
        return self.t


class StsFalso:
    def __init__(self, reloj: Reloj) -> None:
        self.reloj = reloj
        self.llamadas: list[dict[str, Any]] = []

    def assume_role(self, **parametros: Any) -> dict[str, Any]:
        self.llamadas.append(parametros)
        n = len(self.llamadas)
        return {
            "Credentials": {
                "AccessKeyId": f"AK{n}",
                "SecretAccessKey": "secreto",
                "SessionToken": f"TOKEN{n}",
                "Expiration": self.reloj.t + 3600,
            }
        }


def _proveedor() -> tuple[ProveedorClientesIA, StsFalso, Reloj]:
    reloj = Reloj()
    sts = StsFalso(reloj)
    proveedor = ProveedorClientesIA(
        sts,
        "arn:aws:iam::111122223333:role/ia",
        "externo-123",
        "us-east-1",
        reloj=reloj,
        fabrica=lambda servicio, **credenciales: (servicio, credenciales["aws_session_token"]),
    )
    return proveedor, sts, reloj


def test_asume_el_rol_con_external_id_y_duracion_maxima_de_la_sesion() -> None:
    proveedor, sts, _ = _proveedor()
    assert proveedor.cliente("bedrock-runtime") == ("bedrock-runtime", "TOKEN1")
    assert sts.llamadas == [
        {
            "RoleArn": "arn:aws:iam::111122223333:role/ia",
            "RoleSessionName": "anuc-backend",
            "ExternalId": "externo-123",
            "DurationSeconds": 3600,
        }
    ]


def test_mientras_estan_vigentes_se_reutilizan_y_se_comparten_entre_servicios() -> None:
    proveedor, sts, reloj = _proveedor()
    primero = proveedor.cliente("bedrock-runtime")
    reloj.t += 3600 - MARGEN_RENOVACION_SEGUNDOS - 1  # todavía antes del margen
    assert proveedor.cliente("bedrock-runtime") is primero
    assert proveedor.cliente("rekognition") == ("rekognition", "TOKEN1")  # mismas credenciales, otro servicio
    assert len(sts.llamadas) == 1 and proveedor.renovaciones == 1


def test_se_renuevan_antes_de_vencer_y_los_clientes_usan_las_nuevas() -> None:
    proveedor, sts, reloj = _proveedor()
    assert proveedor.cliente("rekognition") == ("rekognition", "TOKEN1")
    reloj.t += 3600 - MARGEN_RENOVACION_SEGUNDOS  # entra al margen de 5 minutos
    assert proveedor.cliente("rekognition") == ("rekognition", "TOKEN2")
    assert len(sts.llamadas) == 2 and proveedor.renovaciones == 2


def test_si_no_se_pueden_renovar_el_error_sube() -> None:
    proveedor, sts, reloj = _proveedor()
    proveedor.cliente("rekognition")
    reloj.t += 4000

    def falla(**_: Any) -> Any:
        raise RuntimeError("AccessDenied")

    sts.assume_role = falla  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="AccessDenied"):
        proveedor.cliente("rekognition")


def test_sin_rol_ni_external_id_no_arranca(monkeypatch: pytest.MonkeyPatch) -> None:
    sin_ia = get_settings().model_copy(update={"ia_role_arn": None, "ia_external_id": None})
    monkeypatch.setattr(ia_credenciales, "get_settings", lambda: sin_ia)
    monkeypatch.setattr(ia_credenciales, "_PROVEEDOR", None)
    with pytest.raises(RuntimeError, match="IA_ROLE_ARN"):
        ia_credenciales.get_proveedor_ia()
