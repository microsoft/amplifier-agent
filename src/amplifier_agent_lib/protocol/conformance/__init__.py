"""Cross-language wire conformance — shared YAML fixtures + loader.

Per design §4.6 / §8 D7.  The TS and Py wrapper conformance harnesses
(authored in a later plan) consume the SAME ``fixtures/*.yaml`` files
through language-specific loaders.  This module's ``loader`` is the
Python-side reference implementation and structural validator.
"""
