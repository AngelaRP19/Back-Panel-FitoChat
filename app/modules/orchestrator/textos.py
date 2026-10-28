"""Todos los textos fijos del chatbot, en un solo lugar (guía §11, módulo 10).

Los textos marcados PROVISIONAL deben ser validados por el equipo (P-B07 para el aviso de privacidad). Ninguno receta
ni orienta tratamientos. Las preguntas de síntomas NO están aquí: las conduce la IA.

Casi todas las preguntas van con botones o con una lista (ver `whatsapp_mensajes`); por eso los textos dicen «elige»
y no «responde con el número». Escribir la respuesta sigue funcionando: los botones usan los mismos valores.
"""

# --- Saludo, consentimiento y registro -----------------------------------------------------------
SALUDO = (
    "Hola, soy el asistente fitosanitario de la ANUC Zetaquirá. Te ayudo a describir un problema en tu cultivo para "
    "que un técnico lo revise. Si en algún momento quieres empezar de nuevo, escribe REINICIAR."
)
SALUDO_CON_NOMBRE = (
    "Hola {nombre}, soy el asistente fitosanitario de la ANUC Zetaquirá. Si en algún momento quieres empezar de "
    "nuevo, escribe REINICIAR."
)
# PROVISIONAL (P-B07): falta el texto aprobado del aviso de privacidad (Ley 1581 de 2012).
CONSENTIMIENTO = (
    "Para atenderte necesito guardar tu nombre, tu número, los datos y la ubicación de tu finca y las fotos y "
    "respuestas de tu consulta. Solo los ve el equipo técnico de la ANUC y se borran cuando termina el plazo de "
    "conservación. ¿Aceptas?"
)
CONSENTIMIENTO_NEGADO = "Entendido, no guardaré nada. Si cambias de opinión, escríbeme de nuevo."
PEDIR_NOMBRE = "Gracias. ¿Cómo te llamas? Escribe tu nombre completo."
NOMBRE_INVALIDO = "No pude entender el nombre. Escríbelo con letras, por ejemplo: María Pérez."
BIENVENIDA_NUEVO = "Listo, {nombre}, quedaste registrado."
CUENTA_DESHABILITADA = "Tu cuenta está deshabilitada y no puedo abrir consultas. Comunícate con la ANUC."

# --- Elegir o registrar la finca -----------------------------------------------------------------
ELEGIR_FINCA = "¿Sobre qué finca es la consulta? Elige una de tus fincas o registra una nueva."
ELEGIR_FINCA_TEXTO = "¿Sobre qué finca es la consulta? Escribe el número, o NUEVA para registrar otra finca:\n{lista}"
SIN_FINCAS = "Aún no tienes fincas registradas. Vamos a registrar la primera."
PEDIR_NOMBRE_FINCA = "¿Cómo se llama la finca? (por ejemplo, La Esperanza)"
NOMBRE_FINCA_INVALIDO = "Escribe el nombre de la finca con letras, en máximo 100 caracteres."
PEDIR_VEREDA = "¿En qué vereda está la finca {finca}? Si no aparece en la lista, escribe su nombre."
PEDIR_VEREDA_ESCRITA = "¿En qué vereda está la finca {finca}? Escribe su nombre."
ESCRIBE_VEREDA = "Escribe el nombre de la vereda."
VEREDA_INVALIDA = "Escribe el nombre de la vereda con letras, en máximo 100 caracteres."
PEDIR_UBICACION_FINCA = (
    "Ahora necesito la ubicación de la finca {finca}. Cuando estés en la finca, toca «Enviar ubicación» y comparte "
    "tu ubicación actual."
)
FINCA_CREADA = "Finca {nombre} registrada."

