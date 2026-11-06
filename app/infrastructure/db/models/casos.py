from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Date, DateTime, ForeignKeyConstraint, Index, PrimaryKeyConstraint, String, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.db.base import Base, flag_check, flag_column

if TYPE_CHECKING:
    from app.infrastructure.db.models.catalogos import Estado
    from app.infrastructure.db.models.cuentas import Tecnico
    from app.infrastructure.db.models.diagnostico import Diagnostico
    from app.infrastructure.db.models.epidemiologia import AlertaCaso
    from app.infrastructure.db.models.identificacion import IdentificacionPreliminar
    from app.infrastructure.db.models.territorio import CultivoEspecie


class Caso(Base):
    __tablename__ = "casos"
    __table_args__ = (
        PrimaryKeyConstraint("id_caso", name="pk_casos"),
        # FK compuesta: el par (cultivo, especie) debe existir en cultivo_especie.
        ForeignKeyConstraint(
            ["id_cultivo", "id_especie"],
            ["cultivo_especie.id_cultivo", "cultivo_especie.id_especie"],
            name="fk_cas_cul_esp",
        ),
        ForeignKeyConstraint(
            ["id_identificacion_preliminar"], ["identificacion_preliminar.id_identificacion"], name="fk_cas_iden"
        ),
        ForeignKeyConstraint(["id_estado"], ["estados.id_estado"], name="fk_cas_estado"),
        Index("ix_casos_cult_esp", "id_cultivo", "id_especie"),
        Index("ix_casos_identificacion", "id_identificacion_preliminar"),
        Index("ix_casos_estado", "id_estado"),
        flag_check("advertencia_inactividad_enviada"),
    )

    id_caso: Mapped[str] = mapped_column(String(12))
    id_cultivo: Mapped[str] = mapped_column(String(10), nullable=False)
    id_especie: Mapped[str] = mapped_column(String(5), nullable=False)
    # D-03: opcional; se llena cuando termina el análisis (el caso nace antes de tener identificación).
    id_identificacion_preliminar: Mapped[str | None] = mapped_column(String(12))
    id_estado: Mapped[str] = mapped_column(String(5), nullable=False)
    descripcion_zona_afectada: Mapped[str | None] = mapped_column(String(300))
    fecha_creacion_caso: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    advertencia_inactividad_enviada: Mapped[str] = flag_column(server_default="N")
    # D-06: ciclo de vida del caso (bandeja, cierre, retención e inactividad).
    fecha_envio_panel: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fecha_cierre: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    vence_en: Mapped[date | None] = mapped_column(Date)  # cierre + MESES_CONSERVACION
    ultima_actividad: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    cultivo_especie: Mapped["CultivoEspecie"] = relationship(back_populates="casos")
    identificacion_preliminar: Mapped["IdentificacionPreliminar | None"] = relationship(back_populates="casos")
    estado: Mapped["Estado"] = relationship(back_populates="casos")
    eventos: Mapped[list["Evento"]] = relationship(
        back_populates="caso", cascade="all, delete-orphan", order_by="Evento.fecha_evento"
    )
    diagnostico: Mapped["Diagnostico | None"] = relationship(back_populates="caso", cascade="all, delete-orphan")
    asignaciones: Mapped[list["Asignacion"]] = relationship(
        back_populates="caso", cascade="all, delete-orphan", order_by="Asignacion.fecha_asignacion"
    )
    alertas_vinculadas: Mapped[list["AlertaCaso"]] = relationship(back_populates="caso")


class Asignacion(Base):
    """Responsable de un caso (D-01). Reasignar cierra la vigente (`fecha_fin`) y crea otra.

    El autor del diagnóstico es el técnico de la asignación vigente; el responsable siempre es una cuenta de
    `tecnicos` (D-02: el líder no diagnostica con su rol).
    """

    __tablename__ = "asignaciones"
    __table_args__ = (
        PrimaryKeyConstraint("id_asignacion", name="pk_asignaciones"),
        ForeignKeyConstraint(["id_caso"], ["casos.id_caso"], name="fk_asg_caso"),
        ForeignKeyConstraint(["id_tecnico"], ["tecnicos.id_tecnico"], name="fk_asg_tecnico"),
        # Una sola asignación vigente por caso.
        Index("uq_asg_caso_vigente", "id_caso", unique=True, postgresql_where=text("fecha_fin IS NULL")),
    )

    id_asignacion: Mapped[str] = mapped_column(String(12))
    id_caso: Mapped[str] = mapped_column(String(12), nullable=False)
    id_tecnico: Mapped[str] = mapped_column(String(10), nullable=False)
    fecha_asignacion: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fecha_fin: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    caso: Mapped[Caso] = relationship(back_populates="asignaciones")
    tecnico: Mapped["Tecnico"] = relationship(back_populates="asignaciones")


class Evento(Base):
    __tablename__ = "eventos"
    __table_args__ = (
        PrimaryKeyConstraint("id_evento", name="pk_eventos"),
        ForeignKeyConstraint(["id_caso"], ["casos.id_caso"], name="fk_eve_caso"),
        Index("ix_eventos_caso", "id_caso", "fecha_evento"),
    )

    id_evento: Mapped[str] = mapped_column(String(12))
    id_caso: Mapped[str] = mapped_column(String(12), nullable=False)
    tipo_evento: Mapped[str] = mapped_column(String(30), nullable=False)
    descripcion_evento: Mapped[str] = mapped_column(String(500), nullable=False)
    fecha_evento: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # D-13: quién hizo el paso (LIDER, TECNICO, PRODUCTOR o SISTEMA) y su id; sin FK porque apunta a varias tablas.
    actor_tipo: Mapped[str | None] = mapped_column(String(15))
    actor_id: Mapped[str | None] = mapped_column(String(10))

    caso: Mapped[Caso] = relationship(back_populates="eventos")
