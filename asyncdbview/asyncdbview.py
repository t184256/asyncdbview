# SPDX-FileCopyrightText: 2023 Alexander Sosedkin <monk@unboiled.info>
# SPDX-License-Identifier: GPL-3.0

"""Limited async-first ORM with a local cache."""

import asyncio
import enum

import sqlalchemy
import sqlalchemy.ext.asyncio
import sqlalchemy.inspection


Mode = enum.Enum('Mode', ('OFFLINE', 'PREFER_CACHE', 'FRESHEN'))
RaiseIfMissing = object()


def _min_mode(mode1, mode2):
    if mode1 is None:
        return mode2
    return Mode(min(mode1.value, mode2.value))


class NotLiveError(RuntimeError):
    """Error raised when operating on unopened/closed ADBV."""


class IsOfflineError(RuntimeError):
    """Error raised when making uncached queries offline."""


def in_memory_cache_db():
    """Return an in-memory database suitable for use with ADBV(cache=...)."""
    return sqlalchemy.ext.asyncio.create_async_engine(
        'sqlite+aiosqlite:///:memory:'
    )


_extra_metadata = sqlalchemy.MetaData()

_ever_loaded = sqlalchemy.Table(
    '_ever_loaded',
    _extra_metadata,
    sqlalchemy.Column('cls', sqlalchemy.String, primary_key=True),
    sqlalchemy.Column('identity', sqlalchemy.String, primary_key=True),
    sqlalchemy.Column('field', sqlalchemy.String, primary_key=True),
)


async def _ever_loaded_exists(cache_session, cls, identity, attrname):
    # Can give false negatives under race conditions, that's acceptable
    stmt = sqlalchemy.text("""
        SELECT 1 FROM '_ever_loaded' WHERE
            cls = :cls AND
            identity = :identity AND
            field = :field
        LIMIT 1
    """)
    i = str(identity[0]
            if isinstance(identity, tuple) and len(identity) == 1 else
            identity)
    values = {
        'cls': cls.__name__,
        'identity': str(i),
        'field': attrname,
    }
    ex = cache_session.execute(stmt, params=values)
    return bool((await ex).all())
    # TODO: try session.execute(select(1).where(...))


async def _ever_loaded_mark(cache_session, cls, identity, attrname):
    stmt = sqlalchemy.text("""
        INSERT INTO _ever_loaded(cls,identity,field)
        VALUES(:cls,:identity,:field)
        ON CONFLICT DO NOTHING
    """)
    i = str(identity[0]
            if isinstance(identity, tuple) and len(identity) == 1 else
            identity)
    values = {
        'cls': cls.__name__,
        'identity': str(i),
        'field': attrname,
    }
    await cache_session.execute(stmt, params=values)
    # TODO: try sqlalchemy way of upserting?


