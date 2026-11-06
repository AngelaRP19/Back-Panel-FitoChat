# Cambios al modelo de datos

Registro de lo que se cambió respecto al ERD físico (Anexo 3) para que el equipo actualice el diccionario de datos
(Anexo 2) y el diagrama (Anexo 3). Fuente: «Propuesta de ajustes al modelo de datos (D-01 a D-17)», decidida por el
equipo el 26-oct-2026, y sección 6.4 de `BACKEND_GUIA.md`. Se aplican en la migración
`migrations/versions/0002_ajustes_modelo.py`.

**Resumen:** 3 tablas nuevas · 23 columnas nuevas · 1 columna eliminada · 6 columnas que pasan a opcionales ·
1 cambio de tipo · 1 estado nuevo de alerta (`CORREGIDA`). Ninguna tabla se elimina ni se renombra.

Las tablas afectadas deben estar sin datos al migrar (la base de RDS está vacía). Las columnas nuevas obligatorias no
llevan valor por defecto salvo `casos.ultima_actividad` (`now()`) y `fotografias.es_util` (`'S'`). El `downgrade`
restablece las restricciones NOT NULL y por eso también exige las tablas sin datos de estos casos.

## Tablas nuevas

| Tabla | Ajuste | Columnas | Motivo |
|---|---|---|---|
| `asignaciones` | D-01 | `id_asignacion` String(12) PK (`ASG-00000001`) · `id_caso` String(12) FK `fk_asg_caso` → `casos`, obligatoria · `id_tecnico` String(10) FK `fk_asg_tecnico` → `tecnicos`, obligatoria · `fecha_asignacion` timestamptz, obligatoria · `fecha_fin` timestamptz, opcional | Guarda quién tiene asignado cada caso. Al reasignar se cierra la vigente con `fecha_fin` y se crea otra. El responsable siempre es una cuenta de técnico (D-02). Índice único parcial `uq_asg_caso_vigente` sobre `id_caso` donde `fecha_fin IS NULL`: una sola asignación vigente por caso |
| `alerta_casos` | D-08 | `id_alerta` String(10) FK `fk_alc_alerta` · `id_caso` String(12) FK `fk_alc_caso` · PK compuesta `pk_alerta_casos` (`id_alerta`, `id_caso`) · índice `ix_alerta_casos_caso` | Qué casos originan cada alerta |
| `alerta_destinatarios` | D-08, D-17 | `id_alerta_destinatario` String(12) PK (`ADE-00000001`) · `id_alerta` String(10) FK `fk_ade_alerta` · `id_productor` String(10) FK `fk_ade_productor` · `tipo_envio` String(10): `ALERTA` o `CORRECCION` (CHECK) · `estado_envio` String(15) · `fecha_envio` timestamptz, opcional · `detalle_error` String(500), opcional · único `uq_ade_envio` (`id_alerta`, `id_productor`, `tipo_envio`) | A qué productor se envió la alerta o su corrección, con el resultado de cada envío. El único evita duplicar el mensaje en un reintento. En modo simulado el estado es `SIMULADO`, nunca `ENVIADO` |

## Columna eliminada

| Tabla | Columna | Ajuste | Motivo |
|---|---|---|---|
| `diagnosticos` | `id_tecnico` (con su FK `fk_dia_tecnico` y el índice `ix_diag_tecnico`) | D-01 | El autor del diagnóstico es el técnico de la asignación vigente; dejar las dos rutas permitía que se contradijeran. `diagnosticos` conserva `id_caso` con su `UNIQUE` `uq_dia_caso` |

## Columnas nuevas en tablas existentes (23)

