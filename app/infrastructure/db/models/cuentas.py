"""Cuentas del panel.

contrasena_* guarda SOLO hashes Argon2 (app.core.passwords), nunca texto plano. Si el panel se
autentica solo con Cognito, estas columnas pueden quedar sin usar (se llenan con
unusable_password_hash() porque el modelo las define NOT NULL).
cognito_sub no está en el modelo físico: se agrega para enlazar la cuenta con el usuario de Cognito.
"""

from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Date, PrimaryKeyConstraint, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.db.base import Base

if TYPE_CHECKING:
    from app.infrastructure.db.models.auditoria import Auditoria
    from app.infrastructure.db.models.casos import Asignacion
    from app.infrastructure.db.models.epidemiologia import Alerta


class Tecnico(Base):
    __tablename__ = "tecnicos"
    __table_args__ = (
        PrimaryKeyConstraint("id_tecnico", name="pk_tecnico"),
        UniqueConstraint("correo_tecnico", name="uq_tec_correo"),
        UniqueConstraint("celular_tecnico", name="uq_tec_celular"),
        UniqueConstraint("cognito_sub", name="uq_tec_cognito_sub"),
    )

    id_tecnico: Mapped[str] = mapped_column(String(10))
    nombre_tecnico: Mapped[str] = mapped_column(String(100), nullable=False)
    correo_tecnico: Mapped[str] = mapped_column(String(100), nullable=False)
    celular_tecnico: Mapped[str] = mapped_column(String(15), nullable=False)
    contrasena_tecnico: Mapped[str] = mapped_column(String(255), nullable=False)
    estado_cuenta_tecnico: Mapped[str] = mapped_column(String(15), nullable=False)
    fecha_registro_tecnico: Mapped[date] = mapped_column(Date, nullable=False)
    cognito_sub: Mapped[str | None] = mapped_column(String(64))

    asignaciones: Mapped[list["Asignacion"]] = relationship(back_populates="tecnico")
    registros_auditoria: Mapped[list["Auditoria"]] = relationship(back_populates="tecnico")


class LiderTecnico(Base):
    __tablename__ = "lideres_tecnicos"
    __table_args__ = (
        PrimaryKeyConstraint("id_lider_tecnico", name="pk_lider_tecnico"),
        UniqueConstraint("correo_lider_tecnico", name="uq_lid_tec_correo"),
        UniqueConstraint("celular_lider_tecnico", name="uq_lid_tec_celular"),
        UniqueConstraint("cognito_sub", name="uq_lid_tec_cognito_sub"),
    )

    id_lider_tecnico: Mapped[str] = mapped_column(String(10))
    nombre_lider_tecnico: Mapped[str] = mapped_column(String(100), nullable=False)
    correo_lider_tecnico: Mapped[str] = mapped_column(String(100), nullable=False)
    celular_lider_tecnico: Mapped[str] = mapped_column(String(15), nullable=False)
    contrasena_lider_tecnico: Mapped[str] = mapped_column(String(255), nullable=False)
    estado_cuenta_lider_tecnico: Mapped[str] = mapped_column(String(15), nullable=False)
    cognito_sub: Mapped[str | None] = mapped_column(String(64))

    registros_auditoria: Mapped[list["Auditoria"]] = relationship(back_populates="lider_tecnico")
    alertas_gestionadas: Mapped[list["Alerta"]] = relationship(
        back_populates="lider_gestion", foreign_keys="Alerta.id_lider_gestion"
    )
    alertas_corregidas: Mapped[list["Alerta"]] = relationship(
        back_populates="lider_correccion", foreign_keys="Alerta.id_lider_correccion"
    )
