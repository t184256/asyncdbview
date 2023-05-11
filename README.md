# asyncdbview

Limited async-first ORM with a local cache.

A work-in-progress.

## What is it useful for

I want to write asyncio programs accessing rarely-changing data
that lives in a slow, read-only database.

sqlalchemy is awesome, but I want to

1. `await` on the relationship attributes whenever I want to to load them
   (see https://github.com/sqlalchemy/sqlalchemy/discussions/9731)
2. work with a caching db in front of a slow origin db
3. be able to work fully offline and access at least everything I've cached before
4. still be able to issue arbitary SQL queries if I want to
5. flexibly, optionally eagerly preload data in the most optimal way for my data model
6. have it all typing-friendly

## Interface

For `A`s having several `B`s, I want to be able to write something like this:

``` python
async with My_ADBV(origin_db_engine, cache=cache_db_engine) as adbv:
    a1 = await adbv.A(1)
    await a1.bs
    # the asserts below make no extra queries, both a1 and its .bs are cached
    assert len(await a1.bs) == 3
    assert await (await a1.bs)[0].a is a1  # identity checks work
    assert await asyncio.gather(*[b.a for b in await a1.bs]) == [a1] * 3

async with My_ADBV(None, cache=cache_db_engine,
                   mode=asyncdbview.mode.OFFLINE) as adbv:
    # working with cache alone
    a1 = await adbv.A(1)
    assert await asyncio.gather(*[b.a for b in await a1.bs]) == [a1] * 3
```

## Non-goals, limitations and optimizations

1. read-only
2. async-only
2. consistency is overrated

## Possible stretch goals

1. maintaining a set of local changes visible in local db, but not committed to remote db
2. switching database mode on-the-fly

## TODO

1. switch from lists to tuples
2. fix ever_loaded insertion race condition (rewrite in raw SQL?)
3. per-query-params locking. add column for queued/completed?
4. tests that hammer the database in parallel
5. tests that hammer the database in parallel
   and ensure no unnecessary queries have happened
6. tests that share one file-backed cache across processes
9. profiling and optimizing
10. OFFLINE_FALLBACK mode (make fallbacks a mode, not a relationship?)
11. explicit refresh_all action?
12. move from AVDB inheritance to composition?
13. add types, enable mypy
14. consider using __slots__
15. custom queries
