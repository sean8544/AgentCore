# Migration and Rollout Plan

## Stage 0 - Shadow Mode

- Keep existing runtime configuration path active.
- Use draft save and compile-preview APIs to mirror configurations.
- Compare compiled snapshots with current runtime behavior.

## Stage 1 - Controlled Publish

- Publish profile versions for a small set of non-critical agents.
- Enable permission simulation and HITL policy checks.
- Monitor approval event volume and runtime trace completeness.

## Stage 2 - Production Promotion

- Promote profiles by environment from dev to staging to prod.
- Keep rollback runbook ready with known-good version ids.
- Enforce publish gate for unsupported tool combinations.

## Stage 3 - Legacy Path Deprecation

- Freeze updates to legacy ad hoc configuration paths.
- Export and archive old configuration records.
- Switch all agents to versioned profile activation.

## Deprecation Checklist

- [ ] Every production agent has at least one published profile version.
- [ ] Runtime traces contain profile version ids and summaries.
- [ ] Approval audit events are retained and queryable.
- [ ] Legacy configuration writes are disabled.
- [ ] Rollback drill completed successfully.
