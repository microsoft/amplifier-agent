# Examples

Example applications built on Amplifier Agent, for learning and copying. Each one uses only the public exports of
`amplifier_agent`, keeps its library calls in one place, and says in its README where to look.

Each example is its own uv project with a pinned revision of the library. You need [uv](https://docs.astral.sh/uv/),
Git, and a credential for the example's provider, such as `OPENAI_API_KEY`. Run an example from its folder:

```bash
cd examples/python/<example>
uv run <command>
```

## Index

- [`python/llm-wiki/`](python/llm-wiki/)
  - builds a cross-linked markdown wiki of the Amplifier repositories with many parallel sessions; shows sessions,
    streamed events, caller tools, an approval handler on exact arguments, skills, and usage
