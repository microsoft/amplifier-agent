"""SWE-bench Pro support modules for the swe_bench task loader.

Copy-adapted verbatim from the proven prior-art `swe_bench_pro/` package (the
working implementation under `.amplifier/evaluation/swe-bench-pro/`). These are
pure fetch/convert helpers with no dependency on the evaluation library:

- `dataset`         -- fetch one instance row on the fly from HuggingFace.
- `official_assets` -- shallow-clone the scaleapi repo for the per-instance
                       base+instance Dockerfiles, run_script.sh, and parser.py.
- `dockerfile_convert` -- convert the official Dockerfiles into a DTU/Incus
                       provision profile (Docker Hub images cannot be pulled by
                       the Incus engine, so the environment is reconstructed).

The deterministic grading logic (build the entry script + score parsed test
statuses) lives with the grader in `eval.graders.deterministic`, since the
grader owns the trust-critical verdict.
"""