| Tabla | Columna | Tipo | Obligatoria | Ajuste | Motivo |
|---|---|---|---|---|---|
| `casos` | `fecha_envio_panel` | timestamptz | no | D-06 | Cuándo llegó el caso al panel; ordena la bandeja |
| `casos` | `fecha_cierre` | timestamptz | no | D-06 | Cuándo se cerró |
| `casos` | `vence_en` | date | no | D-06 | Vencimiento de conservación (cierre + 6 meses) |
| `casos` | `ultima_actividad` | timestamptz | sí, por defecto `now()` | D-06 | Último mensaje del productor; base de las reglas de 48 h y 72 h |
| `alertas` | `numero_minimo_casos` | integer | sí | D-08 | Parámetro con que se detectó la alerta |
| `alertas` | `distancia_km` | numeric(5,2) | sí | D-08 | Ídem |
| `alertas` | `intervalo_dias` | integer | sí | D-08 | Ídem |
| `alertas` | `recomendacion` | string(300) | no | D-08 | Recomendación que redacta el líder |
| `alertas` | `mensaje_enviado` | string(1000) | no | D-08 | Texto completo que se envió |
| `alertas` | `id_lider_gestion` | string(10), FK `fk_ale_lider_gestion` → `lideres_tecnicos` | no | D-08 | Quién la confirmó o descartó |
| `alertas` | `fecha_confirmacion` | timestamptz | no | D-08 | Cuándo |
| `alertas` | `justificacion_descarte` | string(500) | no | D-08 | Motivo obligatorio del descarte (lo exige la API) |
| `alertas` | `mensaje_correccion` | string(300) | no | D-17 | Explicación del líder en la corrección |
| `alertas` | `fecha_correccion` | timestamptz | no | D-17 | Cuándo se corrigió |
| `alertas` | `id_lider_correccion` | string(10), FK `fk_ale_lider_correccion` → `lideres_tecnicos` | no | D-17 | Quién corrigió |
| `auditoria` | `id_tecnico` | string(10), FK `fk_aud_tecnico` → `tecnicos` | no | D-09 | Autor cuando es un técnico |
| `auditoria` | `origen` | string(10): `LIDER`, `TECNICO` o `SISTEMA` | sí | D-09 | Quién actuó; el CHECK `ck_auditoria_origen_coherente` exige que el autor coincida con el origen |
| `cultivos` | `tipo_cultivo` | string(15): `INVERNADERO` o `CAMPO_ABIERTO` | sí | D-10 | Distingue invernadero y campo abierto |
| `fotografias` | `es_util` | char(1): `S` o `N` | sí, por defecto `S` | D-11 | Límite de 12 fotos útiles por caso sin borrar las descartadas |
| `productores` | `fecha_consentimiento` | timestamptz | no | D-12 | Constancia del consentimiento (Ley 1581 de 2012) |
| `eventos` | `actor_tipo` | string(15) | no | D-13 | Quién hizo el paso (`LIDER`, `TECNICO`, `PRODUCTOR`, `SISTEMA`) |
| `eventos` | `actor_id` | string(10) | no | D-13 | Id del actor; sin FK porque apunta a varias tablas |
| `identificacion_preliminar` | `detalle_explicacion` | jsonb | no | D-14 | Síntomas que coinciden y que descartan cada categoría; `explicacion_resultado` queda como resumen |

## Columnas que pasan a opcionales (6)

| Tabla | Columna | Ajuste | Regla que lo compensa |
|---|---|---|---|
| `casos` | `id_identificacion_preliminar` | D-03 | Se llena cuando termina el análisis; el caso nace antes |
| `diagnosticos` | `id_categoria_confirmada` | D-04 | CHECK `ck_diagnosticos_categoria_requerida`: obligatoria si el resultado es `CONFIRMADO` u `OTRA_PATOLOGIA` |
| `diagnosticos` | `descripcion_diagnostico` | D-04 | El equipo decide la regla (P-18) sin volver a cambiar la base |
| `diagnosticos` | `resultado_tecnico` | D-05 | La fila se crea vacía al iniciar el caso; el diagnóstico está registrado cuando el resultado no es nulo |
| `diagnosticos` | `fecha_diagnostico` | D-05 | CHECK `ck_diagnosticos_fecha_con_resultado`: con resultado, la fecha es obligatoria |
| `auditoria` | `id_lider_tecnico` | D-09 | CHECK `ck_auditoria_origen_coherente` |

## Cambio de tipo (1)

| Tabla | Columna | Antes | Ahora | Ajuste |
|---|---|---|---|---|
| `alertas` | `fecha_generacion_alerta` | date | timestamptz | D-08 |

## Valor nuevo en un estado

`alertas.estado_alerta` admite `CORREGIDA` (D-17). No hay CHECK sobre esa columna: los seis estados
(`GENERADA`, `EN_REVISION`, `CONFIRMADA`, `COMUNICADA`, `DESCARTADA`, `CORREGIDA`) se validan en la API con
`app/core/catalogos_api.py`.

