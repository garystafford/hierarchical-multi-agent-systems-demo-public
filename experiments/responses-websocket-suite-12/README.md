# Twelve-task WebSocket evaluation

See the [project README](../../README.md) for setup, offline checks, paid execution and generated output locations. [DESIGN.md](DESIGN.md) describes the evaluation controls.

The implementation uses GPT-6.1 Sol with medium reasoning through the Responses API over WebSocket. The two arm identifiers are `single` (Single agent) and `native` (Subagents enabled). The model chooses delegation assignments, count and depth; an enabled run with zero subagents is a valid observation.

The distributed `cases/` directory includes task indexes, immutable evidence, source manifests and reference answers used by local scoring. `read_evidence` exposes only indexed evidence IDs, and excludes reference answers and credentials.
