# MOA Agent Instructions

## Project scope

MOA is a Mudae optimization and account-analysis application.

Prefer focused, verifiable changes. Do not modify unrelated systems or broadly
refactor the repository unless explicitly requested.

## Model routing policy

Model routing is advisory. Do not claim to have changed the active model.

Before making changes, classify the task:

### Tier 1 — Luna Medium

Use for:

- Renaming
- Formatting and spacing
- Comments and documentation
- Commit preparation
- Tiny UI text or layout adjustments
- Mechanical edits with an obvious implementation

### Tier 2 — Luna High

Use for:

- Clearly scoped bug fixes
- Clearly scoped features
- Changes primarily limited to one to three modules
- Adding tests for established behavior
- Ordinary Discord command parsing and UI work

This is the default tier for MOA development.

### Tier 3 — Luna XHigh or Terra Medium

Use Luna XHigh when the task is difficult but still narrowly scoped.

Recommend Terra Medium before editing when the task requires:

- Exploring several unfamiliar parts of the repository
- Tracing data through multiple layers
- Diagnosing unclear state or concurrency behavior
- Understanding an undocumented subsystem
- Coordinating parser, database, bot, and UI changes

### Tier 4 — Terra High

Recommend Terra High before editing when the task requires:

- A broad refactor
- A new subsystem
- Significant schema or API changes
- Multiple interacting failures
- Careful compatibility work across many modules

### Tier 5 — Sol Medium

Recommend Sol Medium before editing only for:

- Architecture decisions with long-term consequences
- Authentication or authorization
- Security-sensitive code
- Database migrations or possible data loss
- Payment systems
- Complex concurrency or distributed-state correctness
- A difficult task that Terra attempted unsuccessfully with useful evidence

### Sol High, XHigh, or Max

Never recommend these automatically.

Only use them after explicit user approval and only when:

- Lower tiers failed with documented evidence
- The failure could cause security, data-loss, or major architectural damage
- A genuinely difficult root-cause analysis remains unresolved

## Escalation behavior

When the current task appears to need a stronger tier:

1. Do not begin a broad edit.
2. Briefly state the recommended model and reasoning effort.
3. Explain why the current task crosses the escalation threshold.
4. Identify the files or systems likely involved.
5. Wait for the user to change the model or explicitly request proceeding.

Do not escalate solely because a task is long. Escalate because it requires
greater reasoning, broader coordination, or carries higher risk.

## Credit efficiency

- Use the least expensive tier likely to finish correctly.
- Do not repeatedly retry the same failed approach.
- After one failed implementation, inspect the evidence and revise the plan.
- After two materially failed approaches, recommend escalation.
- Do not use high reasoning effort for mechanical work.
- Avoid reading the entire repository when targeted searches are sufficient.
- Keep responses and implementation summaries concise.

## Local performance

- Run only tests relevant to changed files unless the full suite is requested.
- Never run multiple test suites concurrently.
- Use no more than two test workers.
- Do not start persistent Discord bots, servers, listeners, or file watchers
  unless explicitly requested.
- Stop every process started for testing before finishing.
- Avoid repeatedly scanning the entire repository.

Do not recursively inspect these directories unless necessary:

- .git
- .venv
- __pycache__
- .pytest_cache
- build
- dist
- logs
- artifacts
- node_modules

## Editing rules

- Do not modify unrelated files.
- Preserve existing architecture and naming conventions.
- Prefer minimal changes over speculative rewrites.
- Do not silently change database schemas, environment configuration, public
  command behavior, or stored data formats.
- Add or update tests when changing observable behavior.

## Validation

At completion, report:

- Files changed
- Commands and tests run
- Results of validation
- Any unverified behavior
- Any processes started and confirmation that they were stopped