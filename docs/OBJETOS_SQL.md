# Objetos SQL del Avance 5

Vistas, funciones, procedimientos y triggers de la guía §6.6, creados en `migrations/versions/0003_objetos_sql.py`.
Los `sp_*` son `PROCEDURE` de PL/pgSQL y se invocan con `CALL`. Las sentencias listas para usar con SQLAlchemy están en
`app/infrastructure/db/procedimientos.py` y sirven igual con una `Connection` que con una `AsyncSession`.

## Errores de negocio

Los objetos avisan con un SQLSTATE propio; la capa de servicio los traduce a códigos HTTP.

| SQLSTATE | `SqlState` | Significa | HTTP sugerido |
|---|---|---|---|
| `AN001` | `TRANSICION_INVALIDA` | El caso no está en el estado que exige la operación | `409 TRANSICION_INVALIDA` |
| `AN002` | `NO_ES_EL_TECNICO_ASIGNADO` | Quien actúa no es el técnico (activo) de la asignación vigente | `404` si el técnico no ve el caso, `403` en otro caso |
| `AN003` | `DATOS_INVALIDOS` | Resultado, categoría, motivo, origen o tipo desconocido o faltante | `422 VALIDACION` |
| `AN004` | `NO_ENCONTRADO` | El caso o el estado no existe | `404 NO_ENCONTRADO` |
| `AN005` | `AUDITORIA_INMUTABLE` | Se intentó modificar o borrar la auditoría | `500` (es un defecto del programa) |

`error_de_negocio(excepcion)` devuelve el `SqlState` o `None` si el fallo es de otra clase (por ejemplo una clave foránea).

## Contexto de la transacción

La aplicación lo fija al inicio de la transacción con `ESTABLECER_ACTOR` (usa `set_config(..., true)`, así que dura solo la
transacción). Los triggers lo anotan en `eventos.actor_tipo` y `eventos.actor_id` (D-13).

| Ajuste | Qué hace | Sin valor |
|---|---|---|
| `anuc.actor_tipo`, `anuc.actor_id` | Quién actúa (`LIDER`, `TECNICO`, `PRODUCTOR` o `SISTEMA`) | `SISTEMA`; al crear un caso, el productor dueño del cultivo; al diagnosticar, el técnico de la asignación vigente |
| `anuc.ahora` | Reloj simulado (pruebas y datos de demostración) | `now()` |

Con actor en el contexto se usan el tipo y el id completos, tal cual; el valor por defecto solo aplica si no hay actor.

## Funciones

| Función | Qué hace |
|---|---|
| `fn_id_estado(nombre)` | Id de un estado por nombre; `AN004` si no existe. Nunca se escribe el id de un catálogo en el código |
| `fn_distancia_km(a, b)` | Distancia en km entre dos puntos (`ST_Distance` sobre `geography`); nula si alguno es nulo |
| `fn_enmascarar_telefonos(texto)` | `573001234567` y `3001234567` quedan `***4567`; la usa la auditoría |
| `fn_ahora()`, `fn_actor_tipo(defecto)`, `fn_actor_id()` | Leen el contexto de la transacción |

## Vistas

| Vista | Contenido |
|---|---|
| `vw_casos_detalle` | Una fila por caso: productor, finca (con `ubicacion_finca`), cultivo, especie, estado, análisis con la categoría de mayor coincidencia, técnico de la asignación vigente y diagnóstico registrado |
| `vw_casos_pendientes_revision` | Los casos en «Pendiente de revisión», con `horas_en_espera` |
| `vw_diagnosticos_detalle` | Solo los diagnósticos registrados (resultado no nulo), con el autor (técnico de la asignación vigente) y la categoría |
| `vw_casos_confirmados_transmisibles` | Entrada de `sp_evaluar_alerta` (ver abajo) |
| `vw_ultima_actividad_caso` | Casos en «Caso creado» y «En análisis», con `horas_inactivo`; para los jobs de 48 h y 72 h |
| `vw_auditoria_detalle` | Auditoría con el nombre del autor (`Sistema` si no hay) |

## Procedimientos

