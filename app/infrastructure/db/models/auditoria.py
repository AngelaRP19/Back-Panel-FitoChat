from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKeyConstraint, Index, PrimaryKeyConstraint, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.db.base import Base

if TYPE_CHECKING:
    from app.infrastructure.db.models.cuentas import LiderTecnico


class Auditoria(Base):
    """Registro de auditoría. valor_anterior/valor_nuevo nunca deben contener contraseñas ni teléfonos completos."""

    __tablename__ = "auditoria"
    __table_args__ = (
        PrimaryKeyConstraint("id_auditoria", name="pk_auditoria"),
        ForeignKeyConstraint(["id_lider_tecnico"], ["lideres_tecnicos.id_lider_tecnico"], name="fk_aud_lider"),
        Index("ix_auditoria_lider", "id_lider_tecnico", "fecha_auditoria"),
        Index("ix_auditoria_entidad", "entidad_afectada"),
    )

    id_auditoria: Mapped[str] = mapped_column(String(12))
    id_lider_tecnico: Mapped[str] = mapped_column(String(10), nullable=False)
    accion: Mapped[str] = mapped_column(String(20), nullable=False)
    entidad_afectada: Mapped[str] = mapped_column(String(50), nullable=False)
    descripcion: Mapped[str] = mapped_column(String(500), nullable=False)
    valor_anterior: Mapped[str | None] = mapped_column(String(1000))
    valor_nuevo: Mapped[str | None] = mapped_column(String(1000))
    resultado: Mapped[str] = mapped_column(String(10), nullable=False)
    fecha_auditoria: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    lider_tecnico: Mapped["LiderTecnico"] = relationship(back_populates="registros_auditoria")
