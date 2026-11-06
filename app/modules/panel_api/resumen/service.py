"""Inicio del panel (guía §10.6, módulo 7): indicadores ligeros del líder y del técnico.

El líder ve el panorama de todo el sistema; el técnico, solo contadores de lo suyo. Los números salen de las mismas
fuentes que la bandeja (`vw_casos_detalle`) y la lista de alertas, para que los contadores coincidan.
"""

from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.paginacion import ParametrosPagina
from app.core.catalogos_api import CATEGORIAS, ESTADOS_CASO, CategoriaFito, EstadoAlerta, EstadoCaso, normalizar_codigo
from app.core.codigos import codigo_alerta, codigo_caso, numero_de_id
from app.core.roles import Rol
from app.core.security_cognito import UsuarioActual
from app.modules.panel_api.alertas import repository as alertas_repo
from app.modules.panel_api.resumen import repository as repo
from app.modules.panel_api.resumen.schemas import (
    AlertaDestacada,
    AlertaReciente,
    CargaTecnico,
    CasoPendiente,
    CasosPorCategoria,
    CategoriaTotal,
    CerradosMes,
    DiagnosticoReciente,
    InicioLider,
    InicioTecnico,
    KpisLider,
    KpisTecnico,
    Periodo,
    PorAtender,
    ResumenAlertas,
)


def _cerrados(k: repo.Kpis) -> CerradosMes:
    return CerradosMes(
        total=k.cerrados,
        confirmados=k.confirmados,
        otra_patologia=k.otra_patologia,
        no_identificado=k.no_identificado,
        requiere_visita=k.requiere_visita,
    )


class ResumenService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _ahora(self) -> datetime:
        ahora = await self.session.scalar(text("SELECT fn_ahora()"))
        assert isinstance(ahora, datetime)
        return ahora

    async def inicio(self, actor: UsuarioActual) -> InicioLider | InicioTecnico:
        if actor.rol is Rol.LIDER_TECNICO:
            return await self._inicio_lider()
        return await self._inicio_tecnico(actor)

    async def _inicio_lider(self) -> InicioLider:
        k = await repo.kpis(self.session, None)
        antiguos = await repo.sin_diagnostico_antiguos(self.session)
        recientes, _, conteos = await alertas_repo.listar(
            self.session, ParametrosPagina(1, repo.LIMITE_ALERTAS_RECIENTES), None
        )
        generadas, _, _ = await alertas_repo.listar(self.session, ParametrosPagina(1, 1), EstadoAlerta.GENERADA.value)
        return InicioLider(
            generado_en=await self._ahora(),
            kpis=KpisLider(
                pendientes_revision=k.pendientes,
                pendientes_mas_48h=k.pendientes_mas_48h,
                en_revision=k.en_revision,
                cerrados_mes=_cerrados(k),
                proximos_a_vencer=k.proximos_a_vencer,
                dias_aviso_vencimiento=repo.DIAS_AVISO_VENCIMIENTO,
            ),
            pendientes_antiguos=[
                CasoPendiente(
                    id=p.id,
                    codigo=codigo_caso(p.id),
                    numero=numero_de_id(p.id),
                    productor_nombre=p.nombre_productor,
                    enviado_a_panel_en=p.enviado,
                    tecnico_asignado_id=p.id_tecnico,
                )
                for p in antiguos
            ],
            pendientes_sin_tecnico=k.pendientes,
            alertas=ResumenAlertas(
                generadas_sin_revisar=conteos.get(EstadoAlerta.GENERADA.value, 0),
                destacada=AlertaDestacada(
                    categoria=CATEGORIAS.a_codigo(generadas[0].nombre_categoria),
                    vereda=normalizar_codigo(generadas[0].vereda) if generadas[0].vereda else None,
                )
                if generadas
                else None,
                recientes=[
                    AlertaReciente(
                        id=a.id,
                        codigo=codigo_alerta(a.id),
                        estado=EstadoAlerta(a.estado),
                        categoria=CATEGORIAS.a_codigo(a.nombre_categoria),
                        vereda=normalizar_codigo(a.vereda) if a.vereda else None,
                        num_casos=a.num_casos,
                        productores_notificados=a.notificados
                        if a.estado in (EstadoAlerta.COMUNICADA.value, EstadoAlerta.CORREGIDA.value)
                        else None,
                        generada_en=a.generada_en,
                    )
                    for a in recientes
                ],
            ),
            carga_tecnicos=[
                CargaTecnico(tecnico_id=t.id, nombre=t.nombre, activos=t.activos, en_revision=t.en_revision)
                for t in await repo.carga_de_tecnicos(self.session)
            ],
        )

    async def _inicio_tecnico(self, actor: UsuarioActual) -> InicioTecnico:
        k = await repo.kpis(self.session, actor.id)
        ahora = await self._ahora()
        limite = timedelta(hours=repo.HORAS_ESPERA)
        atender = await repo.por_atender(self.session, actor.id)
        registrado = ESTADOS_CASO.a_nombre(EstadoCaso.REGISTRADO)
        return InicioTecnico(
            generado_en=ahora,
            nombre=await repo.nombre_de_tecnico(self.session, actor.id),
            lider_nombre=await repo.nombre_de_lider(self.session),
            kpis=KpisTecnico(
                en_revision=k.en_revision,
                en_revision_mas_48h=k.en_revision_mas_48h,
                registrados_sin_cerrar=[p.id for p in atender if p.nombre_estado == registrado],
                cerrados_mes=_cerrados(k),
                proximos_a_vencer=k.proximos_a_vencer,
                dias_aviso_vencimiento=repo.DIAS_AVISO_VENCIMIENTO,
            ),
            por_atender=[
                PorAtender(
                    id=p.id,
                    codigo=codigo_caso(p.id),
                    productor_nombre=p.nombre_productor,
                    estado=EstadoCaso(ESTADOS_CASO.a_codigo(p.nombre_estado)),
                    enviado_a_panel_en=p.enviado,
                    mas_48h=p.enviado is not None and ahora - p.enviado > limite,
                )
                for p in atender
            ],
            diagnosticos_recientes=[
                DiagnosticoReciente(
                    caso_id=d.id_caso,
                    codigo=codigo_caso(d.id_caso),
                    resultado_tecnico=d.resultado,
                    categoria=CATEGORIAS.a_codigo(d.categoria) if d.categoria else None,
                    registrado_en=d.fecha,
                )
                for d in await repo.diagnosticos_recientes(self.session, actor.id)
            ],
        )

    async def por_categoria(self, periodo: Periodo) -> CasosPorCategoria:
        cuenta = await repo.casos_por_categoria(self.session, periodo)
        items = sorted(
            (CategoriaTotal(categoria=c.value, total=cuenta.get(c.value, 0)) for c in CategoriaFito),
            key=lambda i: -i.total,  # estable: a igual total, el orden del catálogo
        )
        return CasosPorCategoria(periodo=periodo, total=sum(i.total for i in items), items=items)
