"""Constantes de las evidencias (fotos) de un caso, compartidas por el chatbot y el panel."""

# Cada foto cuelga de una respuesta (modelo de datos). Las respuestas que solo transportan una foto llevan esta
# «pregunta»: el panel no las cuenta ni las lista como parte del cuestionario de síntomas.
PREGUNTA_FOTO = "Fotografía de evidencia"
MAXIMO_FOTOS_UTILES = 12
# Las fotos no se clasifican por parte de la planta (hoja, envés, fruto, tallo): basta con que sean de la planta.
# La columna `fotografias.tipo_evidencia` sigue siendo obligatoria; las fotos nuevas llevan este valor y las
# anteriores conservan el suyo, que ya no se muestra.
TIPO_FOTO = "PLANTA"
