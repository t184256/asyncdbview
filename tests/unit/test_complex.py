# SPDX-FileCopyrightText: 2023 Alexander Sosedkin <monk@unboiled.info>
# SPDX-License-Identifier: GPL-3.0

"""Three-level hierarchy and custom loading tests for asyncdbview."""

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
    bs: sqlalchemy.orm.Mapped[typing.List['_B']] = \
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
    cs: sqlalchemy.orm.Mapped[typing.List['_C']] = \
        sqlalchemy.orm.relationship()


class _C(MappedObjectBase):
    """Test grandchild class, database model."""

    __tablename__ = 'c'

    id: sqlalchemy.orm.Mapped[int] = \
        sqlalchemy.orm.mapped_column(primary_key=True)
    name: sqlalchemy.orm.Mapped[str]
    b_id: sqlalchemy.orm.Mapped[int] = \
        sqlalchemy.orm.mapped_column(sqlalchemy.ForeignKey('b.id'))
    b: sqlalchemy.orm.Mapped[_B] = \
        sqlalchemy.orm.relationship(back_populates='cs')


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

    @property
    def cs(self) -> typing.Awaitable[typing.List['C']]:  # noqa: D102
        # custom loader that loads all Cs of the A at once
        # alternatively, one could define an A->C relationship and load it
        # with the regular _field_loader
        async def custom_loader():
            adbv = self._adbv
            a = await self.a
            # all of them are merged, query is remembered to be cached
            cs = await adbv._load_from_query(C, 'A.cs', a.id,
                                             sqlalchemy.select(_C, _B, _A)
                                                       .join(_A.bs)
                                                       .join(_B.cs)
                                                       .where(_A.id == a.id))
            # but only the ones related to a specific b are returned
            return [c for c in cs if await c.b == self]
        return custom_loader()


