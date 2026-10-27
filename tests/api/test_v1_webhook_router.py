import json
from typing import Any
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from app.infrastructure.sqs_queue_client import SqsQueueClient, get_sqs_queue_client
from app.main import app
from app.modules.webhook.signature import compute_signature
from tests.conftest import APP_SECRET, VERIFY_TOKEN
from tests.payloads import PHONE, image_message, status_event, text_message

URL = "/webhook/whatsapp"


def _post_signed(client: TestClient, payload: dict[str, Any] | bytes) -> Any:
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    return client.post(
        URL,
        content=body,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": compute_signature(body, APP_SECRET)},
    )


def _receive_all(sqs: tuple[Any, str]) -> list[dict[str, Any]]:
    sqs_client, queue_url = sqs
    response = sqs_client.receive_message(
        QueueUrl=queue_url, MaxNumberOfMessages=10, AttributeNames=["All"], MessageSystemAttributeNames=["All"]
    )
    return response.get("Messages", [])


# --- GET: verificación de Meta ---------------------------------------------------------------


def test_verificacion_ok_devuelve_challenge_en_texto_plano(client: TestClient) -> None:
    response = client.get(
        URL, params={"hub.mode": "subscribe", "hub.verify_token": VERIFY_TOKEN, "hub.challenge": "1158201444"}
    )
    assert response.status_code == 200
    assert response.text == "1158201444"
    assert response.headers["content-type"].startswith("text/plain")


def test_verificacion_token_incorrecto_403(client: TestClient) -> None:
    response = client.get(URL, params={"hub.mode": "subscribe", "hub.verify_token": "otro", "hub.challenge": "x"})
    assert response.status_code == 403
    assert response.json()["codigo"] == "SIN_PERMISO"


def test_verificacion_modo_incorrecto_403(client: TestClient) -> None:
    response = client.get(
        URL, params={"hub.mode": "unsubscribe", "hub.verify_token": VERIFY_TOKEN, "hub.challenge": "x"}
    )
    assert response.status_code == 403


def test_verificacion_sin_parametros_403(client: TestClient) -> None:
    assert client.get(URL).status_code == 403


# --- POST: firma -----------------------------------------------------------------------------


def test_firma_invalida_401_y_no_encola(client: TestClient, sqs: tuple[Any, str]) -> None:
    body = json.dumps(image_message()).encode("utf-8")
    response = client.post(URL, content=body, headers={"X-Hub-Signature-256": "sha256=" + "0" * 64})
    assert response.status_code == 401
    assert response.json() == {"codigo": "NO_AUTENTICADO", "mensaje": "Firma inválida."}
    assert _receive_all(sqs) == []


def test_sin_firma_401(client: TestClient) -> None:
    response = client.post(URL, content=json.dumps(image_message()).encode("utf-8"))
    assert response.status_code == 401


def test_firma_calculada_con_otro_secreto_401(client: TestClient) -> None:
    body = json.dumps(image_message()).encode("utf-8")
    response = client.post(URL, content=body, headers={"X-Hub-Signature-256": compute_signature(body, "otro")})
    assert response.status_code == 401


def test_firma_sobre_bytes_crudos_no_sobre_json_reserializado(client: TestClient, sqs: tuple[Any, str]) -> None:
    # Espacios y orden distintos a json.dumps: la firma solo cuadra con los bytes exactos.
    body = b'{ "entry" :   [] ,"object":"whatsapp_business_account"}'
    assert _post_signed(client, body).status_code == 200


# --- POST: encolado --------------------------------------------------------------------------


def test_mensaje_de_imagen_llega_a_sqs_con_atributos_correctos(client: TestClient, sqs: tuple[Any, str]) -> None:
    response = _post_signed(client, image_message(wamid="wamid.IMG001"))
    assert response.status_code == 200

    messages = _receive_all(sqs)
    assert len(messages) == 1
    attrs = messages[0]["Attributes"]
    assert attrs["MessageGroupId"] == PHONE
    assert attrs["MessageDeduplicationId"] == "wamid.IMG001"
    assert json.loads(messages[0]["Body"]) == {
        "wamid": "wamid.IMG001",
        "from": PHONE,
        "type": "image",
        "timestamp": "1760000000",
        "content": {"media_id": "MEDIA123", "mime_type": "image/jpeg", "caption": "Hojas con manchas"},
    }


def test_mensaje_de_texto_llega_a_sqs(client: TestClient, sqs: tuple[Any, str]) -> None:
    assert _post_signed(client, text_message()).status_code == 200
    body = json.loads(_receive_all(sqs)[0]["Body"])
    assert body["type"] == "text"
    assert body["content"] == {"text": "Hola"}


def test_evento_statuses_no_genera_envio(client: TestClient, sqs: tuple[Any, str]) -> None:
    response = _post_signed(client, status_event())
    assert response.status_code == 200
    assert _receive_all(sqs) == []


def test_reintento_de_meta_no_duplica(client: TestClient, sqs: tuple[Any, str]) -> None:
    payload = image_message(wamid="wamid.DUP")
    assert _post_signed(client, payload).status_code == 200
    assert _post_signed(client, payload).status_code == 200
    assert len(_receive_all(sqs)) == 1


def test_json_invalido_con_firma_valida_responde_200(client: TestClient, sqs: tuple[Any, str]) -> None:
    assert _post_signed(client, b"no es json").status_code == 200
    assert _receive_all(sqs) == []


def test_fallo_de_sqs_responde_500(client: TestClient) -> None:
    failing = MagicMock()
    failing.send_message.side_effect = RuntimeError("SQS caído")
    app.dependency_overrides[get_sqs_queue_client] = lambda: SqsQueueClient(failing, "url")

    response = _post_signed(client, image_message())
    assert response.status_code == 500
    assert response.json()["codigo"] == "ERROR_INTERNO"
    assert "SQS" not in response.text