class ADBVObject:
    """
    Wrapper for a data model object that asyncdbview exposes.

    You'll then define awaitable attributes
    as properties returning custom loaders
    and regular attributes as properties returning values taken from
    self._cache_object.
    Inherit your wrappers from this class.
    """

    def __init__(self, adbv, cache_object, private=False):
        """Do not use."""
        assert private, 'Do not initialize ADBVObjects directly, use ADBV'
        self._adbv = adbv
        self._cache_object = cache_object

    def _field_loader(self, wrapper_class, name, limit_mode=None,
                      offline_fallback=RaiseIfMissing):
        async def loader():
            adbv = self._adbv
            insp = sqlalchemy.inspection.inspect(self._cache_object)
            cache_object = object.__getattribute__(self, '_cache_object')

            # stage 1, maybe it's loaded already?
            if name not in insp.unloaded:
                # already loaded
                r = object.__getattribute__(cache_object, name)
                return adbv._wrap_multi(self.__class__, r)

            # stage 2, loading is required. maybe we can make do with cache?
            cache_session = adbv._cache_session
            mode = _min_mode(limit_mode, adbv._mode)
            cls, identity, _ = \
                cache_session.identity_key(instance=cache_object)
            # TODO: move inside
            ever_loaded_exists = await _ever_loaded_exists(cache_session,
                                                           cls, identity, name)
            if mode != Mode.FRESHEN:
                if ever_loaded_exists:
                    await cache_session.refresh(cache_object,
                                                attribute_names=[name])
                    r = getattr(cache_object, name)
                    return (adbv._wrap_multi(wrapper_class, r)
                            if r is not None else None)

                if mode == Mode.OFFLINE:
                    # never loaded & can't query
                    if offline_fallback == RaiseIfMissing:
                        raise IsOfflineError(f'cannot query .{name} '
                                             f'of f{self}; '
                                             'offline and it is not cached')
                    return offline_fallback

            # strategy 3, loading from origin and merging into cache
            # we're going the slower get(self) + refresh(name) thing
            r = None
            async with adbv._origin_sm() as origin_session:
                # load the object from the origin db first (FIXME: avoid)
                o = await origin_session.get(cls, identity)
                await origin_session.refresh(o, attribute_names=[name])
                r = getattr(o, name)
            # merge the result(s) into the cache db
            if r is not None:
                await adbv._merge(r)
            if not ever_loaded_exists:
                async with adbv._cache_sm() as cache_session2:
                    await _ever_loaded_mark(cache_session2, cls,
                                            identity, name)
                    await cache_session2.commit()
            await cache_session.refresh(cache_object,
                                        attribute_names=[name])
            if r is None:
                return None
            r = getattr(cache_object, name)
            return adbv._wrap_multi(wrapper_class, r)

        return loader()

    def _by_id_field_loader(self, wrapper_class, name, id_name,
                            limit_mode=None,
                            offline_fallback=RaiseIfMissing):
        async def loader():
            adbv = self._adbv
            cache_object = object.__getattribute__(self, '_cache_object')
            insp = sqlalchemy.inspection.inspect(cache_object)
            if name not in insp.unloaded:  # is it loaded already? just wrap
                r = object.__getattribute__(cache_object, name)
                return adbv._wrap_multi(self.__class__, r)
            id_ = object.__getattribute__(cache_object, id_name)
            r = await adbv._load(wrapper_class, id_,
                                 limit_mode=limit_mode,
                                 offline_fallback=offline_fallback)
            await adbv._cache_session.refresh(cache_object,
                                              attribute_names=[name])
            return r
        return loader()

    def __getattribute__(self, name):
        adbv = object.__getattribute__(self, '_adbv')
        if not adbv._opened:
            self_class = object.__getattribute__(self, '__class__')
            raise NotLiveError(f'{self_class.__name__} is being accessed ' +
                               (f'after {adbv.__class__.__name__} '
                                'has been closed'
                                if adbv._opened is False else
                                f'before {adbv.__class__.__name__} '
                                'has been opened'))
        return object.__getattribute__(self, name)