class C(asyncdbview.ADBVObject):
    """Wrapper class for C (grandchiled) entities exposed by ADBV."""

    __underlying_class__ = _C

    @property
    def id(self) -> int:  # noqa: D102
        return self._cache_object.id

    @property
    def name(self) -> str:  # noqa: D102
        return self._cache_object.name

    @property
    def b(self) -> typing.Awaitable[B]:  # noqa: D102
        return self._by_id_field_loader(
            B, 'b', 'b_id',
            limit_mode=asyncdbview.Mode.PREFER_CACHE
            # assumes Cs aren't reparented to some different B
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
        _a2 = _A(id=2, name='a2')
        session.add(_a2)
        for i in range(4):
            _b = _B(id=i, a=_a1, name=f'b{i}')
            session.add(_b)
            if i != 3:
                for j in range(2):
                    cid = 3 * i + j
                    session.add(_C(id=cid, b=_b, name=f'b{i}.c{j}'))
        await session.commit()
    return engine


class AB_ADBV(asyncdbview.ADBV):
    """ADBV capable of giving you A and B wrapper objects."""

    __mapped_object_base__ = MappedObjectBase

    async def A(self, id_: int) -> A:  # noqa: D102
        return await self._load(A, id_)

    async def B(self, id_: int) -> B:  # noqa: D102
        return await self._load(B, id_)

    async def C(self, id_: int) -> C:  # noqa: D102
        return await self._load(C, id_)


@pytest.mark.asyncio
async def test_smoke(example_origin):
    """Test basic functionality."""
    origin = await example_origin
    async with AB_ADBV(origin) as adbv:
        a1 = await adbv.A(1)
        b0 = (await a1.bs)[0]
        assert (await b0.cs)[0].name == 'b0.c0'
        assert (await b0.cs)[1].name == 'b0.c1'
        assert sum([len(await b.cs) for b in await a1.bs]) == 3 * 2
        await b0.a
        assert await b0.a is a1
    await origin.dispose()


@pytest.mark.asyncio
async def test_mass_preloading(example_origin):
    """Ensure preloading happens for all Cs at once."""
    origin = await example_origin
    cache = asyncdbview.in_memory_cache_db()

    async with AB_ADBV(origin, cache=cache) as adbv:
        a1 = await adbv.A(1)
        b0 = (await a1.bs)[0]
        b3 = (await a1.bs)[3]
        assert len(await b0.cs) == 2
        assert len(await b3.cs) == 0

    await origin.dispose()

    async with AB_ADBV(None, cache=cache,
                       mode=asyncdbview.Mode.OFFLINE) as adbv:
        a1 = await adbv.A(1)
        b0 = (await a1.bs)[0]
        b3 = (await a1.bs)[3]
        assert len(await b0.cs) == 2
        assert len(await b3.cs) == 0
        c = (await b0.cs)[0]
        assert await c.b is b0
        assert await (await c.b).a is a1

    await cache.dispose()


@pytest.mark.asyncio
async def test_no_preloading(example_origin):
    """Ensure that not loading Cs doesn't cache them."""
    origin = await example_origin
    cache = asyncdbview.in_memory_cache_db()

    async with AB_ADBV(origin, cache=cache) as adbv:
        a1 = await adbv.A(1)
        b0 = (await a1.bs)[0]
        b3 = (await a1.bs)[3]

    await origin.dispose()

    async with AB_ADBV(None, cache=cache,
                       mode=asyncdbview.Mode.OFFLINE) as adbv:
        a1 = await adbv.A(1)
        b0 = (await a1.bs)[0]
        b3 = (await a1.bs)[3]
        with pytest.raises(asyncdbview.IsOfflineError):
            await b0.cs
        with pytest.raises(asyncdbview.IsOfflineError):
            await b3.cs

    await cache.dispose()


@pytest.mark.asyncio
async def test_modes_a(example_origin):
    """Test modes functionality."""
    origin = await example_origin
    cache = asyncdbview.in_memory_cache_db()
    async with AB_ADBV(origin, cache=cache) as adbv:
        await adbv.A(1)
        # but neither its .b nor its .cs have been loaded
    async with AB_ADBV(None, cache=cache,
                       mode=asyncdbview.Mode.OFFLINE) as adbv:
        a1 = await adbv.A(1)
        with pytest.raises(asyncdbview.IsOfflineError):
            await a1.bs
    async with AB_ADBV(origin, cache=cache,
                       mode=asyncdbview.Mode.PREFER_CACHE) as adbv:
        a1 = await adbv.A(1)
        assert len(await a1.bs) == 4
        assert sum([len(await b.cs) for b in await a1.bs]) == 6

    async with sqlalchemy.ext.asyncio.async_sessionmaker(origin)() as session:
        session.add(_C(id=100, b_id=1, name='test'))
        await session.commit()

    async with AB_ADBV(origin, cache=cache,
                       mode=asyncdbview.Mode.OFFLINE) as adbv:
        a1 = await adbv.A(1)
        assert len(await a1.bs) == 4
        assert sum([len(await b.cs) for b in await a1.bs]) == 6
    async with AB_ADBV(origin, cache=cache,
                       mode=asyncdbview.Mode.PREFER_CACHE) as adbv:
        a1 = await adbv.A(1)
        assert len(await a1.bs) == 4
        assert sum([len(await b.cs) for b in await a1.bs]) == 6
    async with AB_ADBV(origin, cache=cache,
                       mode=asyncdbview.Mode.FRESHEN) as adbv:
        a1 = await adbv.A(1)
        assert len(await a1.bs) == 4
        assert sum([len(await b.cs) for b in await a1.bs]) == 7
    async with AB_ADBV(origin, cache=cache,
                       mode=asyncdbview.Mode.PREFER_CACHE) as adbv:
        a1 = await adbv.A(1)
        assert len(await a1.bs) == 4
        assert sum([len(await b.cs) for b in await a1.bs]) == 7
    async with AB_ADBV(None, cache=cache,
                       mode=asyncdbview.Mode.OFFLINE) as adbv:
        a1 = await adbv.A(1)
        assert len(await a1.bs) == 4
        assert sum([len(await b.cs) for b in await a1.bs]) == 7
    await origin.dispose()
    await cache.dispose()


@pytest.mark.asyncio
async def test_modes_b(example_origin):
    """Test modes functionality."""
    origin = await example_origin
    cache = asyncdbview.in_memory_cache_db()
    async with AB_ADBV(None, cache=cache,
                       mode=asyncdbview.Mode.OFFLINE) as adbv:
        with pytest.raises(asyncdbview.IsOfflineError):
            await adbv.B(1)
    async with AB_ADBV(origin, cache=cache) as adbv:
        b1 = await adbv.B(1)
        assert b1.name == 'b1'
        # but neither its .a nor its .cs have been loaded
    async with AB_ADBV(None, cache=cache,
                       mode=asyncdbview.Mode.OFFLINE) as adbv:
        b1 = await adbv.B(1)
        with pytest.raises(asyncdbview.IsOfflineError):
            await b1.a
        with pytest.raises(asyncdbview.IsOfflineError):
            await b1.cs
        assert b1.name == 'b1'
    async with AB_ADBV(origin, cache=cache,
                       mode=asyncdbview.Mode.PREFER_CACHE) as adbv:
        b1 = await adbv.B(1)
        assert (await b1.a).name == 'a1'
        assert len(await b1.cs) == 2

    async with AB_ADBV(origin, cache=cache,
                       mode=asyncdbview.Mode.PREFER_CACHE) as adbv:
        async with sqlalchemy.ext.asyncio.async_sessionmaker(origin)() \
                as session:
            session.add(_C(id=100, b_id=1, name='test'))
            _b1 = await session.get(_B, 1)
            _b1.a_id = 2
            _b1.name = 'changed'
            await session.commit()
        b1 = await adbv.B(1)
        assert (await b1.a).name == 'a1'  # not picked up because PREFER_CACHE
        assert len(await b1.cs) == 2
        assert b1.name == 'b1'

    async with AB_ADBV(None, cache=cache,
                       mode=asyncdbview.Mode.OFFLINE) as adbv:
        b1 = await adbv.B(1)
        assert (await b1.a).name == 'a1'  # not picked up because OFFLINE
        assert len(await b1.cs) == 2
        assert b1.name == 'b1'
    async with AB_ADBV(origin, cache=cache,
                       mode=asyncdbview.Mode.FRESHEN) as adbv:
        b1 = await adbv.B(1)
        assert (await b1.a).name == 'a2'
        assert len(await b1.cs) == 3
        assert b1.name == 'changed'
    async with AB_ADBV(origin, cache=cache,
                       mode=asyncdbview.Mode.PREFER_CACHE) as adbv:
        b1 = await adbv.B(1)
        assert (await b1.a).name == 'a2'
        assert len(await b1.cs) == 3
        assert b1.name == 'changed'
    async with AB_ADBV(None, cache=cache,
                       mode=asyncdbview.Mode.OFFLINE) as adbv:
        b1 = await adbv.B(1)
        assert (await b1.a).name == 'a2'
        assert len(await b1.cs) == 3
        assert b1.name == 'changed'
    await origin.dispose()
    await cache.dispose()


@pytest.mark.asyncio
async def test_missing(example_origin):
    """Test modes functionality."""
    origin = await example_origin
    async with AB_ADBV(origin, mode=asyncdbview.Mode.OFFLINE) as adbv:
        with pytest.raises(asyncdbview.IsOfflineError):
            await adbv.B(100500)
    async with AB_ADBV(origin, mode=asyncdbview.Mode.PREFER_CACHE) as adbv:
        with pytest.raises(sqlalchemy.orm.exc.UnmappedInstanceError):  # FIXME
            await adbv.B(100500)
    async with AB_ADBV(origin, mode=asyncdbview.Mode.FRESHEN) as adbv:
        with pytest.raises(sqlalchemy.orm.exc.UnmappedInstanceError):  # FIXME
            await adbv.B(100500)
