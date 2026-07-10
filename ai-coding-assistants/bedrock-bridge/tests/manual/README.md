# tests/manual/

Compatibility-matrix probes, run by hand. They emit a markdown report showing
which Bedrock models work through the bridge. pytest never collects this
directory (`norecursedirs` in `pyproject.toml`); these are report generators
with no assertions.

| Script | Purpose |
|--------|---------|
| `compat_matrix.py` | Direct Bedrock Converse probes per model (text, tool_use, image-in-tool-result, stream). No bridge or Claude Code involved. Fastest signal on whether a model accepts the request shapes. |
| `matrix_e2e.py` | Spawns `bedrock-bridge` plus a real Claude Code `--print` session per model, classifies the result by tailing the bridge log. Slower; covers what `compat_matrix.py` cannot. |

```bash
./.venv/bin/python tests/manual/compat_matrix.py --region ap-northeast-1
./.venv/bin/python tests/manual/matrix_e2e.py --only kimi qwen
```

`matrix_e2e.py` writes its table to `/tmp/bridge_matrix.md`; per-run bridge logs
land in `/tmp/bedrock-bridge-<port>.log`. The image turn uses the shared fixture
`tests/fixtures/sample_01.jpg`.
