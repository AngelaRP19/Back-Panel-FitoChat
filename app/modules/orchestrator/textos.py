"""Todos los textos fijos del chatbot, en un solo lugar (guía §11, módulo 10).

Los textos marcados PROVISIONAL deben ser validados por el equipo (P-B07 para el aviso de privacidad). Ninguno receta
ni orienta tratamientos. Las preguntas de síntomas NO están aquí: las conduce la IA.
"""

# --- Saludo, consentimiento y registro -----------------------------------------------------------
SALUDO = "Hola, soy el asistente fitosanitario de la ANUC Zetaquirá. Te ayudo a describir un problema en tu cultivo para que un técnico lo revise."
SALUDO_CON_NOMBRE = "Hola {nombre}, soy el asistente fitosanitario de la ANUC Zetaquirá."
# PROVISIONAL (P-B07): falta el texto aprobado del aviso de privacidad (Ley 1581 de 2012).
CONSENTIMIENTO = (
    "Para atenderte necesito guardar tu nombre, tu número, los datos de tu finca y las fotos y respuestas de tu "
    "consulta. Solo los ve el equipo técnico de la ANUC y se borran cuando termina el plazo de conservación. "
    "¿Aceptas? Responde Sí o No."
)
CONSENTIMIENTO_NEGADO = "Entendido, no guardaré nada. Si cambias de opinión, escríbeme de nuevo."
PEDIR_NOMBRE = "Gracias. ¿Cómo te llamas? Escribe tu nombre completo."
NOMBRE_INVALIDO = "No pude entender el nombre. Escríbelo con letras, por ejemplo: María Pérez."
BIENVENIDA_NUEVO = "Listo, {nombre}, quedaste registrado."
CUENTA_DESHABILITADA = "Tu cuenta está deshabilitada y no puedo abrir consultas. Comunícate con la ANUC."
SIN_FINCAS = "Aún no tienes ninguna finca registrada. Pide a la ANUC que la registre y vuelve a escribirme."

# --- Finca, cultivo, especie y zona --------------------------------------------------------------
ELEGIR_FINCA = "¿Sobre qué finca es la consulta? Responde con el número:\n{lista}"
FINCA_UNICA = "Tu finca es {nombre}."
ELEGIR_CULTIVO = "¿En cuál cultivo? Responde con el número:\n{lista}"
CULTIVO_NUEVO_OPCION = "Registrar un cultivo nuevo"
CULTIVO_UNICO = "El cultivo es {nombre}."
SIN_CULTIVOS = (
    "Esta finca aún no tiene cultivos. Vamos a registrar uno. ¿Cómo se llama el cultivo (por ejemplo, Lote 1)?"
)
PEDIR_NOMBRE_CULTIVO = "¿Cómo se llama el cultivo (por ejemplo, Lote 1)?"
NOMBRE_CULTIVO_INVALIDO = "Ese nombre no sirve o ya existe en esta finca. Escribe otro."
PEDIR_TIPO_CULTIVO = "¿El cultivo está en invernadero o a campo abierto?\n1. Invernadero\n2. Campo abierto"
PEDIR_ESPECIES_CULTIVO = (
    "¿Qué especies tiene? Responde con los números separados por coma (por ejemplo: 1, 3):\n{lista}"
)
ESPECIES_INVALIDAS = "Elige al menos una especie de la lista, con su número."
CULTIVO_CREADO = "Cultivo {nombre} registrado."
ELEGIR_ESPECIE = "¿Cuál especie está afectada? Por ahora cada consulta es de una sola especie.\n{lista}"
PEDIR_AFECTACION = "¿La afectación es en todo el cultivo o solo en una parte?\n1. Todo el cultivo\n2. Una parte"
PEDIR_ZONA = "Describe en qué parte del cultivo está el problema (por ejemplo: la esquina junto a la quebrada)."
ZONA_INVALIDA = "Cuéntame en pocas palabras (hasta 300 letras) en qué parte está el problema."
ZONA_TOTAL = "Todo el cultivo"
OPCION_INVALIDA = "No te entendí. {ayuda}"
RESPONDE_CON_NUMERO = "Responde con el número de la opción."

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
    "total. Cuando termines, escribe LISTO."
)
FOTO_ACEPTADA = "Recibí la foto ({n} de {total}). Puedes enviar más o escribir LISTO."
FOTO_RECHAZADA = "Esa foto no sirve: {motivo}. Envíame otra, por favor."
FOTO_RECHAZADA_SIN_MOTIVO = "Esa foto no sirve. Envíame otra más clara y de cerca."
LIMITE_POR_TIPO = "Ya tengo {por_tipo} fotos de ese tipo. Envía de otra parte de la planta o escribe LISTO."
LIMITE_TOTAL = "Ya tengo las {total} fotos que necesito."
FALTA_FOTO = "Necesito al menos una foto útil para continuar. Envíame una, por favor."
SOLO_FOTOS = "En este paso solo puedo recibir fotos. Envía una foto o escribe LISTO."

# --- Resumen y envío -----------------------------------------------------------------------------
RESUMEN = (
    "Resumen de tu consulta:\n- Finca: {finca}\n- Cultivo: {cultivo} ({especie})\n- Zona afectada: {zona}\n"
    "- Respuestas: {respuestas}\n- Fotos: {fotos}\n¿Envío la consulta al técnico? Responde Sí o No."
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
NO_ENVIADO = "Está bien. Puedes enviar más fotos y cuando termines escribe LISTO."

# --- Salir, abandonar y reanudar -----------------------------------------------------------------
CONFIRMAR_SALIR = "¿Seguro que quieres cancelar la consulta? Se borrarán tus respuestas y fotos. Responde Sí o No."
SALIR_HECHO = "Consulta cancelada y datos borrados. Cuando quieras empezar otra, escríbeme."
SALIR_CANCELADO = "Seguimos con tu consulta."
NO_HAY_CONSULTA = "No tienes ninguna consulta abierta."
CONFIRMAR_ABANDONO = "Ya tienes una consulta abierta. ¿Quieres abandonarla y empezar otra? Responde Sí o No."
ABANDONO_HECHO = "Listo, descarté la consulta anterior."
CONTINUAR_EXISTENTE = "Seguimos con tu consulta anterior."
CONSULTA_VENCIDA = "Tu consulta anterior se cerró por inactividad. Empecemos de nuevo."
SI_O_NO = "Responde Sí o No."
RETOMAR = "Retomamos donde quedamos."
ERROR_GENERICO = "No pude procesar tu mensaje. Inténtalo de nuevo."

AFIRMATIVOS = frozenset({"si", "sí", "s", "claro", "ok", "dale", "acepto", "afirmativo", "yes"})
NEGATIVOS = frozenset({"no", "n", "nunca", "negativo"})
TERMINAR = frozenset({"listo", "terminar", "ya", "fin", "enviar", "ya envié"})
