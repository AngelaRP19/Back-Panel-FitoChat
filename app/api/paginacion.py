"""Paginación común del panel: `?page=1&page_size=20` (máximo 100) -> `{items, total, page, page_size}` (guía §8)."""

from dataclasses import dataclass
from typing import Annotated, Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel

T = TypeVar("T")

PAGE_SIZE_POR_DEFECTO = 20
PAGE_SIZE_MAXIMO = 100


class Pagina(BaseModel, Generic[T]):  # noqa: UP046
    items: list[T]
    total: int
    page: int
    page_size: int


@dataclass(frozen=True)
class ParametrosPagina:
    page: int
    page_size: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


def parametros_pagina(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=PAGE_SIZE_MAXIMO)] = PAGE_SIZE_POR_DEFECTO,
) -> ParametrosPagina:
    return ParametrosPagina(page=page, page_size=page_size)
