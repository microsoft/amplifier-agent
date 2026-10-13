---
name: py-count
description: Count Python source files under a directory. Use when asked how many .py files a directory holds.
context: fork
agent: counter
allowed-tools: [bash, glob]
---
# Python file count

Count the regular files whose names end in `.py` under the directory named in $ARGUMENTS, recursively. Use one
command, such as `find <directory> -name '*.py' -type f | wc -l`.
