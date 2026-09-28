# Quality Regressions

The contract holds and every call succeeds, but the agent's judgment got worse:
worse answers, skipped steps, wrong tool, gave up early. Tests cannot see this.
Evaluations can, with care.

The harness scores trials. It stores no baseline and compares nothing across
runs. One trial is a sample, not a finding. A low score can also mean a broken
task or a miscalibrated rubric.

```
1. Reproduce the judgment, not an error. Use the closest task, or write one
   that puts the agent in the reported situation. A new task changes what the
   suite measures; get the user's agreement first.
2. Run it against the checkout, trials: 3. Record the scores. They are the only
   baseline that will exist.
3. Read evidence before scores: driver/result.json, driver/events.jsonl,
   workspace/, grader/<evaluation>/report.md. Confirm the agent did the bad
   thing and the grader judged it for the right reason.
4. A/B before touching a rubric. Run the task against the suspected-good and
   suspected-bad configuration, varying exactly one of: install, model, or a
   single setting. The difference is the measurement.
5. Fix, re-run at the same trial count, and compare against step 2.
```

Handoff records the task id, trial count, and scores before and after.
