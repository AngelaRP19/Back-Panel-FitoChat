from typing import TYPE_CHECKING

from sqlalchemy import PrimaryKeyConstraint, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.db.base import Base, flag_check, flag_column

if TYPE_CHECKING:
    from app.infrastructure.db.models.arquitectura import ConocimientoEmbedding
    from app.infrastructure.db.models.casos import Caso
    from app.infrastructure.db.models.diagnostico import Diagnostico
    from app.infrastructure.db.models.epidemiologia import Alerta, ParametroEpidemiologico
    from app.infrastructure.db.models.identificacion import CoincidenciaCategoria
    from app.infrastructure.db.models.territorio import CultivoEspecie


class CategoriaFitosanitaria(Base):
    __tablename__ = "categorias_fitosanitarias"
    __table_args__ = (
        PrimaryKeyConstraint("id_categoria", name="pk_categorias"),
        UniqueConstraint("nombre_categoria", name="uq_cat_nombre"),
        flag_check("es_transmisible"),
    )

    id_categoria: Mapped[str] = mapped_column(String(5))
    nombre_categoria: Mapped[str] = mapped_column(String(50), nullable=False)
    es_transmisible: Mapped[str] = flag_column()

    coincidencias: Mapped[list["CoincidenciaCategoria"]] = relationship(back_populates="categoria")
    diagnosticos_confirmados: Mapped[list["Diagnostico"]] = relationship(back_populates="categoria_confirmada")
    parametro_epidemiologico: Mapped["ParametroEpidemiologico | None"] = relationship(back_populates="categoria")
    alertas: Mapped[list["Alerta"]] = relationship(back_populates="categoria")
    conocimientos: Mapped[list["ConocimientoEmbedding"]] = relationship(back_populates="categoria")


class Estado(Base):
    __tablename__ = "estados"
    __table_args__ = (
        PrimaryKeyConstraint("id_estado", name="pk_estados"),
        UniqueConstraint("nombre_estado", name="uq_est_nombre"),
    )

    id_estado: Mapped[str] = mapped_column(String(5))
    nombre_estado: Mapped[str] = mapped_column(String(50), nullable=False)
    descripcion_estado: Mapped[str] = mapped_column(String(200), nullable=False)

    casos: Mapped[list["Caso"]] = relationship(back_populates="estado")


class Especie(Base):
    __tablename__ = "especies"
    __table_args__ = (
        PrimaryKeyConstraint("id_especie", name="pk_especies"),
        UniqueConstraint("nombre_especie", name="uq_esp_nombre"),
    )

    id_especie: Mapped[str] = mapped_column(String(5))
    nombre_especie: Mapped[str] = mapped_column(String(50), nullable=False)

    cultivos_asociados: Mapped[list["CultivoEspecie"]] = relationship(back_populates="especie")
