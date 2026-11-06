from sqlalchemy import CheckConstraint, MetaData, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Convención para los objetos que el modelo no nombra. Los nombres explícitos del modelo
# (pk_*, fk_*, uq_*, ix_*) se respetan tal cual: estas plantillas no usan %(constraint_name)s,
# salvo la de CHECK, que antepone ck_<tabla>_.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def flag_column(*, server_default: str | None = None) -> Mapped[str]:
    """CHAR(1) de Oracle: String(1) NOT NULL; el CHECK ('S','N') se declara con flag_check()."""
    return mapped_column(String(1), nullable=False, server_default=server_default)


def flag_check(column: str) -> CheckConstraint:
    return CheckConstraint(f"{column} IN ('S', 'N')", name=f"{column}_sn")
