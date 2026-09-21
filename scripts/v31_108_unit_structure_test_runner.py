"""Minimal test runner standing in for pytest (not installed, no network to
install it in this sandbox). Discovers test_*.py files and runs every
top-level test_* function and every test_* method on Test* classes,
resolving any of this project's tests/conftest.py fixtures the test asks
for by name (fake_db, monkeypatch, app_module, firebase_configured,
firebase_not_configured, telegram_api, cloudinary_api, gemini_api,
chapa_api, fake_messaging, fake_firebase_auth), including fixtures that
themselves depend on other fixtures (e.g. telegram_api needs monkeypatch),
plus the autouse rate-limit reset before every test.

Only supports what this project's test files need - not a general pytest
replacement (no parametrize, no non-Test-prefixed classes, etc).
"""
import inspect
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# tests/conftest.py does `import pytest` and uses @pytest.fixture and (in
# one file) @pytest.mark.parametrize. A no-op fixture decorator is enough
# to import conftest.py itself; this runner drives fixtures directly
# instead of pytest's DI. pytest.raises is implemented for real since
# several test files use it directly as a context manager.
if "pytest" not in sys.modules:
    import types as _types
    import contextlib as _contextlib

    _fake_pytest = _types.ModuleType("pytest")
    _fake_pytest.fixture = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda fn: fn))

    class _Mark:
        def parametrize(self, *a, **k):
            def deco(fn):
                fn.__bmt_parametrize_skip__ = True
                return fn
            return deco

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    _fake_pytest.mark = _Mark()

    def _param(*values, **k):
        return values

    _fake_pytest.param = _param

    class _Approx:
        def __init__(self, expected, rel=1e-6, abs=1e-12):
            self.expected = expected
            self.rel = rel
            self.abs = abs

        def __eq__(self, other):
            return abs(other - self.expected) <= max(self.abs, self.rel * abs(self.expected))

        def __repr__(self):
            return f"approx({self.expected!r})"

    _fake_pytest.approx = _Approx

    @_contextlib.contextmanager
    def _raises(expected_exception, *a, **k):
        try:
            yield
        except expected_exception:
            return
        except Exception as exc:  # pragma: no cover - diagnostic path
            raise AssertionError(
                f"expected {expected_exception} but a different exception was raised: {exc!r}"
            ) from exc
        else:
            raise AssertionError(f"expected {expected_exception} but no exception was raised")

    _fake_pytest.raises = _raises
    sys.modules["pytest"] = _fake_pytest

# flask_cors isn't installed in this sandbox (no network to pip install it)
# and app.py only uses it for `CORS(app)` at import time with no test
# exercising cross-origin behavior, so a no-op stand-in is enough to let
# app.py import.
if "flask_cors" not in sys.modules:
    import types as _types

    _fake_flask_cors = _types.ModuleType("flask_cors")

    class _CORS:
        def __init__(self, *a, **k):
            pass

    _fake_flask_cors.CORS = _CORS
    sys.modules["flask_cors"] = _fake_flask_cors

import tests.conftest as conftest  # noqa: E402


class MonkeyPatch:
    """Minimal stand-in for pytest's monkeypatch fixture: setattr/delattr on
    objects or dotted-path strings, setitem/delitem on mappings, setenv/
    delenv on os.environ - all undone in reverse order on teardown."""

    _NOTSET = object()

    def __init__(self):
        self._undo = []

    def setattr(self, target, name, value=_NOTSET, raising=True):
        if value is self._NOTSET:
            # monkeypatch.setattr("dotted.module.path", value) form: `name`
            # here is actually the value, and target is the dotted string.
            value = name
            target_str = target
            module_path, _, attr = target_str.rpartition(".")
            target = __import__(module_path, fromlist=[attr]) if "." in target_str else sys.modules[module_path]
            name = attr
        had = hasattr(target, name)
        old = getattr(target, name, None)
        if raising and not had:
            raise AttributeError(f"{target!r} has no attribute {name!r}")
        self._undo.append(("attr", target, name, had, old))
        setattr(target, name, value)

    def delattr(self, target, name, raising=True):
        had = hasattr(target, name)
        old = getattr(target, name, None)
        if raising and not had:
            raise AttributeError(f"{target!r} has no attribute {name!r}")
        if had:
            self._undo.append(("attr", target, name, had, old))
            delattr(target, name)

    def setitem(self, mapping, key, value):
        had = key in mapping
        old = mapping.get(key)
        self._undo.append(("item", mapping, key, had, old))
        mapping[key] = value

    def delitem(self, mapping, key, raising=True):
        had = key in mapping
        old = mapping.get(key)
        if raising and not had:
            raise KeyError(key)
        if had:
            self._undo.append(("item", mapping, key, had, old))
            del mapping[key]

    def setenv(self, name, value):
        self.setitem(os.environ, name, str(value))

    def delenv(self, name, raising=True):
        self.delitem(os.environ, name, raising=raising)

    def undo(self):
        for kind, target, key, had, old in reversed(self._undo):
            if kind == "attr":
                if had:
                    setattr(target, key, old)
                else:
                    try:
                        delattr(target, key)
                    except AttributeError:
                        pass
            else:  # item
                if had:
                    target[key] = old
                else:
                    target.pop(key, None)
        self._undo.clear()


