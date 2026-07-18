# MOA Agent Instructions

## Scope

These instructions apply to the entire repository.

## Local performance

- Run only tests relevant to the files being changed unless the user explicitly requests the full test suite.
- Do not start persistent Discord bots, development servers, background listeners, or file watchers unless explicitly requested.
- Stop every process started for testing before finishing the task.
- Never run multiple test commands concurrently.
- Use no more than 2 parallel test workers.
- Avoid repeatedly scanning the entire repository.

## Excluded directories

Do not recursively search or inspect these directories unless necessary:

- .git
- .venv
- __pycache__
- .pytest_cache
- build
- dist
- logs
- artifacts
- node_modules

## Editing

- Do not modify unrelated files.
- Prefer focused changes over broad refactors.
- Preserve the existing project structure and naming conventions.
- Do not replace working implementations without explaining why.

## Validation

- Run targeted tests for changed functionality.
- Report which tests and commands were run.
- Clearly disclose any validation that could not be completed.