| Procedimiento | Qué hace |
|---|---|
| `sp_registrar_evento(caso, tipo, descripcion, actor_tipo, actor_id, fecha)` | Línea de tiempo; valida el tipo de evento y de actor |
| `sp_registrar_auditoria(origen, id_actor, accion, entidad, descripcion, valor_anterior, valor_nuevo, resultado, fecha)` | Guarda el autor según el origen (D-09) y enmascara teléfonos |
| `sp_registrar_diagnostico(caso, tecnico, resultado, categoria, motivo_visita, descripcion, fecha)` | Exige **En revisión** y que `tecnico` sea el de la asignación vigente. Completa la fila vacía (la crea si faltara) y deja el caso **Registrado**. Evento y auditoría en la misma transacción |
| `sp_cerrar_caso(caso, actor_tipo, actor_id, meses, fecha)` | Exige **Registrado**; deja **Cerrado**, `fecha_cierre` y `vence_en` = cierre + `meses` (por defecto 6) con la fecha de Colombia; mes más corto: queda el último día |
| `sp_marcar_advertencia_enviada(caso)` | Marca la advertencia de 48 h; solo casos en «Caso creado» o «En análisis»; repetirla no hace nada |
| `sp_evaluar_alerta(categoria, fecha)` | Ver abajo |

`sp_registrar_diagnostico` valida: resultado ∈ {`CONFIRMADO`, `OTRA_PATOLOGIA`, `NO_IDENTIFICADO`, `REQUIERE_VISITA`};
categoría obligatoria con `CONFIRMADO` y `OTRA_PATOLOGIA`; motivo obligatorio con `REQUIERE_VISITA`. La regla P-04
(«`CONFIRMADO` solo si la identificación fue preliminar») sigue abierta y **no** está en el procedimiento: la aplica el servicio.

### Evaluación de alertas

1. Para cada categoría **transmisible** con parámetros (o solo la indicada), se toman los casos con diagnóstico de esa
   categoría de los últimos `intervalo_dias`.
2. **Foco:** cada caso de esos que todavía no pertenece a ninguna alerta de la categoría (de cualquier estado, así una alerta
   descartada no se vuelve a generar).
3. **Grupo:** los casos a `distancia_km` o menos del foco (el foco incluido). El radio se mide desde cada caso foco, no entre
   todos los casos del grupo.
4. Si el grupo llega a `numero_minimo_casos`: se suman sus casos a la alerta **abierta** (`GENERADA` o `EN_REVISION`) con la
   que ya comparta casos; si no hay, se crea una alerta `GENERADA` con los parámetros vigentes (que la alerta conserva).
5. Es idempotente. `categoria = NULL` es el barrido diario de respaldo (P-B01).

Cuentan como confirmados los diagnósticos `CONFIRMADO` y `OTRA_PATOLOGIA` cuya categoría, fijada por el técnico, es
transmisible. `NO_IDENTIFICADO`, `REQUIERE_VISITA` y los diagnósticos vacíos no cuentan.

## Triggers

| Trigger | Tabla | Hace |
|---|---|---|
| `trg_casos_creacion_evento` | `casos` (alta) | Anota `CASO_CREADO` |
| `trg_casos_estado_evento` | `casos` (cambio de `id_estado`) | Anota `ANALISIS_CHATBOT`, `ENVIADO_A_REVISION`, `TECNICO_ASIGNADO`, `CASO_CERRADO` o `CASO_DESCARTADO`. No anota «Caso creado» ni «Registrado»: ya los anotan la creación y el diagnóstico. Reasignar no cambia el estado: `TECNICO_REASIGNADO` lo registra el servicio con `sp_registrar_evento` |
| `trg_diag_evento` | `diagnosticos` | Anota `DIAGNOSTICO_REGISTRADO` cuando `resultado_tecnico` pasa de nulo a valor |
| `trg_diag_tecnico_activo` | `diagnosticos` | Con resultado, exige una asignación vigente de un técnico **activo** |
| `trg_param_transmisible` | `parametros_epidemiologicos` | Solo categorías transmisibles tienen parámetros |
| `trg_auditoria_inmutable` | `auditoria` | Bloquea `UPDATE` y `DELETE` |

## Pendiente a propósito

- **Índices nuevos** (`casos(id_estado, fecha_envio_panel)`, `asignaciones(id_tecnico) WHERE fecha_fin IS NULL`,
  `casos(vence_en)`): la guía pide justificar cada uno con un `EXPLAIN`, y esas consultas nacen en los módulos 4 y 8. Se
  agregan allí, con su `EXPLAIN` sobre datos de volumen.
- `TRUNCATE` sobre `auditoria` no lo bloquea el trigger de fila; la protección es no dar ese permiso al rol de la aplicación.