def _monkeypatch_fixture():
    mp = MonkeyPatch()
    try:
        yield mp
    finally:
        mp.undo()


# Fixture builders: name -> (needs, factory). `factory` is called with the
# resolved dependencies (in `needs` order) and may be a generator function
# (yield-style, teardown resumed after the test) or a plain callable.
_FIXTURE_SPECS = {
    "monkeypatch": ([], _monkeypatch_fixture),
    "fake_db": ([], conftest.fake_db),
    "firebase_configured": ([], conftest.firebase_configured),
    "firebase_not_configured": ([], conftest.firebase_not_configured),
    "app_module": ([], conftest.app_module),
    "telegram_api": (["monkeypatch"], conftest.telegram_api),
    "cloudinary_api": (["monkeypatch"], conftest.cloudinary_api),
    "gemini_api": (["monkeypatch"], conftest.gemini_api),
    "chapa_api": (["monkeypatch"], conftest.chapa_api),
    "fake_messaging": (["monkeypatch"], conftest.fake_messaging),
    "fake_firebase_auth": (["monkeypatch"], conftest.fake_firebase_auth),
}


class FixtureContext:
    """Resolves fixtures by name (with dependencies), memoizing per test so
    e.g. two params both needing `monkeypatch` share one instance, and runs
    every generator fixture's teardown half in reverse order at the end."""

    def __init__(self):
        self._values = {}
        self._teardowns = []  # list of generators to advance once more

    def get(self, name):
        if name in self._values:
            return self._values[name]
        if name not in _FIXTURE_SPECS:
            raise LookupError(f"no fixture named {name!r} in this runner")
        needs, factory = _FIXTURE_SPECS[name]
        args = [self.get(dep) for dep in needs]
        result = factory(*args)
        if inspect.isgenerator(result):
            value = next(result)
            self._teardowns.append(result)
        else:
            value = result
        self._values[name] = value
        return value

    def close(self):
        for gen in reversed(self._teardowns):
            try:
                next(gen)
            except StopIteration:
                pass


def run_module(module_name):
    mod = __import__(module_name, fromlist=["*"])
    passed = 0
    failed = 0
    skipped = 0
    failures = []

    def call_with_fixtures(fn):
        if getattr(fn, "__bmt_parametrize_skip__", False):
            return None, None  # skipped: this runner doesn't do parametrize
        params = [
            p for p in inspect.signature(fn).parameters
            if p not in ("self",)
        ]
        unknown = [p for p in params if p not in _FIXTURE_SPECS]
        if unknown:
            return False, f"no fixture(s) named {unknown!r} in this runner"
        ctx = FixtureContext()
        try:
            kwargs = {p: ctx.get(p) for p in params}
            app_module_obj = sys.modules.get("app")
            if app_module_obj is not None:
                app_module_obj._rate_buckets.clear()
            fn(**kwargs)
            return True, None
        except Exception:
            return False, traceback.format_exc()
        finally:
            ctx.close()

    for name in dir(mod):
        obj = getattr(mod, name)
        if inspect.isfunction(obj) and name.startswith("test_") and obj.__module__ == module_name:
            ok, err = call_with_fixtures(obj)
            if ok is None:
                skipped += 1
            elif ok:
                passed += 1
            else:
                failed += 1
                failures.append((f"{module_name}::{name}", err))
        elif inspect.isclass(obj) and name.startswith("Test") and obj.__module__ == module_name:
            instance = obj()
            for meth_name in dir(instance):
                if not meth_name.startswith("test_"):
                    continue
                meth = getattr(instance, meth_name)
                ok, err = call_with_fixtures(meth)
                if ok is None:
                    skipped += 1
                elif ok:
                    passed += 1
                else:
                    failed += 1
                    failures.append((f"{module_name}::{name}::{meth_name}", err))
    return passed, failed, skipped, failures


if __name__ == "__main__":
    modules = sys.argv[1:] or ["tests.test_course_structure_units"]
    total_passed = 0
    total_failed = 0
    total_skipped = 0
    all_failures = []
    for m in modules:
        p, f, s, failures = run_module(m)
        total_passed += p
        total_failed += f
        total_skipped += s
        all_failures.extend(failures)
        print(f"{m}: {p} passed, {f} failed, {s} skipped")
    for name, err in all_failures:
        print(f"\n=== FAILED: {name} ===\n{err}")
    print(f"\nTOTAL: {total_passed} passed, {total_failed} failed, {total_skipped} skipped")
    sys.exit(1 if total_failed else 0)
