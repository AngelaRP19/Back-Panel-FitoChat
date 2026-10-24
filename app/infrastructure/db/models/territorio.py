from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from geoalchemy2 import Geometry
from sqlalchemy import (
    Computed,
    Date,
    ForeignKeyConstraint,
    Index,
    Numeric,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.db.base import Base

if TYPE_CHECKING:
    from app.infrastructure.db.models.arquitectura import MensajeWhatsapp
    from app.infrastructure.db.models.casos import Caso
    from app.infrastructure.db.models.catalogos import Especie


def ubicacion_column(latitud: str, longitud: str) -> Mapped[Any]:
    """geometry(Point, 4326) GENERADA a partir de longitud/latitud (columna STORED de PostgreSQL).

    spatial_index=False porque el índice GiST se declara con nombre explícito en __table_args__.
    """
    return mapped_column(
        Geometry("POINT", srid=4326, spatial_index=False),
        Computed(
            f"ST_SetSRID(ST_MakePoint({longitud}::double precision, {latitud}::double precision), 4326)",
            persisted=True,
        ),
    )


class Productor(Base):
    __tablename__ = "productores"
    __table_args__ = (
        PrimaryKeyConstraint("id_productor", name="pk_productores"),
        UniqueConstraint("telefono_productor", name="uq_prod_telefono"),
    )

    id_productor: Mapped[str] = mapped_column(String(10))
    nombre_productor: Mapped[str] = mapped_column(String(100), nullable=False)
    telefono_productor: Mapped[str] = mapped_column(String(15), nullable=False)
    estado_cuenta_productor: Mapped[str] = mapped_column(String(15), nullable=False)
    fecha_registro_productor: Mapped[date] = mapped_column(Date, nullable=False)

    fincas: Mapped[list["Finca"]] = relationship(back_populates="productor")
    mensajes_whatsapp: Mapped[list["MensajeWhatsapp"]] = relationship(back_populates="productor")


class Finca(Base):
    __tablename__ = "fincas"
    __table_args__ = (
        PrimaryKeyConstraint("id_finca", name="pk_fincas"),
        ForeignKeyConstraint(["id_productor"], ["productores.id_productor"], name="fk_fin_productor"),
        Index("ix_fincas_productor", "id_productor"),
        Index("ix_fincas_ubicacion", "ubicacion", postgresql_using="gist"),
    )

    id_finca: Mapped[str] = mapped_column(String(10))
    nombre_finca: Mapped[str] = mapped_column(String(100), nullable=False)
    vereda_sector_finca: Mapped[str] = mapped_column(String(100), nullable=False)
    latitud_finca: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    longitud_finca: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    id_productor: Mapped[str] = mapped_column(String(10), nullable=False)
    ubicacion: Mapped[Any] = ubicacion_column("latitud_finca", "longitud_finca")

    productor: Mapped[Productor] = relationship(back_populates="fincas")
    cultivos: Mapped[list["Cultivo"]] = relationship(back_populates="finca")


class Cultivo(Base):
    __tablename__ = "cultivos"
    __table_args__ = (
        PrimaryKeyConstraint("id_cultivo", name="pk_cultivos"),
        ForeignKeyConstraint(["id_finca"], ["fincas.id_finca"], name="fk_cul_finca"),
        Index("ix_cultivos_finca", "id_finca"),
        Index("ix_cultivos_ubicacion", "ubicacion", postgresql_using="gist"),
    )

    id_cultivo: Mapped[str] = mapped_column(String(10))
    nombre_cultivo: Mapped[str] = mapped_column(String(100), nullable=False)
    latitud_cultivo: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    longitud_cultivo: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    id_finca: Mapped[str] = mapped_column(String(10), nullable=False)
    ubicacion: Mapped[Any] = ubicacion_column("latitud_cultivo", "longitud_cultivo")

    finca: Mapped[Finca] = relationship(back_populates="cultivos")
    especies_asociadas: Mapped[list["CultivoEspecie"]] = relationship(
        back_populates="cultivo", cascade="all, delete-orphan"
    )


class CultivoEspecie(Base):
    """Relación N:M cultivo-especie. Es un objeto de asociación porque casos tiene FK compuesta hacia aquí."""

    __tablename__ = "cultivo_especie"
    __table_args__ = (
        PrimaryKeyConstraint("id_cultivo", "id_especie", name="pk_cultivo_especie"),
        ForeignKeyConstraint(["id_cultivo"], ["cultivos.id_cultivo"], name="fk_ce_cultivo"),
        ForeignKeyConstraint(["id_especie"], ["especies.id_especie"], name="fk_ce_especie"),
        Index("ix_cult_esp_especie", "id_especie"),
    )

    id_cultivo: Mapped[str] = mapped_column(String(10))
    id_especie: Mapped[str] = mapped_column(String(5))

    cultivo: Mapped[Cultivo] = relationship(back_populates="especies_asociadas")
    especie: Mapped["Especie"] = relationship(back_populates="cultivos_asociados")
    casos: Mapped[list["Caso"]] = relationship(back_populates="cultivo_especie")
