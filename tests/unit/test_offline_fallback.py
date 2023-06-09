# SPDX-FileCopyrightText: 2023 Alexander Sosedkin <monk@unboiled.info>
# SPDX-License-Identifier: GPL-3.0

"""offline_fallback functionality tests."""

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
        return self._field_loader(B, 'bs', offline_fallback=[])

    @property
    def also_bs(self) -> typing.Awaitable[typing.List['B']]:  # noqa: D102
        async def custom_loader():
            adbv = self._adbv
            # all of them are merged, query is remembered to be cached
            return await adbv._load_from_query(
                B, 'A.bs', self.id,
                sqlalchemy.select(_B, _A).join(_A.bs).where(_A.id == self.id),
                offline_fallback=[],
            )
        return custom_loader()


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
            limit_mode=asyncdbview.Mode.PREFER_CACHE,
            offline_fallback=None,
            # assumes Bs aren't reparented to some different A
        )


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
        session.add_all(_B(id=i, a=_a1, name=f'b{i}') for i in range(3))
        _a2 = _A(id=2, name='a2')
        session.add(_a2)
        session.add(_B(id=9, a=_a2, name='b9'))
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
async def test_offline_fallback(example_origin):
    """Test offline_fallback functionality."""
    origin = await example_origin
    cache = asyncdbview.in_memory_cache_db()
    async with AB_ADBV(origin, cache=cache) as adbv:
        a1 = await adbv.A(1)
        b9 = await adbv.B(9)
    async with AB_ADBV(None, cache=cache,
                       mode=asyncdbview.Mode.OFFLINE) as adbv:
        # working with cache alone
        a1 = await adbv.A(1)
        b9 = await adbv.B(9)
        assert await a1.bs == []  # fallback
        assert await a1.also_bs == []  # fallback
        assert await b9.a is None  # fallback
    async with AB_ADBV(origin, cache=cache,
                       mode=asyncdbview.Mode.PREFER_CACHE) as adbv:
        # loading
        a1 = await adbv.A(1)
        b9 = await adbv.B(9)
        assert len(await a1.bs) == 3
        assert await a1.bs == await a1.also_bs
        assert (await b9.a).id == 2
    async with AB_ADBV(None, cache=cache,
                       mode=asyncdbview.Mode.OFFLINE) as adbv:
        # loaded
        a1 = await adbv.A(1)
        b9 = await adbv.B(9)
        assert await a1.bs == await a1.also_bs
        assert (await b9.a).id == 2
    await origin.dispose()
    await cache.dispose()
