"""AutomationBench support module for the automation_bench task loader.

Mirrors `eval.task_loaders.swe_bench_support`: pure fetch helpers with no
dependency on the evaluation library. AutomationBench task data is NOT a
HuggingFace parquet -- it lives in-code as domain getters inside the
`zapier/AutomationBench` package (MIT). So instead of downloading a dataset row,
we:

- `dataset.ensure_repo()` -- shallow-clone the pinned AutomationBench commit ONCE
  per machine and reuse the local checkout for every task and every run.
- `dataset.fetch_task()` -- run the existing extractor (`extract_task.py`) in an
  isolated uv env with the clone on PYTHONPATH, returning the task_info dict that
  used to be vendored as `task_info.json`.

No AutomationBench task content is stored in this repo anymore.
"""
