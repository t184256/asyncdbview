# SPDX-FileCopyrightText: 2023 Alexander Sosedkin <monk@unboiled.info>
# SPDX-License-Identifier: GPL-3.0

"""Lifecycle tests for asyncdbview."""

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


class A(asyncdbview.ADBVObject):
    """Wrapper class for A (parent) entities exposed by ADBV."""

    __underlying_class__ = _A

    @property
    def id(self) -> int:  # noqa: D102
        return self._cache_object.id

    @property
    def name(self) -> str:  # noqa: D102
        return self._cache_object.name


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
        await session.commit()
    return engine


class AB_ADBV(asyncdbview.ADBV):
    """ADBV capable of giving you A and B wrapper objects."""

    __mapped_object_base__ = MappedObjectBase

    async def A(self, id_: int) -> A:  # noqa: D102
        return await self._load(A, id_)


@pytest.mark.asyncio
async def test_lifecycles(example_origin):
    """Test opening/closing/reopening/... ADBV."""
    origin = await example_origin
    adbv = AB_ADBV(origin)

    with pytest.raises(asyncdbview.NotLiveError):
        await adbv.A(1)

    async with adbv:
        a1 = await adbv.A(1)
        assert a1.name == 'a1'

    with pytest.raises(asyncdbview.NotLiveError):
        await adbv.A(1)

    with pytest.raises(asyncdbview.NotLiveError):
        a1.name  # pylint: disable=pointless-statement

    with pytest.raises(asyncdbview.NotLiveError):
        async with adbv:
            pass
