from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    ForeignKeyConstraint,
    Index,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.db.base import Base

if TYPE_CHECKING:
    from app.infrastructure.db.models.casos import Caso
    from app.infrastructure.db.models.catalogos import CategoriaFitosanitaria
    from app.infrastructure.db.models.cuentas import Tecnico


class Diagnostico(Base):
    __tablename__ = "diagnosticos"
    __table_args__ = (
        PrimaryKeyConstraint("id_diagnostico", name="pk_diagnosticos"),
        UniqueConstraint("id_caso", name="uq_dia_caso"),  # 1:1 con casos
        ForeignKeyConstraint(["id_caso"], ["casos.id_caso"], name="fk_dia_caso"),
        ForeignKeyConstraint(["id_tecnico"], ["tecnicos.id_tecnico"], name="fk_dia_tecnico"),
        ForeignKeyConstraint(
            ["id_categoria_confirmada"], ["categorias_fitosanitarias.id_categoria"], name="fk_dia_categoria"
        ),
        Index("ix_diag_tecnico", "id_tecnico"),
        Index("ix_diag_categoria", "id_categoria_confirmada"),
    )

    id_diagnostico: Mapped[str] = mapped_column(String(12))
    id_caso: Mapped[str] = mapped_column(String(12), nullable=False)
    id_tecnico: Mapped[str] = mapped_column(String(10), nullable=False)
    resultado_tecnico: Mapped[str] = mapped_column(String(20), nullable=False)
    id_categoria_confirmada: Mapped[str] = mapped_column(String(5), nullable=False)
    patologia_alternativa: Mapped[str | None] = mapped_column(String(100))
    motivo_visita: Mapped[str | None] = mapped_column(String(500))
    descripcion_diagnostico: Mapped[str] = mapped_column(String(1000), nullable=False)
    observaciones_diagnostico: Mapped[str | None] = mapped_column(String(1000))
    fecha_diagnostico: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    caso: Mapped["Caso"] = relationship(back_populates="diagnostico")
    tecnico: Mapped["Tecnico"] = relationship(back_populates="diagnosticos")
    categoria_confirmada: Mapped["CategoriaFitosanitaria"] = relationship(back_populates="diagnosticos_confirmados")
    respuestas: Mapped[list["Respuesta"]] = relationship(back_populates="diagnostico", cascade="all, delete-orphan")


class Pregunta(Base):
    __tablename__ = "preguntas"
    __table_args__ = (PrimaryKeyConstraint("id_pregunta", name="pk_preguntas"),)

    id_pregunta: Mapped[str] = mapped_column(String(10))
    texto_pregunta: Mapped[str] = mapped_column(String(300), nullable=False)

    respuestas: Mapped[list["Respuesta"]] = relationship(back_populates="pregunta")


class Respuesta(Base):
    __tablename__ = "respuestas"
    __table_args__ = (
        PrimaryKeyConstraint("id_respuesta", name="pk_respuestas"),
        ForeignKeyConstraint(["id_pregunta"], ["preguntas.id_pregunta"], name="fk_res_pregunta"),
        ForeignKeyConstraint(["id_diagnostico"], ["diagnosticos.id_diagnostico"], name="fk_res_diagnostico"),
    )

    id_respuesta: Mapped[str] = mapped_column(String(12))
    id_pregunta: Mapped[str] = mapped_column(String(10), nullable=False)
    id_diagnostico: Mapped[str] = mapped_column(String(12), nullable=False)
    texto_respuesta: Mapped[str] = mapped_column(String(500), nullable=False)
    fecha_respuesta: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    pregunta: Mapped[Pregunta] = relationship(back_populates="respuestas")
    diagnostico: Mapped[Diagnostico] = relationship(back_populates="respuestas")
    fotografias: Mapped[list["Fotografia"]] = relationship(back_populates="respuesta", cascade="all, delete-orphan")


class Fotografia(Base):
    __tablename__ = "fotografias"
    __table_args__ = (
        PrimaryKeyConstraint("id_fotografia", name="pk_fotografias"),
        ForeignKeyConstraint(["id_respuesta"], ["respuestas.id_respuesta"], name="fk_fot_res"),
        Index("ix_fotos_respuesta", "id_respuesta"),
    )

    id_fotografia: Mapped[str] = mapped_column(String(12))
    id_respuesta: Mapped[str] = mapped_column(String(12), nullable=False)
    tipo_evidencia: Mapped[str] = mapped_column(String(20), nullable=False)
    # CLAVE del objeto en S3 (ej. evidencias/2026/10/FOT-00000001.webp); nunca la imagen ni una URL fija.
    # Las URLs se generan prefirmadas al momento de servirlas.
    ruta_fotografia: Mapped[str] = mapped_column(String(500), nullable=False)
    fecha_envio_fotografia: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    respuesta: Mapped[Respuesta] = relationship(back_populates="fotografias")
