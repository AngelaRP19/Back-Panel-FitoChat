from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.db.base import Base

if TYPE_CHECKING:
    from app.infrastructure.db.models.casos import Caso
    from app.infrastructure.db.models.catalogos import CategoriaFitosanitaria
    from app.infrastructure.db.models.cuentas import LiderTecnico
    from app.infrastructure.db.models.territorio import Productor


class ParametroEpidemiologico(Base):
    __tablename__ = "parametros_epidemiologicos"
    __table_args__ = (
        PrimaryKeyConstraint("id_parametro", name="pk_parametros"),
        UniqueConstraint("id_categoria", name="uq_par_categoria"),  # 1:1 con la categoría
        ForeignKeyConstraint(["id_categoria"], ["categorias_fitosanitarias.id_categoria"], name="fk_par_categoria"),
        # NUMBER(3) en Oracle: además del mínimo pedido (>= 1) se conserva el tope de 3 dígitos.
        CheckConstraint("numero_minimo_casos BETWEEN 1 AND 999", name="numero_minimo_casos_rango"),
        CheckConstraint("intervalo_dias BETWEEN 1 AND 999", name="intervalo_dias_rango"),
    )

    id_parametro: Mapped[str] = mapped_column(String(10))
    id_categoria: Mapped[str] = mapped_column(String(5), nullable=False)
    numero_minimo_casos: Mapped[int] = mapped_column(Integer, nullable=False)
    distancia_km: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    intervalo_dias: Mapped[int] = mapped_column(Integer, nullable=False)
    fecha_actualizacion: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    categoria: Mapped["CategoriaFitosanitaria"] = relationship(back_populates="parametro_epidemiologico")


class Alerta(Base):
    """Alerta epidemiológica. Estados: GENERADA, EN_REVISION, CONFIRMADA, DESCARTADA, COMUNICADA y CORREGIDA (D-17)."""

    __tablename__ = "alertas"
    __table_args__ = (
        PrimaryKeyConstraint("id_alerta", name="pk_alertas"),
        ForeignKeyConstraint(["id_categoria"], ["categorias_fitosanitarias.id_categoria"], name="fk_ale_categoria"),
        ForeignKeyConstraint(["id_lider_gestion"], ["lideres_tecnicos.id_lider_tecnico"], name="fk_ale_lider_gestion"),
        ForeignKeyConstraint(
            ["id_lider_correccion"], ["lideres_tecnicos.id_lider_tecnico"], name="fk_ale_lider_correccion"
        ),
        Index("ix_alertas_categoria", "id_categoria"),
    )

    id_alerta: Mapped[str] = mapped_column(String(10))
    id_categoria: Mapped[str] = mapped_column(String(5), nullable=False)
    estado_alerta: Mapped[str] = mapped_column(String(15), nullable=False)
    # D-08: fecha y hora (antes solo fecha).
    fecha_generacion_alerta: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fecha_comunicacion_alerta: Mapped[date | None] = mapped_column(Date)
    # D-08: parámetros con que se detectó; no cambian aunque el líder edite los parámetros después.
    numero_minimo_casos: Mapped[int] = mapped_column(Integer, nullable=False)
    distancia_km: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    intervalo_dias: Mapped[int] = mapped_column(Integer, nullable=False)
    recomendacion: Mapped[str | None] = mapped_column(String(300))
    mensaje_enviado: Mapped[str | None] = mapped_column(String(1000))
    id_lider_gestion: Mapped[str | None] = mapped_column(String(10))  # quién la confirmó o descartó
    fecha_confirmacion: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    justificacion_descarte: Mapped[str | None] = mapped_column(String(500))
    # D-17: corrección de una alerta comunicada por error (una sola vez).
    mensaje_correccion: Mapped[str | None] = mapped_column(String(300))
    fecha_correccion: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    id_lider_correccion: Mapped[str | None] = mapped_column(String(10))

    categoria: Mapped["CategoriaFitosanitaria"] = relationship(back_populates="alertas")
    lider_gestion: Mapped["LiderTecnico | None"] = relationship(
        back_populates="alertas_gestionadas", foreign_keys=[id_lider_gestion]
    )
    lider_correccion: Mapped["LiderTecnico | None"] = relationship(
        back_populates="alertas_corregidas", foreign_keys=[id_lider_correccion]
    )
    casos: Mapped[list["AlertaCaso"]] = relationship(back_populates="alerta", cascade="all, delete-orphan")
    destinatarios: Mapped[list["AlertaDestinatario"]] = relationship(
        back_populates="alerta", cascade="all, delete-orphan"
    )


class AlertaCaso(Base):
    """N:M entre alertas y los casos que las originan (D-08)."""

    __tablename__ = "alerta_casos"
    __table_args__ = (
        PrimaryKeyConstraint("id_alerta", "id_caso", name="pk_alerta_casos"),
        ForeignKeyConstraint(["id_alerta"], ["alertas.id_alerta"], name="fk_alc_alerta"),
        ForeignKeyConstraint(["id_caso"], ["casos.id_caso"], name="fk_alc_caso"),
        Index("ix_alerta_casos_caso", "id_caso"),
    )

    id_alerta: Mapped[str] = mapped_column(String(10))
    id_caso: Mapped[str] = mapped_column(String(12))

    alerta: Mapped[Alerta] = relationship(back_populates="casos")
    caso: Mapped["Caso"] = relationship(back_populates="alertas_vinculadas")


class AlertaDestinatario(Base):
    """Resultado del envío de una alerta o de su corrección a cada productor (D-08 y D-17).

    Una fila por (alerta, productor, tipo de envío): un reintento no duplica el mensaje. En modo simulado el
    estado del envío es SIMULADO, nunca ENVIADO.
    """

    __tablename__ = "alerta_destinatarios"
    __table_args__ = (
        PrimaryKeyConstraint("id_alerta_destinatario", name="pk_alerta_destinatarios"),
        UniqueConstraint("id_alerta", "id_productor", "tipo_envio", name="uq_ade_envio"),
        ForeignKeyConstraint(["id_alerta"], ["alertas.id_alerta"], name="fk_ade_alerta"),
        ForeignKeyConstraint(["id_productor"], ["productores.id_productor"], name="fk_ade_productor"),
        CheckConstraint("tipo_envio IN ('ALERTA', 'CORRECCION')", name="tipo_envio_valido"),
    )

    id_alerta_destinatario: Mapped[str] = mapped_column(String(12))
    id_alerta: Mapped[str] = mapped_column(String(10), nullable=False)
    id_productor: Mapped[str] = mapped_column(String(10), nullable=False)
    tipo_envio: Mapped[str] = mapped_column(String(10), nullable=False)
    estado_envio: Mapped[str] = mapped_column(String(15), nullable=False)
    fecha_envio: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detalle_error: Mapped[str | None] = mapped_column(String(500))

    alerta: Mapped[Alerta] = relationship(back_populates="destinatarios")
    productor: Mapped["Productor"] = relationship(back_populates="alertas_recibidas")
