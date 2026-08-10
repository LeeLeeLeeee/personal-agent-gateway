# Team Run workspace inheritance

A new Team Run may continue from one terminal Team Run by copying its safe workspace files into a new isolated workspace.

## Contract

- `parent_team_run_id` records the direct parent in `team_runs`.
- The parent must be terminal when the child is created.
- The child Team SPACE must use `isolated` write mode.
- Copying is point-in-time. Parent and child files are independent after creation.
- Git metadata, secret environment files, dependency/cache directories, symlinks, `_inputs`, and PAG delivery state are excluded. The non-secret `.env.example` template is included.
- `artifacts/workspace-inheritance.json` records copied paths, sizes, and SHA-256 hashes.
- Agents continue to receive the child `working_root`; runtime execution needs no special inheritance branch.

This deliberately supports one parent and copy-on-create semantics. It avoids shared-write races and merge rules while preserving a simple lineage for the mostly sequential Team Run workflow.
