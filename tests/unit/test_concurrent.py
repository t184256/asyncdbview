# SPDX-FileCopyrightText: 2023 Alexander Sosedkin <monk@unboiled.info>
# SPDX-License-Identifier: GPL-3.0

"""Concurrency smoke-tests for asyncdbview."""

import asyncio
import typing

import sqlalchemy
import sqlalchemy.event
import sqlalchemy.ext.asyncio
import sqlalchemy.orm

import pytest

import asyncdbview


class MappedObjectBase(sqlalchemy.orm.DeclarativeBase):
    """Base class for the internal sqlalchemy objects behind ADBVObject."""


class _A(MappedObjectBase):
    """Test parent class, database model."""

    __tablename__ = 'a'

    id: sqlalchemy.orm.Mapped[int] = \
        sqlalchemy.orm.mapped_column(primary_key=True)
    name: sqlalchemy.orm.Mapped[str]
    bs: sqlalchemy.orm.Mapped[typing.List['_B']] =\
        sqlalchemy.orm.relationship()


class _B(MappedObjectBase):
    """Test child class, database model."""

    __tablename__ = 'b'
    id: sqlalchemy.orm.Mapped[int] = \
        sqlalchemy.orm.mapped_column(primary_key=True)
    name: sqlalchemy.orm.Mapped[str]
    a_id: sqlalchemy.orm.Mapped[int] = \
        sqlalchemy.orm.mapped_column(sqlalchemy.ForeignKey('a.id'))
    a: sqlalchemy.orm.Mapped[_A] = \
        sqlalchemy.orm.relationship(back_populates='bs')


class A(asyncdbview.ADBVObject):
    """Wrapper class for A (parent) entities exposed by ADBV."""

    __underlying_class__ = _A

    @property
    def id(self) -> int:  # noqa: D102
        return self._cache_object.id

    @property
    def name(self) -> str:  # noqa: D102
        return self._cache_object.name

    @property
    def bs(self) -> typing.Awaitable[typing.List['B']]:  # noqa: D102
        return self._field_loader(B, 'bs')


class B(asyncdbview.ADBVObject):
    """Wrapper class for B (child) entities exposed by ADBV."""

    __underlying_class__ = _B

    @property
    def id(self) -> int:  # noqa: D102
        return self._cache_object.id

    @property
    def name(self) -> str:  # noqa: D102
        return self._cache_object.name

    @property
    def a(self) -> typing.Awaitable[A]:  # noqa: D102
        return self._by_id_field_loader(
            A, 'a', 'a_id',
            limit_mode=asyncdbview.Mode.PREFER_CACHE
            # assumes Bs aren't reparented to some different A
        )


@pytest.fixture(name='example_origin')
async def _example_origin():
    engine = sqlalchemy.ext.asyncio.create_async_engine(
        'sqlite+aiosqlite:///:memory:', echo=True
    )
    async with engine.begin() as connection:
        await connection.run_sync(MappedObjectBase.metadata.create_all)
    async with sqlalchemy.ext.asyncio.async_sessionmaker(engine)() as session:
        for i in range(10):
            _a = _A(id=i, name=f'a{i}')
            session.add(_a)
            session.add_all(_B(id=i * 1000 + j, a=_a, name=f'a{i}-b{j}')
                            for j in range(100))
        await session.commit()
    return engine


class AB_ADBV(asyncdbview.ADBV):
    """ADBV capable of giving you A and B wrapper objects."""

    __mapped_object_base__ = MappedObjectBase

    async def A(self, id_: int) -> A:  # noqa: D102
        return await self._load(A, id_)

    async def B(self, id_: int) -> B:  # noqa: D102
        return await self._load(B, id_)


@pytest.mark.asyncio
async def test_smoke(example_origin):
    """Test basic sanity of the data."""
    origin = await example_origin
    async with AB_ADBV(origin) as adbv:
        for i in range(10):
            a = await adbv.A(i)
            assert len(await a.bs) == 100
        await origin.dispose()
    await origin.dispose()


@pytest.mark.asyncio
async def test_concurrent(example_origin):
    """Test concurrent access."""
    origin = await example_origin
    async with AB_ADBV(origin) as adbv:
        async def count_bs(a_id):
            a = await adbv.A(a_id)
            return len(await a.bs)

        bs = await asyncio.gather(*[count_bs(i) for i in range(10)])
        assert sum(bs) == 10 * 100