# --- Elegir o registrar el lote ------------------------------------------------------------------
ELEGIR_CULTIVO = "¿En qué lote de la finca {finca} es el problema? Elige uno o registra uno nuevo."
ELEGIR_CULTIVO_TEXTO = "¿En qué lote es el problema? Escribe el número, o NUEVO para registrar otro lote:\n{lista}"
SIN_CULTIVOS = "La finca {finca} aún no tiene lotes registrados. Vamos a registrar uno."
PEDIR_NOMBRE_CULTIVO = "¿Cómo se llama el lote? (por ejemplo, Lote 1)"
NOMBRE_CULTIVO_INVALIDO = "Ese nombre no sirve o ya existe en esta finca. Escribe otro."
PEDIR_TIPO_CULTIVO = "¿El lote está en invernadero o a campo abierto?"
PEDIR_ESPECIES_CULTIVO = "¿Qué cultivos hay en el lote {lote}?"
ESPECIES_INVALIDAS = "Elige una de las opciones de la lista."
PEDIR_UBICACION_LOTE = (
    "Ahora necesito la ubicación del lote {lote}. Párate dentro del lote de producción, toca «Enviar ubicación» y "
    "comparte tu ubicación actual."
)
CULTIVO_CREADO = "Lote {nombre} registrado."

# --- Ubicación -----------------------------------------------------------------------------------
UBICACION_FALTA = (
    "Necesito la ubicación. Toca «Enviar ubicación» (o el clip 📎 → Ubicación) y comparte tu ubicación actual."
)
UBICACION_FUERA_DE_COLOMBIA = "Esa ubicación no está en Colombia. Comparte tu ubicación actual desde la finca."
CONFIRMAR_UBICACION = "¿La ubicación de {nombre} es la que te muestro en el mapa?"
AVISO_FUERA_DE_ZETAQUIRA = "Ojo: ese punto parece estar fuera de Zetaquirá."
AVISO_LOTE_LEJOS = "Ojo: ese punto está a {km} km de la finca. El lote debería estar dentro de la finca."
OTRA_UBICACION = "Está bien. Comparte otra vez tu ubicación actual."

# --- Especie, afectación y zona ------------------------------------------------------------------
ELEGIR_ESPECIE = "¿Cuál cultivo está afectado? Por ahora cada consulta es de uno solo."
PEDIR_AFECTACION = "¿El problema está en todo el lote o solo en una parte?"
PEDIR_ZONA = "Describe en qué parte del lote está el problema (por ejemplo: la esquina junto a la quebrada)."
ZONA_INVALIDA = "Cuéntame en pocas palabras (hasta 300 letras) en qué parte está el problema."
ZONA_TOTAL = "Todo el cultivo"
OPCION_INVALIDA = "No te entendí. {ayuda}"
RESPONDE_CON_NUMERO = "Elige una de las opciones."

# --- Cuestionario (lo conduce la IA) -------------------------------------------------------------
CUESTIONARIO_INICIO = "Voy a hacerte unas preguntas sobre lo que ves."
FUERA_DE_DOMINIO = "Solo puedo ayudarte con problemas fitosanitarios de tus cultivos de gulupa, granadilla y maracuyá."
SIN_PREGUNTA = "Gracias por tus respuestas."
IA_NO_DISPONIBLE = (
    "Disculpa, no pude hacerte más preguntas en este momento. Sigamos con las fotos y un técnico de la ANUC "
    "revisará tu caso."
)
PREGUNTA_BLOQUEADA = "Prefiero no responder eso. Un técnico de la ANUC revisará tu caso."

# --- Fotos ---------------------------------------------------------------------------------------
PEDIR_FOTOS = (
    "Ahora envíame fotos de la planta afectada (hoja, envés, fruto o tallo). Hasta {por_tipo} por tipo y {total} en "
    "total. Cuando termines, toca Listo o escribe LISTO."
)
FOTO_ACEPTADA = "Recibí la foto ({n} de {total}). Puedes enviar más o tocar Listo."
FOTO_RECHAZADA = "Esa foto no sirve: {motivo}. Envíame otra, por favor."
FOTO_RECHAZADA_SIN_MOTIVO = "Esa foto no sirve. Envíame otra más clara y de cerca."
LIMITE_POR_TIPO = "Ya tengo {por_tipo} fotos de ese tipo. Envía de otra parte de la planta o toca Listo."
LIMITE_TOTAL = "Ya tengo las {total} fotos que necesito."
FALTA_FOTO = "Necesito al menos una foto útil para continuar. Envíame una, por favor."
SOLO_FOTOS = "En este paso solo puedo recibir fotos. Envía una foto o toca Listo."

