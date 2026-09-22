# MOA Agent Instructions

## Project scope

MOA is a Mudae optimization and account-analysis application.

Prefer focused, verifiable changes. Do not modify unrelated systems or broadly
refactor the repository unless explicitly requested.

## Model routing policy

Model routing is advisory. Do not claim to have changed the active model.

Before making changes, classify the task and route it as follows.

### GPT-6 Luna Medium

Use for:

- Documentation
- Comments
- Mechanical refactors
- Low-risk test cleanup
- Formatting
- Metadata work

### GPT-6 Luna High

Use for:

- Normal scoped implementation
- Parser repairs
- CLI changes
- Ordinary listener wiring
- Service or repository features with already-characterized behavior
- Focused test additions

This is the default tier for MOA implementation.

### GPT-6 Sol High

Use for:

- Independent read-only characterization
- Independent review and closeout
- Privacy-sensitive capture or tooling
- Security-sensitive implementation
- Correctness-sensitive data semantics
- Subtle listener or parser investigations
- Database or data-model work where a wrong assumption could corrupt meaning
- Complex migration behavior
- Source-authority or publication verification

For privacy-, data-, durability-, or security-sensitive implementation, use
GPT-6 Sol High and use GPT-6 Sol High in a fresh session for independent
closeout.

### GPT-6 Sol Medium

Use only for bounded runtime or operational source tasks after
characterization has resolved the design. Privacy- or security-sensitive
implementation routes to GPT-6 Sol High.

### GPT-6 Astra

Use only when work is genuinely cross-cutting, architecture-heavy, cannot be
safely decomposed, and requires high reasoning across several subsystems. Do
not use Astra for routine implementation.

### Default workflow

- Characterization: GPT-6 Sol High, start a new session.
- Implementation: GPT-6 Luna High, start a new session.
- Independent closeout: GPT-6 Sol High, start a new session.
- For privacy-, data-, durability-, or security-sensitive implementation:
  use GPT-6 Sol High for implementation and GPT-6 Sol High in a new session
  for independent closeout.

Review independence comes from a fresh session and independent evidence and
revalidation; it does not require a different model.

An explicit user authorization may cross a model-routing recommendation
where existing policy permits it. This exception applies only to model
routing; it does not bypass source-authority gates, production safety,
privacy controls, credential controls, destructive-operation safeguards,
Git mutation authorization, or orchestrator authorization.

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

## Discord ingestion safety

- Every listener or parser change must include a sanitized real-payload fixture.
- Tests must cover multiple configured users sharing one Discord channel.
- Never attribute a response using channel-wide mutable context alone.
- Ambiguous account attribution must be preserved as unresolved, never guessed.
- Every persisted Discord event must have a durable source identity.
- Raw-event persistence and projection updates must be transactional.
- Duplicate/replayed Discord events must be idempotent across process restarts.
- Listener and repository tests must use temporary databases.
- Never run migrations, repairs, or integration tests against the live database.
- Do not split large modules until characterization tests protect their behavior.