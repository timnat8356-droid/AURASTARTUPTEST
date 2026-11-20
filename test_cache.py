#!/usr/bin/env python3
"""Smoke-test the in-memory archetypes/products caches in app.py.

Verifies:
  1. app.py imports and init_db() runs.
  2. After load_demo(), archetypes()/products() load lazily from DB into memory
     and return plain dicts with the expected counts.
  3. The cache actually caches (same list object on the second call, no new DB hit).
  4. invalidate_catalog() drops both caches so the next call reloads fresh data.
  5. The hot paths (match_archetypes, build_outfit) run end-to-end off the cache.
"""
import os, sys, tempfile, types

# Use a throwaway DATA_DIR so we don't touch any real catalog.
tmp = tempfile.mkdtemp(prefix="aura_test_")
os.environ["DATA_DIR"] = tmp
os.environ["GIGACHAT_AUTH_KEY"] = ""  # stay in rules mode, no network

sys.path.insert(0, "/home/z/my-project/download")
import app

conn = app.db()
na, np_ = app.load_demo(conn)
conn.commit()
print(f"load_demo -> {na} типажей, {np_} СКЮ")

# 2. lazy load
arch_first = app.archetypes(conn)
prod_first = app.products(conn)
print(f"archetypes() -> {len(arch_first)} rows, type={type(arch_first[0]).__name__}")
print(f"products()   -> {len(prod_first)} rows, type={type(prod_first[0]).__name__}")
assert len(arch_first) == na, "archetype count mismatch"
assert len(prod_first) == np_, "product count mismatch"
assert isinstance(arch_first[0], dict) and isinstance(prod_first[0], dict), "rows must be dicts"

# 3. cache hit — same list object, no reload
arch_second = app.archetypes(conn)
prod_second = app.products(conn)
assert arch_second is arch_first, "archetypes cache should return the same object"
assert prod_second is prod_first, "products cache should return the same object"
print("cache hit: same list object returned on 2nd call OK")

# 4. invalidation drops cache
app.invalidate_catalog()
assert app._ARCH_CACHE["rows"] is None and app._PROD_CACHE["rows"] is None
arch_third = app.archetypes(conn)
prod_third = app.products(conn)
assert arch_third is not arch_first and prod_third is not prod_first
assert len(arch_third) == na and len(prod_third) == np_
print("invalidate_catalog -> reloaded fresh data OK")

# 5. hot path: match_archetypes + build_outfit run off the cache
q = {"gender": "женский", "age": 30, "height": 168, "budget_to": 20000,
     "body": "среднее", "styles": ["классика"], "fav_colors": ["черный"], "avoid_colors": [],
     "occasion": "повседневный", "season": "демисезон", "budget_from": 0,
     "size_top": "M", "size_bottom": "44", "size_shoes": "38"}
best = app.match_archetypes(conn, q, photo=None)
print(f"match_archetypes -> best archetype: {best['name']} (score {best['percent']:.1f})")
notes = []
outfit = app.build_outfit(conn, q, best, notes)
print(f"build_outfit -> {len(outfit['items'])} items, notes={notes[:1]}")
assert outfit["items"], "build_outfit returned no items"

# 6. after admin_reset simulation, cache reloads empty
for t in ("archetypes", "products", "events", "sessions"):
    conn.execute(f"DELETE FROM {t}")
conn.commit()
app.invalidate_catalog()
assert app.archetypes(conn) == [] and app.products(conn) == []
print("after reset: archetypes=[] products=[] OK")

conn.close()
print("\nALL CHECKS PASSED")
