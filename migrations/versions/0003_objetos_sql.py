"""Objetos SQL del Avance 5 (guía §6.6): vistas, funciones, procedimientos y triggers.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-27

Los nombres son los del Avance 5. Los `sp_*` son PROCEDURE de PL/pgSQL y se invocan con CALL. Los errores de
negocio llevan un SQLSTATE propio para que la API los traduzca (ver app/infrastructure/db/procedimientos.py):

    AN001 transición de estado inválida     AN002 no es el técnico de la asignación vigente
    AN003 datos inválidos                   AN004 no encontrado
    AN005 la auditoría es inmutable

Contexto de la transacción (opcional, lo fija la aplicación con set_config(..., true)):
    anuc.actor_tipo / anuc.actor_id  quién actúa; los triggers lo anotan en `eventos` (D-13). Sin valor: SISTEMA.
    anuc.ahora                       reloj simulado para pruebas y datos de demostración.

El detalle de cada objeto está en docs/OBJETOS_SQL.md.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# ---------------------------------------------------------------------------------------------------------------
# Funciones
# ---------------------------------------------------------------------------------------------------------------

FUNCIONES = [
    # Reloj de la transacción: now() salvo que se haya simulado otro.
    """
    CREATE FUNCTION fn_ahora() RETURNS timestamptz
    LANGUAGE sql STABLE AS $$
        SELECT COALESCE(NULLIF(current_setting('anuc.ahora', true), '')::timestamptz, now())
    $$
    """,
    # Quién actúa en la transacción (lo fija la aplicación; sin valor, el tipo por defecto).
    """
    CREATE FUNCTION fn_actor_tipo(p_defecto varchar DEFAULT 'SISTEMA') RETURNS varchar
    LANGUAGE sql STABLE AS $$
        SELECT COALESCE(NULLIF(current_setting('anuc.actor_tipo', true), ''), p_defecto)
    $$
    """,
    """
    CREATE FUNCTION fn_actor_id() RETURNS varchar
    LANGUAGE sql STABLE AS $$
        SELECT NULLIF(current_setting('anuc.actor_id', true), '')
    $$
    """,
    # Resuelve el id de un estado por nombre: nunca se escribe el id de un catálogo en el código.
    """
    CREATE FUNCTION fn_id_estado(p_nombre varchar) RETURNS varchar
    LANGUAGE plpgsql STABLE AS $$
    DECLARE
        v_id varchar;
    BEGIN
        SELECT id_estado INTO v_id FROM estados WHERE nombre_estado = p_nombre;
        IF v_id IS NULL THEN
            RAISE EXCEPTION 'No existe el estado "%"', p_nombre USING ERRCODE = 'AN004';
        END IF;
        RETURN v_id;
    END
    $$
    """,
    # Distancia en kilómetros entre dos puntos (ST_Distance sobre geography, es decir, sobre el elipsoide).
    """
    CREATE FUNCTION fn_distancia_km(a geometry, b geometry) RETURNS double precision
    LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
        SELECT ST_Distance(a::geography, b::geography) / 1000.0
    $$
    """,
    # Los logs y la auditoría no guardan teléfonos completos: 573001234567 y 3001234567 quedan como ***4567.
    r"""
    CREATE FUNCTION fn_enmascarar_telefonos(p_texto text) RETURNS text
    LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
        SELECT regexp_replace(p_texto, '(?<![0-9])(57)?3[0-9]{5}([0-9]{4})(?![0-9])', '***\2', 'g')
    $$
    """,
]

# ---------------------------------------------------------------------------------------------------------------
# Vistas
# ---------------------------------------------------------------------------------------------------------------

VISTAS = [
    # Listados y detalle: una fila por caso, con el técnico de la asignación vigente y la categoría con más
    # coincidencia (empate: la de menor id).
    """
    CREATE VIEW vw_casos_detalle AS
    SELECT c.id_caso,
           c.id_estado, e.nombre_estado,
           c.fecha_creacion_caso, c.fecha_envio_panel, c.fecha_cierre, c.vence_en, c.ultima_actividad,
           c.advertencia_inactividad_enviada, c.descripcion_zona_afectada,
           cu.id_cultivo, cu.nombre_cultivo, cu.tipo_cultivo,
           f.id_finca, f.nombre_finca, f.vereda_sector_finca, f.latitud_finca, f.longitud_finca,
           f.ubicacion AS ubicacion_finca,
           p.id_productor, p.nombre_productor, p.telefono_productor,
           s.id_especie, s.nombre_especie,
           c.id_identificacion_preliminar, ip.tipo_resultado AS resultado_analisis, ip.fecha_analisis,
           cp.id_categoria AS id_categoria_principal,
           cp.nombre_categoria AS nombre_categoria_principal,
           cp.porcentaje_coincidencia AS porcentaje_principal,
           a.id_tecnico, t.nombre_tecnico, a.fecha_asignacion,
           d.resultado_tecnico, d.id_categoria_confirmada,
           cc.nombre_categoria AS nombre_categoria_confirmada, d.fecha_diagnostico
    FROM casos c
    JOIN estados e ON e.id_estado = c.id_estado
    JOIN cultivos cu ON cu.id_cultivo = c.id_cultivo
    JOIN fincas f ON f.id_finca = cu.id_finca
    JOIN productores p ON p.id_productor = f.id_productor
    JOIN especies s ON s.id_especie = c.id_especie
    LEFT JOIN identificacion_preliminar ip ON ip.id_identificacion = c.id_identificacion_preliminar
    LEFT JOIN LATERAL (
        SELECT k.id_categoria, cf.nombre_categoria, k.porcentaje_coincidencia
        FROM coincidencias_categoria k
        JOIN categorias_fitosanitarias cf ON cf.id_categoria = k.id_categoria
        WHERE k.id_identificacion = ip.id_identificacion
        ORDER BY k.porcentaje_coincidencia DESC, k.id_categoria
        LIMIT 1
    ) cp ON TRUE
    LEFT JOIN asignaciones a ON a.id_caso = c.id_caso AND a.fecha_fin IS NULL
    LEFT JOIN tecnicos t ON t.id_tecnico = a.id_tecnico
    LEFT JOIN diagnosticos d ON d.id_caso = c.id_caso
    LEFT JOIN categorias_fitosanitarias cc ON cc.id_categoria = d.id_categoria_confirmada
    """,
    # Bandeja de pendientes: casos enviados al panel que todavía no tienen técnico.
    """
    CREATE VIEW vw_casos_pendientes_revision AS
    SELECT v.*,
           EXTRACT(EPOCH FROM (now() - v.fecha_envio_panel)) / 3600.0 AS horas_en_espera
    FROM vw_casos_detalle v
    WHERE v.nombre_estado = 'Pendiente de revisión'
    """,
    # Solo los diagnósticos registrados (resultado no nulo): la fila vacía no es un diagnóstico.
    """
    CREATE VIEW vw_diagnosticos_detalle AS
    SELECT d.id_diagnostico, d.id_caso, d.resultado_tecnico,
           d.id_categoria_confirmada, cf.nombre_categoria AS nombre_categoria_confirmada, cf.es_transmisible,
           d.patologia_alternativa, d.motivo_visita, d.descripcion_diagnostico, d.observaciones_diagnostico,
           d.fecha_diagnostico,
           a.id_tecnico AS id_tecnico_autor, t.nombre_tecnico AS nombre_tecnico_autor
    FROM diagnosticos d
    LEFT JOIN categorias_fitosanitarias cf ON cf.id_categoria = d.id_categoria_confirmada
    LEFT JOIN asignaciones a ON a.id_caso = d.id_caso AND a.fecha_fin IS NULL
    LEFT JOIN tecnicos t ON t.id_tecnico = a.id_tecnico
    WHERE d.resultado_tecnico IS NOT NULL
    """,
    # Entrada de la evaluación epidemiológica: categoría determinada por el técnico (CONFIRMADO u OTRA_PATOLOGIA)
    # y transmisible. Los diagnósticos vacíos, NO_IDENTIFICADO y REQUIERE_VISITA no cuentan.
    """
    CREATE VIEW vw_casos_confirmados_transmisibles AS
    SELECT d.id_caso, d.id_categoria_confirmada AS id_categoria, cf.nombre_categoria,
           d.resultado_tecnico, d.fecha_diagnostico,
           f.id_finca, f.vereda_sector_finca, f.id_productor, f.ubicacion AS ubicacion_finca
    FROM diagnosticos d
    JOIN categorias_fitosanitarias cf
        ON cf.id_categoria = d.id_categoria_confirmada AND cf.es_transmisible = 'S'
    JOIN casos c ON c.id_caso = d.id_caso
    JOIN cultivos cu ON cu.id_cultivo = c.id_cultivo
    JOIN fincas f ON f.id_finca = cu.id_finca
    WHERE d.resultado_tecnico IN ('CONFIRMADO', 'OTRA_PATOLOGIA') AND d.fecha_diagnostico IS NOT NULL
    """,
    # Jobs de 48 h y 72 h: solo los casos que aún los maneja el chatbot (Caso creado y En análisis).
    """
    CREATE VIEW vw_ultima_actividad_caso AS
    SELECT c.id_caso, c.id_estado, e.nombre_estado, c.ultima_actividad,
           EXTRACT(EPOCH FROM (now() - c.ultima_actividad)) / 3600.0 AS horas_inactivo,
           c.advertencia_inactividad_enviada,
           f.id_productor, p.telefono_productor
    FROM casos c
    JOIN estados e ON e.id_estado = c.id_estado
    JOIN cultivos cu ON cu.id_cultivo = c.id_cultivo
    JOIN fincas f ON f.id_finca = cu.id_finca
    JOIN productores p ON p.id_productor = f.id_productor
    WHERE e.nombre_estado IN ('Caso creado', 'En análisis')
    """,
    """
    CREATE VIEW vw_auditoria_detalle AS
    SELECT a.id_auditoria, a.fecha_auditoria, a.origen,
           COALESCE(a.id_lider_tecnico, a.id_tecnico) AS id_autor,
           CASE a.origen
               WHEN 'LIDER' THEN l.nombre_lider_tecnico
               WHEN 'TECNICO' THEN t.nombre_tecnico
               ELSE 'Sistema'
           END AS nombre_autor,
           a.accion, a.entidad_afectada, a.descripcion, a.valor_anterior, a.valor_nuevo, a.resultado
    FROM auditoria a
    LEFT JOIN lideres_tecnicos l ON l.id_lider_tecnico = a.id_lider_tecnico
    LEFT JOIN tecnicos t ON t.id_tecnico = a.id_tecnico
    """,
]

# ---------------------------------------------------------------------------------------------------------------
# Funciones de trigger y triggers
# ---------------------------------------------------------------------------------------------------------------

FUNCIONES_TRIGGER = [
    # Un caso nuevo anota CASO_CREADO. Actor: el que fije la aplicación o, por defecto, el productor dueño del cultivo.
    """
    CREATE FUNCTION fn_trg_casos_creacion_evento() RETURNS trigger
    LANGUAGE plpgsql AS $$
    DECLARE
        v_actor_tipo varchar := fn_actor_tipo(NULL);
        v_actor_id varchar := fn_actor_id();
    BEGIN
        IF v_actor_tipo IS NULL THEN  -- sin actor en el contexto: lo inicia el productor dueño del cultivo
            v_actor_tipo := 'PRODUCTOR';
            SELECT f.id_productor INTO v_actor_id
            FROM cultivos cu JOIN fincas f ON f.id_finca = cu.id_finca
            WHERE cu.id_cultivo = NEW.id_cultivo;
        END IF;
        CALL sp_registrar_evento(
            NEW.id_caso, 'CASO_CREADO', 'El productor inició el caso por WhatsApp.',
            v_actor_tipo, v_actor_id, NEW.fecha_creacion_caso
        );
        RETURN NULL;
    END
    $$
    """,
    # Cada cambio de estado anota su evento. «Caso creado» y «Registrado» no: ya los anotan la creación del caso
    # y el diagnóstico (trg_diag_evento). Reasignar no cambia el estado: ese evento lo registra el servicio.
    """
    CREATE FUNCTION fn_trg_casos_estado_evento() RETURNS trigger
    LANGUAGE plpgsql AS $$
    DECLARE
        v_nombre varchar;
        v_tipo varchar;
        v_descripcion varchar;
    BEGIN
        SELECT nombre_estado INTO v_nombre FROM estados WHERE id_estado = NEW.id_estado;
        CASE v_nombre
            WHEN 'En análisis' THEN
                v_tipo := 'ANALISIS_CHATBOT';
                v_descripcion := 'El chatbot analiza las fotos y el cuestionario.';
            WHEN 'Pendiente de revisión' THEN
                v_tipo := 'ENVIADO_A_REVISION';
                v_descripcion := 'El caso se envió al panel para revisión.';
            WHEN 'En revisión' THEN
                v_tipo := 'TECNICO_ASIGNADO';
                v_descripcion := 'Se asignó un técnico al caso.';
            WHEN 'Cerrado' THEN
                v_tipo := 'CASO_CERRADO';
                v_descripcion := 'El caso se cerró.';
            WHEN 'Descartado' THEN
                v_tipo := 'CASO_DESCARTADO';
                v_descripcion := 'El caso se descartó.';
            ELSE
                RETURN NULL;
        END CASE;
        CALL sp_registrar_evento(NEW.id_caso, v_tipo, v_descripcion, fn_actor_tipo(), fn_actor_id(), fn_ahora());
        RETURN NULL;
    END
    $$
    """,
    # El diagnóstico "nace" cuando el resultado pasa de vacío a lleno (no al crear la fila vacía).
    """
    CREATE FUNCTION fn_trg_diag_evento() RETURNS trigger
    LANGUAGE plpgsql AS $$
    DECLARE
        v_actor_tipo varchar := fn_actor_tipo(NULL);
        v_actor_id varchar := fn_actor_id();
    BEGIN
        IF v_actor_tipo IS NULL THEN  -- sin actor en el contexto: el técnico de la asignación vigente
            v_actor_tipo := 'TECNICO';
            SELECT id_tecnico INTO v_actor_id FROM asignaciones WHERE id_caso = NEW.id_caso AND fecha_fin IS NULL;
        END IF;
        CALL sp_registrar_evento(
            NEW.id_caso, 'DIAGNOSTICO_REGISTRADO', 'El técnico registró el diagnóstico.',
            v_actor_tipo, v_actor_id, fn_ahora()
        );
        RETURN NULL;
    END
    $$
    """,
    # Solo el técnico ACTIVO de la asignación vigente puede completar el diagnóstico.
    """
    CREATE FUNCTION fn_trg_diag_tecnico_activo() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1
            FROM asignaciones a JOIN tecnicos t ON t.id_tecnico = a.id_tecnico
            WHERE a.id_caso = NEW.id_caso AND a.fecha_fin IS NULL AND t.estado_cuenta_tecnico = 'ACTIVO'
        ) THEN
            RAISE EXCEPTION 'Solo el técnico activo de la asignación vigente puede completar el diagnóstico del caso %',
                NEW.id_caso USING ERRCODE = 'AN002';
        END IF;
        RETURN NEW;
    END
    $$
    """,
    """
    CREATE FUNCTION fn_trg_param_transmisible() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM categorias_fitosanitarias WHERE id_categoria = NEW.id_categoria AND es_transmisible = 'S'
        ) THEN
            RAISE EXCEPTION 'Solo las categorías transmisibles pueden tener parámetros epidemiológicos'
                USING ERRCODE = 'AN003';
        END IF;
        RETURN NEW;
    END
    $$
    """,
    """
    CREATE FUNCTION fn_trg_auditoria_inmutable() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        RAISE EXCEPTION 'El registro de auditoría es inmutable: no se puede modificar ni borrar'
            USING ERRCODE = 'AN005';
    END
    $$
    """,
]

TRIGGERS = [
    "CREATE TRIGGER trg_casos_creacion_evento AFTER INSERT ON casos "
    "FOR EACH ROW EXECUTE FUNCTION fn_trg_casos_creacion_evento()",
    "CREATE TRIGGER trg_casos_estado_evento AFTER UPDATE OF id_estado ON casos "
    "FOR EACH ROW WHEN (OLD.id_estado IS DISTINCT FROM NEW.id_estado) EXECUTE FUNCTION fn_trg_casos_estado_evento()",
    "CREATE TRIGGER trg_diag_evento AFTER UPDATE OF resultado_tecnico ON diagnosticos "
    "FOR EACH ROW WHEN (OLD.resultado_tecnico IS NULL AND NEW.resultado_tecnico IS NOT NULL) "
    "EXECUTE FUNCTION fn_trg_diag_evento()",
    "CREATE TRIGGER trg_diag_tecnico_activo BEFORE INSERT OR UPDATE ON diagnosticos "
    "FOR EACH ROW WHEN (NEW.resultado_tecnico IS NOT NULL) EXECUTE FUNCTION fn_trg_diag_tecnico_activo()",
    "CREATE TRIGGER trg_param_transmisible BEFORE INSERT OR UPDATE OF id_categoria ON parametros_epidemiologicos "
    "FOR EACH ROW EXECUTE FUNCTION fn_trg_param_transmisible()",
    "CREATE TRIGGER trg_auditoria_inmutable BEFORE UPDATE OR DELETE ON auditoria "
    "FOR EACH ROW EXECUTE FUNCTION fn_trg_auditoria_inmutable()",
]

# ---------------------------------------------------------------------------------------------------------------
# Procedimientos
# ---------------------------------------------------------------------------------------------------------------

PROCEDIMIENTOS = [
    # Línea de tiempo del caso. Valida el tipo de evento y el tipo de actor (D-13).
    """
    CREATE PROCEDURE sp_registrar_evento(
        p_id_caso varchar, p_tipo_evento varchar, p_descripcion varchar,
        p_actor_tipo varchar DEFAULT NULL, p_actor_id varchar DEFAULT NULL,
        p_fecha timestamptz DEFAULT fn_ahora()
    )
    LANGUAGE plpgsql AS $$
    BEGIN
        IF p_tipo_evento NOT IN (
            'CASO_CREADO', 'ANALISIS_CHATBOT', 'ENVIADO_A_REVISION', 'TECNICO_ASIGNADO',
            'TECNICO_REASIGNADO', 'DIAGNOSTICO_REGISTRADO', 'CASO_CERRADO', 'CASO_DESCARTADO'
        ) THEN
            RAISE EXCEPTION 'Tipo de evento desconocido: %', p_tipo_evento USING ERRCODE = 'AN003';
        END IF;
        IF p_actor_tipo IS NOT NULL AND p_actor_tipo NOT IN ('LIDER', 'TECNICO', 'PRODUCTOR', 'SISTEMA') THEN
            RAISE EXCEPTION 'Tipo de actor desconocido: %', p_actor_tipo USING ERRCODE = 'AN003';
        END IF;
        INSERT INTO eventos (id_evento, id_caso, tipo_evento, descripcion_evento, fecha_evento, actor_tipo, actor_id)
        VALUES (
            fn_generar_id('seq_eventos', 'EVE', '-', 12), p_id_caso, p_tipo_evento, p_descripcion, p_fecha,
            p_actor_tipo, p_actor_id
        );
    END
    $$
    """,
    # Auditoría. El autor se guarda según el origen (D-09); los teléfonos completos se enmascaran.
    """
    CREATE PROCEDURE sp_registrar_auditoria(
        p_origen varchar, p_id_actor varchar, p_accion varchar, p_entidad_afectada varchar,
        p_descripcion varchar, p_valor_anterior varchar, p_valor_nuevo varchar, p_resultado varchar,
        p_fecha timestamptz DEFAULT fn_ahora()
    )
    LANGUAGE plpgsql AS $$
    BEGIN
        IF p_origen NOT IN ('LIDER', 'TECNICO', 'SISTEMA') THEN
            RAISE EXCEPTION 'Origen de auditoría desconocido: %', p_origen USING ERRCODE = 'AN003';
        END IF;
        INSERT INTO auditoria (
            id_auditoria, id_lider_tecnico, id_tecnico, origen, accion, entidad_afectada, descripcion,
            valor_anterior, valor_nuevo, resultado, fecha_auditoria
        )
        VALUES (
            fn_generar_id('seq_auditoria', 'AUD', '-', 12),
            CASE WHEN p_origen = 'LIDER' THEN p_id_actor END,
            CASE WHEN p_origen = 'TECNICO' THEN p_id_actor END,
            p_origen, p_accion, p_entidad_afectada,
            fn_enmascarar_telefonos(p_descripcion),
            fn_enmascarar_telefonos(p_valor_anterior),
            fn_enmascarar_telefonos(p_valor_nuevo),
            p_resultado, p_fecha
        );
    END
    $$
    """,
    # Completa la fila (vacía) del diagnóstico y pasa el caso a «Registrado». Exige «En revisión» y que quien
    # registra sea el técnico de la asignación vigente. Evento y auditoría van en la misma transacción.
    """
    CREATE PROCEDURE sp_registrar_diagnostico(
        p_id_caso varchar, p_id_tecnico varchar, p_resultado_tecnico varchar,
        p_id_categoria varchar DEFAULT NULL, p_motivo_visita varchar DEFAULT NULL,
        p_descripcion varchar DEFAULT NULL, p_fecha timestamptz DEFAULT fn_ahora()
    )
    LANGUAGE plpgsql AS $$
    DECLARE
        v_estado varchar;
        v_asignado varchar;
        v_id_diagnostico varchar;
    BEGIN
        SELECT id_estado INTO v_estado FROM casos WHERE id_caso = p_id_caso FOR UPDATE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'El caso % no existe', p_id_caso USING ERRCODE = 'AN004';
        END IF;
        IF v_estado <> fn_id_estado('En revisión') THEN
            RAISE EXCEPTION 'El caso % no está En revisión', p_id_caso USING ERRCODE = 'AN001';
        END IF;
        SELECT id_tecnico INTO v_asignado FROM asignaciones WHERE id_caso = p_id_caso AND fecha_fin IS NULL;
        IF v_asignado IS NULL OR v_asignado <> p_id_tecnico THEN
            RAISE EXCEPTION 'Solo el técnico de la asignación vigente puede diagnosticar el caso %', p_id_caso
                USING ERRCODE = 'AN002';
        END IF;
        IF p_resultado_tecnico NOT IN ('CONFIRMADO', 'OTRA_PATOLOGIA', 'NO_IDENTIFICADO', 'REQUIERE_VISITA') THEN
            RAISE EXCEPTION 'Resultado técnico desconocido: %', p_resultado_tecnico USING ERRCODE = 'AN003';
        END IF;
        IF p_resultado_tecnico IN ('CONFIRMADO', 'OTRA_PATOLOGIA') AND p_id_categoria IS NULL THEN
            RAISE EXCEPTION 'El resultado % exige la categoría', p_resultado_tecnico USING ERRCODE = 'AN003';
        END IF;
        IF p_resultado_tecnico = 'REQUIERE_VISITA' AND btrim(COALESCE(p_motivo_visita, '')) = '' THEN
            RAISE EXCEPTION 'El resultado REQUIERE_VISITA exige el motivo de la visita' USING ERRCODE = 'AN003';
        END IF;

        PERFORM set_config('anuc.ahora', p_fecha::text, true);
        PERFORM set_config('anuc.actor_tipo', 'TECNICO', true);
        PERFORM set_config('anuc.actor_id', p_id_tecnico, true);

        SELECT id_diagnostico INTO v_id_diagnostico FROM diagnosticos WHERE id_caso = p_id_caso FOR UPDATE;
        IF NOT FOUND THEN  -- la fila vacía se crea al iniciar el caso; si faltara, se crea aquí
            INSERT INTO diagnosticos (id_diagnostico, id_caso)
            VALUES (fn_generar_id('seq_diagnosticos', 'DIA', '-', 12), p_id_caso)
            RETURNING id_diagnostico INTO v_id_diagnostico;
        END IF;
        UPDATE diagnosticos
        SET resultado_tecnico = p_resultado_tecnico,
            id_categoria_confirmada = p_id_categoria,
            motivo_visita = p_motivo_visita,
            descripcion_diagnostico = p_descripcion,
            fecha_diagnostico = p_fecha
        WHERE id_diagnostico = v_id_diagnostico;
        UPDATE casos SET id_estado = fn_id_estado('Registrado') WHERE id_caso = p_id_caso;

        CALL sp_registrar_auditoria(
            'TECNICO', p_id_tecnico, 'DIAGNOSTICAR', 'diagnosticos',
            'Diagnóstico registrado en el caso ' || p_id_caso, NULL, p_resultado_tecnico, 'EXITO', p_fecha
        );
    END
    $$
    """,
    # Cierra un caso Registrado y fija el vencimiento de conservación: cierre + p_meses (en hora de Colombia).
    """
    CREATE PROCEDURE sp_cerrar_caso(
        p_id_caso varchar, p_actor_tipo varchar, p_actor_id varchar,
        p_meses_conservacion integer DEFAULT 6, p_fecha timestamptz DEFAULT fn_ahora()
    )
    LANGUAGE plpgsql AS $$
    DECLARE
        v_estado varchar;
        v_vence date;
    BEGIN
        IF p_actor_tipo NOT IN ('LIDER', 'TECNICO') THEN
            RAISE EXCEPTION 'Solo un líder o un técnico puede cerrar un caso' USING ERRCODE = 'AN003';
        END IF;
        SELECT id_estado INTO v_estado FROM casos WHERE id_caso = p_id_caso FOR UPDATE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'El caso % no existe', p_id_caso USING ERRCODE = 'AN004';
        END IF;
        IF v_estado <> fn_id_estado('Registrado') THEN
            RAISE EXCEPTION 'El caso % no está Registrado', p_id_caso USING ERRCODE = 'AN001';
        END IF;

        PERFORM set_config('anuc.ahora', p_fecha::text, true);
        PERFORM set_config('anuc.actor_tipo', p_actor_tipo, true);
        PERFORM set_config('anuc.actor_id', p_actor_id, true);

        v_vence := (timezone('America/Bogota', p_fecha)::date + make_interval(months => p_meses_conservacion))::date;
        UPDATE casos
        SET id_estado = fn_id_estado('Cerrado'), fecha_cierre = p_fecha, vence_en = v_vence
        WHERE id_caso = p_id_caso;

        CALL sp_registrar_auditoria(
            p_actor_tipo, p_actor_id, 'CERRAR_CASO', 'casos',
            'Caso ' || p_id_caso || ' cerrado; conservación hasta ' || v_vence, NULL, NULL, 'EXITO', p_fecha
        );
    END
    $$
    """,
    # Marca la advertencia de 48 h. Solo aplica a los casos que aún maneja el chatbot; repetirla no hace nada.
    """
    CREATE PROCEDURE sp_marcar_advertencia_enviada(p_id_caso varchar)
    LANGUAGE plpgsql AS $$
    DECLARE
        v_estado varchar;
    BEGIN
        SELECT id_estado INTO v_estado FROM casos WHERE id_caso = p_id_caso FOR UPDATE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'El caso % no existe', p_id_caso USING ERRCODE = 'AN004';
        END IF;
        IF v_estado NOT IN (fn_id_estado('Caso creado'), fn_id_estado('En análisis')) THEN
            RAISE EXCEPTION 'El caso % ya no está en manos del chatbot', p_id_caso USING ERRCODE = 'AN001';
        END IF;
        UPDATE casos SET advertencia_inactividad_enviada = 'S' WHERE id_caso = p_id_caso;
    END
    $$
    """,
    # Evalúa las alertas de una categoría (o de todas las transmisibles con parámetros si es NULL).
    #   Foco: caso confirmado de la categoría, dentro del intervalo, que todavía no pertenece a ninguna alerta de esa
    #   categoría (de cualquier estado, para que una alerta descartada no se vuelva a generar).
    #   Grupo: los casos del intervalo a distancia_km o menos del foco (el foco incluido).
    #   Si el grupo llega al mínimo de casos: se suma a la alerta abierta (GENERADA o EN_REVISION) con la que ya
    #   comparta casos, o se crea una alerta GENERADA con los parámetros vigentes. Es idempotente.
    """
    CREATE PROCEDURE sp_evaluar_alerta(p_id_categoria varchar DEFAULT NULL, p_fecha timestamptz DEFAULT fn_ahora())
    LANGUAGE plpgsql AS $$
    DECLARE
        v_param RECORD;
        v_foco RECORD;
        v_grupo varchar[];
        v_alerta varchar;
    BEGIN
        FOR v_param IN
            SELECT p.id_categoria, p.numero_minimo_casos, p.distancia_km, p.intervalo_dias
            FROM parametros_epidemiologicos p
            JOIN categorias_fitosanitarias c ON c.id_categoria = p.id_categoria
            WHERE c.es_transmisible = 'S' AND (p_id_categoria IS NULL OR p.id_categoria = p_id_categoria)
            ORDER BY p.id_categoria
        LOOP
            FOR v_foco IN
                SELECT v.id_caso, v.ubicacion_finca
                FROM vw_casos_confirmados_transmisibles v
                WHERE v.id_categoria = v_param.id_categoria
                  AND v.fecha_diagnostico BETWEEN p_fecha - make_interval(days => v_param.intervalo_dias) AND p_fecha
                ORDER BY v.fecha_diagnostico DESC, v.id_caso
            LOOP
                CONTINUE WHEN EXISTS (
                    SELECT 1 FROM alerta_casos ac JOIN alertas a ON a.id_alerta = ac.id_alerta
                    WHERE ac.id_caso = v_foco.id_caso AND a.id_categoria = v_param.id_categoria
                );
                SELECT array_agg(g.id_caso ORDER BY g.id_caso) INTO v_grupo
                FROM vw_casos_confirmados_transmisibles g
                WHERE g.id_categoria = v_param.id_categoria
                  AND g.fecha_diagnostico BETWEEN p_fecha - make_interval(days => v_param.intervalo_dias) AND p_fecha
                  AND fn_distancia_km(v_foco.ubicacion_finca, g.ubicacion_finca) <= v_param.distancia_km;
                CONTINUE WHEN COALESCE(cardinality(v_grupo), 0) < v_param.numero_minimo_casos;

                SELECT a.id_alerta INTO v_alerta
                FROM alertas a JOIN alerta_casos ac ON ac.id_alerta = a.id_alerta
                WHERE a.id_categoria = v_param.id_categoria
                  AND a.estado_alerta IN ('GENERADA', 'EN_REVISION')
                  AND ac.id_caso = ANY (v_grupo)
                ORDER BY a.fecha_generacion_alerta, a.id_alerta
                LIMIT 1;
                IF v_alerta IS NULL THEN
                    INSERT INTO alertas (
                        id_alerta, id_categoria, estado_alerta, fecha_generacion_alerta,
                        numero_minimo_casos, distancia_km, intervalo_dias
                    )
                    VALUES (
                        fn_generar_id('seq_alertas', 'ALR', '-', 10), v_param.id_categoria, 'GENERADA', p_fecha,
                        v_param.numero_minimo_casos, v_param.distancia_km, v_param.intervalo_dias
                    )
                    RETURNING id_alerta INTO v_alerta;
                END IF;
                INSERT INTO alerta_casos (id_alerta, id_caso)
                SELECT v_alerta, unnest(v_grupo)
                ON CONFLICT DO NOTHING;
            END LOOP;
        END LOOP;
    END
    $$
    """,
]

TABLAS_DE_TRIGGERS = [
    ("trg_casos_creacion_evento", "casos"),
    ("trg_casos_estado_evento", "casos"),
    ("trg_diag_evento", "diagnosticos"),
    ("trg_diag_tecnico_activo", "diagnosticos"),
    ("trg_param_transmisible", "parametros_epidemiologicos"),
    ("trg_auditoria_inmutable", "auditoria"),
]


def _nombre(sentencia: str, palabra: str) -> str:
    return sentencia.split(f"CREATE {palabra} ", 1)[1].split("(", 1)[0].split()[0]


def upgrade() -> None:
    # Los cuerpos de PL/pgSQL se resuelven al ejecutarse, así que el orden entre funciones y procedimientos es libre.
    for sentencia in FUNCIONES + PROCEDIMIENTOS + FUNCIONES_TRIGGER:
        op.execute(sentencia)
    for sentencia in VISTAS:
        op.execute(sentencia)
    for sentencia in TRIGGERS:
        op.execute(sentencia)


def downgrade() -> None:
    for trigger, tabla in reversed(TABLAS_DE_TRIGGERS):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {tabla}")
    for sentencia in reversed(VISTAS):
        op.execute(f"DROP VIEW IF EXISTS {_nombre(sentencia, 'VIEW')}")
    for sentencia in reversed(FUNCIONES_TRIGGER):
        op.execute(f"DROP FUNCTION IF EXISTS {_nombre(sentencia, 'FUNCTION')}()")
    for sentencia in reversed(PROCEDIMIENTOS):
        op.execute(f"DROP PROCEDURE IF EXISTS {_nombre(sentencia, 'PROCEDURE')}")
    for sentencia in reversed(FUNCIONES):
        op.execute(f"DROP FUNCTION IF EXISTS {_nombre(sentencia, 'FUNCTION')}")