# --- Resumen y envío -----------------------------------------------------------------------------
RESUMEN = (
    "Resumen de tu consulta:\n- Finca: {finca}\n- Lote: {cultivo} ({especie})\n- Zona afectada: {zona}\n"
    "- Respuestas: {respuestas}\n- Fotos: {fotos}\n¿Envío la consulta al técnico?"
)
ENVIADO = (
    "Listo, envié tu consulta a los técnicos de la ANUC. Un técnico la revisará y tomará la decisión definitiva. "
    "Esta orientación es preliminar y no reemplaza el diagnóstico del técnico."
)
RESULTADO_PRELIMINAR = "Según lo que me contaste, el resultado preliminar es: {resultado}."
RESULTADO_AMBIGUO = (
    "Según lo que me contaste, tus síntomas se parecen a más de una posibilidad ({primera} y {segunda}). "
    "El técnico lo definirá."
)
NO_ENVIADO = "Está bien. Puedes enviar más fotos y cuando termines toca Listo."

# --- Salir, abandonar y reanudar -----------------------------------------------------------------
CONFIRMAR_SALIR = "¿Seguro que quieres cancelar la consulta? Se borrarán tus respuestas y fotos."
SALIR_HECHO = "Consulta cancelada y datos borrados. Cuando quieras empezar otra, escríbeme."
SALIR_CANCELADO = "Seguimos con tu consulta."
REINICIADO = "Listo, empecemos de nuevo."
CONFIRMAR_ABANDONO = "Ya tienes una consulta abierta. ¿Quieres abandonarla y empezar otra?"
ABANDONO_HECHO = "Listo, descarté la consulta anterior."
CONTINUAR_EXISTENTE = "Seguimos con tu consulta anterior."
CONSULTA_VENCIDA = "Tu consulta anterior se cerró por inactividad. Empecemos de nuevo."
SI_O_NO = "Elige Sí o No."
RETOMAR = "Retomamos donde quedamos."
ERROR_GENERICO = "No pude procesar tu mensaje. Inténtalo de nuevo."

# --- Etiquetas de botones y listas (máximo 20 caracteres en botones, 24 en filas) ----------------
BOTON_SI = "Sí"
BOTON_NO = "No"
BOTON_ACEPTO = "Sí, acepto"
BOTON_NO_ACEPTO = "No acepto"
BOTON_ENVIAR = "Sí, enviar"
BOTON_SEGUIR = "No, seguir"
BOTON_LISTO = "Listo, terminé"
BOTON_ES_AQUI = "Sí, es aquí"
BOTON_OTRA_UBICACION = "Enviar otra"
BOTON_INVERNADERO = "Invernadero"
BOTON_CAMPO_ABIERTO = "Campo abierto"
BOTON_TODO = "Todo el lote"
BOTON_UNA_PARTE = "Una parte"
VER_FINCAS = "Ver fincas"
VER_LOTES = "Ver lotes"
VER_VEREDAS = "Ver veredas"
VER_CULTIVOS = "Ver opciones"
FILA_FINCA_NUEVA = "Registrar finca nueva"
FILA_LOTE_NUEVO = "Registrar lote nuevo"
FILA_OTRA_VEREDA = "Otra vereda"
DESCRIPCION_FINCA_NUEVA = "Nombre, vereda y ubicación"
DESCRIPCION_LOTE_NUEVO = "Nombre, tipo, cultivos y ubicación"
DESCRIPCION_OTRA_VEREDA = "La escribes tú"

AFIRMATIVOS = frozenset({"si", "sí", "s", "claro", "ok", "dale", "acepto", "afirmativo", "yes"})
NEGATIVOS = frozenset({"no", "n", "nunca", "negativo"})
SALIR = frozenset({"/salir", "/reiniciar", "reiniciar", "salir", "cancelar", "empezar de nuevo"})
TERMINAR = frozenset({"listo", "terminar", "ya", "fin", "enviar", "ya envié"})
NUEVA = frozenset({"nueva", "nuevo", "finca nueva", "lote nuevo", "registrar", "otra", "otro"})
