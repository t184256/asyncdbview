# SPDX-FileCopyrightText: 2023 Alexander Sosedkin <monk@unboiled.info>
# SPDX-License-Identifier: GPL-3.0

"""One-to-one tests for asyncdbview."""

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
    b: sqlalchemy.orm.Mapped[typing.Optional['_B']] = \
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
        sqlalchemy.orm.relationship(back_populates='b')


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
    def b(self) -> typing.Awaitable['B']:  # noqa: D102
        return self._field_loader(B, 'b')


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
        return self._by_id_field_loader(A, 'a', 'a_id')


@pytest.fixture(name='example_origin')
async def _example_origin():
    engine = sqlalchemy.ext.asyncio.create_async_engine(
        'sqlite+aiosqlite:///:memory:'
    )
    async with engine.begin() as connection:
        await connection.run_sync(MappedObjectBase.metadata.create_all)
    async with sqlalchemy.ext.asyncio.async_sessionmaker(engine)() as session:
        _a1 = _A(id=1, name='a1')
        session.add(_a1)
        _b1 = _B(id=1, name='b1', a=_a1)
        session.add(_b1)
        _a2 = _A(id=2, name='a2')
        session.add(_a2)
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
async def test_one_to_one(example_origin):
    """Test one-to-one relationship."""
    origin = await example_origin
    cache = asyncdbview.in_memory_cache_db()
    async with AB_ADBV(origin, cache=cache) as adbv:
        a1 = await adbv.A(1)
        b1 = await a1.b
        b1_alt = await adbv.B(1)
        assert b1 is b1_alt
        assert b1.name == 'b1'
        a2 = await adbv.A(2)
        assert await a2.b is None
    await origin.dispose()
    async with AB_ADBV(None, cache=cache,
                       mode=asyncdbview.Mode.OFFLINE) as adbv:
        a1 = await adbv.A(1)
        b1 = await a1.b
        b1_alt = await adbv.B(1)
        assert b1 is b1_alt
        assert b1.name == 'b1'
        a2 = await adbv.A(2)
        assert await a2.b is None
    await cache.dispose()
