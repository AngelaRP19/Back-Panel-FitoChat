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
    from app.infrastructure.db.models.catalogos import CategoriaFitosanitaria


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
    __tablename__ = "alertas"
    __table_args__ = (
        PrimaryKeyConstraint("id_alerta", name="pk_alertas"),
        ForeignKeyConstraint(["id_categoria"], ["categorias_fitosanitarias.id_categoria"], name="fk_ale_categoria"),
        Index("ix_alertas_categoria", "id_categoria"),
    )

    id_alerta: Mapped[str] = mapped_column(String(10))
    id_categoria: Mapped[str] = mapped_column(String(5), nullable=False)
    estado_alerta: Mapped[str] = mapped_column(String(15), nullable=False)
    fecha_generacion_alerta: Mapped[date] = mapped_column(Date, nullable=False)
    fecha_comunicacion_alerta: Mapped[date | None] = mapped_column(Date)

    categoria: Mapped["CategoriaFitosanitaria"] = relationship(back_populates="alertas")
