# SPDX-FileCopyrightText: 2023 Alexander Sosedkin <monk@unboiled.info>
# SPDX-License-Identifier: GPL-3.0

"""Basic smoke-tests for asyncdbview."""

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
        'sqlite+aiosqlite:///:memory:'
    )
    async with engine.begin() as connection:
        await connection.run_sync(MappedObjectBase.metadata.create_all)
    async with sqlalchemy.ext.asyncio.async_sessionmaker(engine)() as session:
        _a1 = _A(id=1, name='a1')
        session.add(_a1)
        session.add_all(_B(id=i, a=_a1, name=f'b{i}') for i in range(3))
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
    """Test basic functionality from the README."""
    origin = await example_origin
    cache = asyncdbview.in_memory_cache_db()
    async with AB_ADBV(origin, cache=cache) as adbv:
        a1 = await adbv.A(1)
        await a1.bs
        # the asserts below make no extra queries
        assert len(await a1.bs) == 3
        assert await (await a1.bs)[0].a is a1  # identity checks work
        assert await asyncio.gather(*[b.a for b in await a1.bs]) == [a1] * 3
        await origin.dispose()
    async with AB_ADBV(None, cache=cache,
                       mode=asyncdbview.Mode.OFFLINE) as adbv:
        # working with cache alone
        a1 = await adbv.A(1)
        assert await asyncio.gather(*[b.a for b in await a1.bs]) == [a1] * 3
    await origin.dispose()
    await cache.dispose()


@pytest.mark.asyncio
async def test_count_connections(example_origin):
    """Count connections to ensure caching works."""
    origin = await example_origin
    cache1 = asyncdbview.in_memory_cache_db()
    cache2 = asyncdbview.in_memory_cache_db()

    counter = 0

    @sqlalchemy.event.listens_for(origin.sync_engine, 'engine_connect')
    def _(_):
        nonlocal counter
        counter += 1

    async with AB_ADBV(origin, cache=cache1) as adbv:
        assert counter == 0
        a1 = await adbv.A(1)
        assert counter == 1  # querying A
        await a1.bs
        assert counter == 2  # querying A, querying its Bs
        assert len(await a1.bs) == 3
        assert await (await a1.bs)[0].a is a1
        assert await asyncio.gather(*[b.a for b in await a1.bs]) == [a1] * 3
        assert counter == 2  # no extra querying

    async with AB_ADBV(origin, cache=cache1) as adbv:
        a1_again1 = await adbv.A(1)
        assert counter == 3  # cache is populated, but it still reloads data
        assert await (await a1_again1.bs)[0].a is a1_again1
        assert a1_again1 != a1  # would've been better if it exploded
        assert counter == 4  # cache is populated, but it still reloads data

    async with AB_ADBV(origin, cache=cache1,
                       mode=asyncdbview.Mode.OFFLINE) as adbv:
        a1_again1 = await adbv.A(1)
        assert await (await a1_again1.bs)[0].a is a1_again1
        assert a1_again1 != a1  # would've been better if it exploded
        assert counter == 4  # now, with offline=True, it doesn't reload data

    async with AB_ADBV(origin, cache=cache1,
                       mode=asyncdbview.Mode.PREFER_CACHE) as adbv:
        a1_again1 = await adbv.A(1)
        assert await (await a1_again1.bs)[0].a is a1_again1
        assert a1_again1 != a1  # would've been better if it exploded
        assert counter == 4  # now, with offline=True, it doesn't reload data

    async with AB_ADBV(origin, cache=cache2) as adbv:
        a1_again2 = await adbv.A(1)
        assert counter == 5  # fresh cache, querying re-starts
        assert await (await a1_again2.bs)[0].a is a1_again2
        assert a1_again2 != a1  # would've been better if it exploded
        assert counter == 6  # fresh cache, querying re-starts

    await origin.dispose()
    await cache1.dispose()
    await cache2.dispose()
