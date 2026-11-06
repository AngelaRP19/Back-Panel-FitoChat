"""Tablas de la arquitectura que no están en el modelo físico original."""

from datetime import datetime
from typing import TYPE_CHECKING, Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    DateTime,
    ForeignKeyConstraint,
    Index,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import get_database_settings
from app.infrastructure.db.base import Base

if TYPE_CHECKING:
    from app.infrastructure.db.models.catalogos import CategoriaFitosanitaria
    from app.infrastructure.db.models.territorio import Productor

# OJO: cambiar EMBEDDING_DIM obliga a recalcular TODOS los vectores y a migrar la columna y su índice;
# vectores de distinta dimensión no se pueden comparar.
EMBEDDING_DIM = get_database_settings().embedding_dim


class MensajeWhatsapp(Base):
    """Mensaje recibido por el webhook. wamid único = idempotencia ante los reintentos de Meta."""

    __tablename__ = "mensajes_whatsapp"
    __table_args__ = (
        PrimaryKeyConstraint("id_mensaje", name="pk_mensajes_whatsapp"),
        UniqueConstraint("wamid", name="uq_msg_wamid"),
        ForeignKeyConstraint(["id_productor"], ["productores.id_productor"], name="fk_msg_productor"),
        Index("ix_msg_telefono_fecha", "telefono", "fecha_recepcion"),
    )

    id_mensaje: Mapped[str] = mapped_column(String(12))
    wamid: Mapped[str] = mapped_column(String(128), nullable=False)
    telefono: Mapped[str] = mapped_column(String(15), nullable=False)
    tipo: Mapped[str] = mapped_column(String(20), nullable=False)
    contenido: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    estado_procesamiento: Mapped[str] = mapped_column(String(15), nullable=False)
    fecha_recepcion: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    id_productor: Mapped[str | None] = mapped_column(String(10))

    productor: Mapped["Productor | None"] = relationship(back_populates="mensajes_whatsapp")


class ConocimientoEmbedding(Base):
    """Fragmentos de conocimiento para el RAG, con su embedding (pgvector)."""

    __tablename__ = "conocimiento_embeddings"
    __table_args__ = (
        PrimaryKeyConstraint("id_conocimiento", name="pk_conocimiento_embeddings"),
        ForeignKeyConstraint(["id_categoria"], ["categorias_fitosanitarias.id_categoria"], name="fk_con_categoria"),
        Index("ix_con_categoria", "id_categoria"),
        Index(
            "ix_con_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id_conocimiento: Mapped[str] = mapped_column(String(12))
    id_categoria: Mapped[str | None] = mapped_column(String(5))
    fuente: Mapped[str] = mapped_column(String(200), nullable=False)
    texto: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[Any] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)

    categoria: Mapped["CategoriaFitosanitaria | None"] = relationship(back_populates="conocimientos")