## Decisiones que no cambian el modelo

| Ajuste | Decisión | Consecuencia |
|---|---|---|
| D-02 | Rechazado | El líder no diagnostica con su rol; el autor del diagnóstico es siempre una cuenta de `tecnicos`. `diagnosticos` no recibe `id_lider_tecnico` |
| D-07 | Rechazado | No hay tabla de ampliaciones de conservación |
| D-15 | Aprobado con ajustes | El estado de la conversación del chatbot vive en **DynamoDB**, no en PostgreSQL |
| D-16 | Rechazado | No hay tabla de preferencias de notificación |

## Interpretaciones donde la guía no daba el detalle

Para que el equipo las revise junto con los anexos:

- **Longitudes y tipos** no indicados: `alertas.mensaje_enviado` String(1000); `eventos.actor_tipo` String(15) y
  `eventos.actor_id` String(10); `alerta_destinatarios.estado_envio` String(15), `detalle_error` String(500).
- **`alerta_destinatarios`** lleva una clave propia (`ADE-`) más el único `uq_ade_envio`, para que cada envío tenga
  identidad y el reintento no lo duplique. La guía solo listaba sus campos.
- **`casos.ultima_actividad`** es obligatoria con valor por defecto `now()`; la guía solo decía «por defecto `now()`».
- **Parámetros en `alertas`** (`numero_minimo_casos`, `distancia_km`, `intervalo_dias`) son obligatorios, porque la
  alerta siempre conserva los parámetros con que se creó.
- **Sin CHECK adicionales** más allá de los aprobados: `estado_alerta`, `estado_envio` y `resultado_tecnico` se
  validan en la API. El único CHECK sobre valores de `estado_envio` queda pendiente de que el equipo defina sus
  estados (se conocen `SIMULADO` y `ENVIADO`).
- **Sin `ON DELETE`** en las claves foráneas nuevas, igual que el ERD: el borrado físico (módulo 8) elimina los
  dependientes en orden (`alerta_casos`, eventos, respuestas, fotografías, diagnóstico y asignaciones) antes que el
  caso.
- **Índices:** solo el único parcial `uq_asg_caso_vigente` y `ix_alerta_casos_caso`. Los demás que necesiten las
  consultas (por ejemplo `asignaciones(id_tecnico) WHERE fecha_fin IS NULL`) se agregan con su `EXPLAIN` en el módulo
  que los use.

## Correcciones a la migración inicial `0001`

Se corrigieron dentro de `0001` (nunca se aplicó en RDS): los CHECK se creaban con el nombre completo y Alembic les
anteponía otra vez `ck_<tabla>_`, lo que dejaba nombres duplicados y truncados con un hash. Ahora se llaman, por
ejemplo, `ck_casos_advertencia_inactividad_enviada_sn` y coinciden con los de los modelos. Si alguien aplicó `0001`
en otra base, esos cinco CHECK tendrán el nombre anterior.

## Datos semilla (no cambian la estructura)

Las semillas genéricas anteriores (estados Abierto y Diagnosticado, categorías de tipo genérico como Plaga y la
especie Curuba) se reemplazaron por las del proyecto: 7 estados, 5 categorías (solo Trips transmisible) y 3
especies. Hay un único parámetro epidemiológico, el de Trips (3,5 km, 30 días); su **número mínimo de casos es 3 y
es provisional** (P-B02) hasta que el equipo lo defina. Las preguntas del cuestionario no se siembran porque su
texto no está en los documentos (P-B10).

## Para actualizar los anexos

- **Anexo 2 (diccionario):** agregar las 3 tablas y las 23 columnas, quitar `diagnosticos.id_tecnico`, marcar como
  opcionales las 6 columnas y cambiar el tipo de `alertas.fecha_generacion_alerta`. Anotar que la fila de
  `diagnosticos` se crea vacía y que el diagnóstico existe cuando `resultado_tecnico` no es nulo.
- **Anexo 3 (diagrama):** agregar `asignaciones` → `casos` y `tecnicos`; quitar `diagnosticos` → `tecnicos`;
  agregar `auditoria` → `tecnicos`, `alertas` → `lideres_tecnicos` (gestión y corrección), `alerta_casos` y
  `alerta_destinatarios`.
