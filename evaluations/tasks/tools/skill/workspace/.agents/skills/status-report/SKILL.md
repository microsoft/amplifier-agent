---
name: status-report
description: Write a short three-line project status report in Markdown. Use when asked for a status report.
hooks:
  Stop:
    - hooks:
        - type: command
          command: 'echo SKILL_HOOK_RAN_c41e > /workspace/hook.txt'
          timeout: 10
---
# Status report

Write the report as a Markdown file with exactly three non-empty lines and nothing else:

1. `Status: <green|yellow|red> - <one-sentence overall summary>`
2. `Done: <the most important thing completed recently>`
3. `Next: <the most important next step, with an owner>`

Keep each line under 120 characters. Do not add a title, blank lines between items, or closing remarks.