class ADBV:
    """
    Cached database class.

    One is supposed to inherit from it and add nice constuctors
    that use _load underneath to return ADBVObjects.
    ADBVObject are supposed to implement properties returning
    1. values directly from underlying DB object,
       for eagerly-loaded attributes
    2. coroutines that define loading of lazily-loaded attributes,
       usually used for relationships

    Inherited class must also define `__mapped_object_base__`
    with declarative base class of your sqlalchemy ORM schema.
    """

    def __init__(self, origin, cache=None, mode=Mode.FRESHEN):
        """
        Construct a cached database.

        origin: database to load data from, can be None if mode is OFFLINE
        cache: database to load data from, will use in-memory cache if None
        mode: OFFLINE / PREFER_CACHE / FRESHEN
        """
        self._mode = mode
        self._cache_engine = cache or in_memory_cache_db()
        self._cache_sm = sqlalchemy.ext.asyncio.async_sessionmaker(
            self._cache_engine, expire_on_commit=False,
        )
        if origin is None:
            assert self._mode == Mode.OFFLINE
        if self._mode != Mode.OFFLINE:
            self._origin_engine = origin
            self._origin_sm = sqlalchemy.ext.asyncio.async_sessionmaker(
                self._origin_engine, expire_on_commit=False,
            )
        self._opened = None
        self._lock = asyncio.Lock()

    async def __aenter__(self):
        if self._opened is False:
            raise NotLiveError(f"{self.__class__.__name__} can't be reopened")
        assert self._opened is None
        async with self._lock:
            async with self._cache_engine.begin() as cache_conn:
                await cache_conn.run_sync(_extra_metadata.create_all)
                await cache_conn.run_sync(
                    self.__mapped_object_base__.metadata.create_all
                )
                self._cache_session = await self._cache_sm().__aenter__()
            self._opened = True
        return self

    async def __aexit__(self, *a):
        assert self._opened is True
        self._opened = False
        return await self._cache_session.__aexit__(*a)

    def _wrap_multi(self, wrapper_class, cache_db_objs):
        if not isinstance(cache_db_objs, list):
            return self._wrap(wrapper_class, cache_db_objs)
        return [self._wrap(wrapper_class, o) for o in cache_db_objs]

    def _wrap(self, wrapper_class, cache_db_obj):
        if hasattr(cache_db_obj, '_wrapper'):
            return cache_db_obj._wrapper
        wrapper = wrapper_class(self, cache_db_obj, private=True)
        cache_db_obj._wrapper = wrapper
        return wrapper

    def _assert_opened(self, wrapper_class):
        if not self._opened:
            raise NotLiveError(f'{wrapper_class.__class__.__name__}'
                               " can't be created from a "
                               + ('closed' if self._opened is False else
                                  'unopened') +
                               f' {self.__class__.__name__}')

    async def _load(self, wrapper_class, id_,
                    limit_mode=None, offline_fallback=RaiseIfMissing):
        """
        Load an object by ID.

        Use it from your ADBV subclasses:
        ```
        class MyADBV:
            __mapped_object_base__ = MappedObjectBase
            async def A(self, id_: int) -> A:
                return await self._load(A, id_)
        ```
        """
        self._assert_opened(wrapper_class)

        mode = _min_mode(limit_mode, self._mode)
        underlying_cls = wrapper_class.__underlying_class__
        ever_loaded_exists = await _ever_loaded_exists(self._cache_session,
                                                       underlying_cls, id_,
                                                       '-')
        if mode != Mode.FRESHEN:
            if ever_loaded_exists:
                cache_obj = await self._cache_session.get(underlying_cls, id_)
                return self._wrap(wrapper_class, cache_obj)
            if mode == Mode.OFFLINE:
                # never loaded & can't query
                if offline_fallback == RaiseIfMissing:
                    raise IsOfflineError('cannot construct '
                                         f'{wrapper_class.__class__.__name__}'
                                         f'#{id_}; '
                                         'offline and it is not cached')
                return offline_fallback
        async with self._origin_sm() as origin_session:
            origin_obj = await origin_session.get(underlying_cls, id_)
        await self._merge_single(origin_obj)
        if not ever_loaded_exists:
            async with self._cache_sm() as cache_session2:
                await _ever_loaded_mark(cache_session2,
                                        underlying_cls, id_, '-')
                await cache_session2.commit()
        cache_obj = await self._cache_session.get(underlying_cls, id_)
        return self._wrap(wrapper_class, cache_obj)

    async def _load_from_query(self, wrapper_class, tag, identity, statement,
                               limit_mode=None,
                               offline_fallback=RaiseIfMissing):
        """
        Load and cache objects returned by an sqlalchemy statement.

        Use it, say, from your object wrappers:
        ```
        class B:
            __underlying_class__ = _B
            async def cs(self, id_: int) -> List[C]:
                async def custom_loader():
                    a = await self.a
                    a_cs = await adbv._load_from_query(
                        C, 'A.cs', self.id, sqlalchemy.select(_C, _B, _A)
                                                      .join(_A.bs)
                                                      .join(_B.cs)
                                                      .where(_A.id == a.id)
                    )
                    return [c for c in a_cs if await c.b == self]
                return custom_loader()
        ```
        """
        self._assert_opened(wrapper_class)
        mode = _min_mode(limit_mode, self._mode)
        underlying_cls = wrapper_class.__underlying_class__
        ever_loaded_exists = await _ever_loaded_exists(self._cache_session,
                                                       underlying_cls,
                                                       identity, tag)
        if mode != Mode.FRESHEN:
            if ever_loaded_exists:
                return await self.__query_cache(wrapper_class, statement)
            if mode == Mode.OFFLINE:
                # never loaded & can't query
                if offline_fallback == RaiseIfMissing:
                    raise IsOfflineError('cannot construct '
                                         f'{wrapper_class.__class__.__name__}'
                                         f'using {tag} custom query; '
                                         'offline and it is not cached')
                return offline_fallback
        async with self._origin_sm() as origin_session:
            origin_objs = (await origin_session.scalars(statement)).all()
        await self._merge_multi(origin_objs)
        if not ever_loaded_exists:
            async with self._cache_sm() as cache_session2:
                await _ever_loaded_mark(cache_session2,
                                        underlying_cls, identity, tag)
                await cache_session2.commit()
        return await self.__query_cache(wrapper_class, statement)

    async def __query_cache(self, wrapper_class, statement):
        cached = (await self._cache_session.scalars(statement)).all()
        return self._wrap_multi(wrapper_class, cached)

    async def _merge(self, dbresult):
        if isinstance(dbresult, list):
            await self._merge_multi(dbresult)
        else:
            await self._merge_single(dbresult)

    async def _merge_single(self, dbobject):
        async with self._lock:
            async with self._cache_sm() as cache_session_wr:
                await cache_session_wr.merge(dbobject)
                await cache_session_wr.commit()

    async def _merge_multi(self, dbobjects):
        async with self._lock:
            async with self._cache_sm() as cache_session_wr:
                for o in dbobjects:
                    await cache_session_wr.merge(o)
                await cache_session_wr.commit()

    # TODO: switching modes in runtime
