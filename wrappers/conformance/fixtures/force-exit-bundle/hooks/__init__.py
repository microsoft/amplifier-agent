# Marker file so `hooks.force_exit` resolves as a Python module path under
# the conformance bundle's base_path. The engine's bundle loader imports the
# hook via `importlib`; without this `__init__.py` the import would fail
# on environments where the bundle's `base_path` is not already on sys.path
# as a package root.
#
# Intentionally empty — no re-exports. Keeping the surface area at exactly
# zero is part of the "conformance-only, do not extend" contract of this
# bundle (see ../bundle.yaml).
