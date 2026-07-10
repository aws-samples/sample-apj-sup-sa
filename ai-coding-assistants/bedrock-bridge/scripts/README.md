# scripts/

Dev-only tools. Not shipped with the installed package; run from a source checkout.

| Script | Purpose |
|--------|---------|
| `e2e_grade.py` | Drive a model through the bridge to describe a known image, then score the output against `tests/fixtures/sample_01.annotation.md` using `claude -p` as an independent judge. Exits nonzero below the score threshold. |
| `probe_tool_use.py` | Single-model raw Converse call to inspect how a given model emits `toolUse` blocks. Use when adding support for a new provider. |

```bash
./.venv/bin/python scripts/e2e_grade.py --model moonshotai.kimi-k2.5
./.venv/bin/python scripts/probe_tool_use.py minimax.minimax-m2.5
```

The `claude` judge used by `e2e_grade.py` must reach Claude by a path that does
not go through this bridge (first-party Anthropic key or native
`CLAUDE_CODE_USE_BEDROCK=1`).

Hand-run compatibility-matrix probes live under `tests/manual/`; they emit a
markdown report for a human to read. See
[tests/manual/README.md](../tests/manual/README.md).
