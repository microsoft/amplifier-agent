"""Feature test suites for the e2e harness.

Each feature is a package: ``cases.py`` holds the ``E2ECase``/``Step`` data, and
``test_<feature>.py`` is a thin pytest module that parametrizes over it. See
docs/E2E_TESTING.md for the framework/suites split and how to add a new feature.
"""

from __future__ import annotations
