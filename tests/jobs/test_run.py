"""`python -m app.jobs.run <nombre>`: el código de salida es lo que ven EventBridge Scheduler y CloudWatch."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text

from app.core import config
from app.jobs import run
from app.jobs.run import CONFIGURACION, ERROR, OK, main
from app.modules.scheduler import jobs
from app.modules.scheduler.jobs import ResultadoJob, bloqueo
from tests.api.territorio import crear_caso
from tests.modules.scheduler.conftest import AlmacenS3, Entorno


def ajustes(**cambios: Any) -> Any:
    return config.get_settings().model_copy(update=cambios)


def test_un_nombre_desconocido_es_un_error_de_uso(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as salida:
        main(["no_existe"])
    assert salida.value.code == 2 and "invalid choice" in capsys.readouterr().err


def test_sin_nombre_es_un_error_de_uso() -> None:
    with pytest.raises(SystemExit) as salida:
        main([])
    assert salida.value.code == 2


@pytest.mark.parametrize("nombre", ["descarte_inactividad", "retencion"])
def test_sin_bucket_los_jobs_de_borrado_no_arrancan(
    nombre: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(run, "get_settings", lambda: ajustes(s3_bucket_evidencias=None))
    assert main([nombre]) == CONFIGURACION
    assert "S3_BUCKET_EVIDENCIAS" in capsys.readouterr().err


def test_sin_cliente_de_whatsapp_la_advertencia_no_arranca(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_disponible() -> Any:
        raise RuntimeError("El cliente real de WhatsApp llega en la Fase C")

    monkeypatch.setattr(run, "get_whatsapp_client", no_disponible)
    assert main(["advertencia_inactividad"]) == CONFIGURACION


def test_un_job_sin_errores_sale_con_cero(entorno: Entorno) -> None:
    assert main(["evaluar_alertas"]) == OK


def test_un_job_con_errores_sale_con_uno(entorno: Entorno, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        jobs.JOBS, "evaluar_alertas", lambda engine, ctx: ResultadoJob("evaluar_alertas", procesados=2, errores=1)
    )
    assert main(["evaluar_alertas"]) == ERROR


def test_una_excepcion_inesperada_sale_con_uno(entorno: Entorno, monkeypatch: pytest.MonkeyPatch) -> None:
    def explota(engine: Any, ctx: Any) -> Any:
        raise RuntimeError("fallo inesperado")

    monkeypatch.setitem(jobs.JOBS, "evaluar_alertas", explota)
    assert main(["evaluar_alertas"]) == ERROR


def test_si_otra_ejecucion_sigue_corriendo_se_omite_y_sale_con_cero(entorno: Entorno) -> None:
    with bloqueo(entorno.motor, "evaluar_alertas") as obtenido:
        assert obtenido
        assert main(["evaluar_alertas"]) == OK


def test_descarte_de_punta_a_punta_por_la_linea_de_comandos(
    entorno: Entorno, s3: AlmacenS3, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(run, "get_settings", lambda: ajustes(s3_bucket_evidencias="anuc-evidencias-test"))
    monkeypatch.setattr(run, "build_boto3_s3_client", lambda: s3.crudo)
    viejo = crear_caso(entorno.motor, entorno.id_cultivo, "GULUPA", "En análisis")
    reciente = crear_caso(entorno.motor, entorno.id_cultivo, "GULUPA", "En análisis")
    with entorno.motor.begin() as c:
        c.execute(
            text("UPDATE casos SET ultima_actividad = :t WHERE id_caso = :c"),
            {"t": datetime.now(UTC) - timedelta(hours=100), "c": viejo},
        )
    assert main(["descarte_inactividad"]) == OK
    with entorno.motor.connect() as c:
        quedan = {f[0] for f in c.execute(text("SELECT id_caso FROM casos"))}
    assert quedan == {reciente}


def test_la_advertencia_de_punta_a_punta_usa_el_cliente_simulado(
    entorno: Entorno, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.infrastructure.whatsapp_client import WhatsAppSimulado

    cliente = WhatsAppSimulado()
    monkeypatch.setattr(run, "get_whatsapp_client", lambda: cliente)
    id_caso = crear_caso(entorno.motor, entorno.id_cultivo, "GULUPA", "En análisis")
    with entorno.motor.begin() as c:
        c.execute(
            text("UPDATE casos SET ultima_actividad = :t WHERE id_caso = :c"),
            {"t": datetime.now(UTC) - timedelta(hours=50), "c": id_caso},
        )
    assert main(["advertencia_inactividad"]) == OK
    assert [tel for tel, _ in cliente.enviados] == [entorno.celular_e164]
