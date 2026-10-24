from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Numeric,
    PrimaryKeyConstraint,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.db.base import Base

if TYPE_CHECKING:
    from app.infrastructure.db.models.casos import Caso
    from app.infrastructure.db.models.catalogos import CategoriaFitosanitaria


class IdentificacionPreliminar(Base):
    __tablename__ = "identificacion_preliminar"
    __table_args__ = (PrimaryKeyConstraint("id_identificacion", name="pk_identificacion"),)

    id_identificacion: Mapped[str] = mapped_column(String(12))
    tipo_resultado: Mapped[str] = mapped_column(String(30), nullable=False)
    explicacion_resultado: Mapped[str] = mapped_column(String(2000), nullable=False)
    fecha_analisis: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    coincidencias: Mapped[list["CoincidenciaCategoria"]] = relationship(
        back_populates="identificacion", cascade="all, delete-orphan"
    )
    casos: Mapped[list["Caso"]] = relationship(back_populates="identificacion_preliminar")


class CoincidenciaCategoria(Base):
    """N:M identificación-categoría con el porcentaje de coincidencia de síntomas."""

    __tablename__ = "coincidencias_categoria"
    __table_args__ = (
        PrimaryKeyConstraint("id_identificacion", "id_categoria", name="pk_coincidencias"),
        ForeignKeyConstraint(
            ["id_identificacion"], ["identificacion_preliminar.id_identificacion"], name="fk_coi_identificacion"
        ),
        ForeignKeyConstraint(["id_categoria"], ["categorias_fitosanitarias.id_categoria"], name="fk_coi_categoria"),
        Index("ix_coinc_categoria", "id_categoria"),
        CheckConstraint("porcentaje_coincidencia BETWEEN 0 AND 100", name="porcentaje_rango"),
    )

    id_identificacion: Mapped[str] = mapped_column(String(12))
    id_categoria: Mapped[str] = mapped_column(String(5))
    porcentaje_coincidencia: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)

    identificacion: Mapped[IdentificacionPreliminar] = relationship(back_populates="coincidencias")
    categoria: Mapped["CategoriaFitosanitaria"] = relationship(back_populates="coincidencias")
