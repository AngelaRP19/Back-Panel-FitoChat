"""Menú «¿Qué quieres hacer ahora?»: lo envía el chatbot tras las novedades de un caso y el panel tras el informe del
técnico o el cierre. Los ids de los botones los atiende el orquestador (`Orquestador._que_hacer`)."""

from app.infrastructure.whatsapp_mensajes import Botones, Opcion
from app.modules.orchestrator import textos as t

CASO_NUEVO, MIS_CASOS, NADA = "caso_nuevo", "mis_casos", "nada"
OPCIONES = [(CASO_NUEVO, t.BOTON_CASO_NUEVO), (MIS_CASOS, t.BOTON_MIS_CASOS), (NADA, t.BOTON_NADA)]
MENU_QUE_HACER = Botones(t.QUE_HACER, tuple(Opcion(i, titulo) for i, titulo in OPCIONES))
