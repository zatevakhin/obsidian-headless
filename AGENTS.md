AGENTS Guide - Behavior, Build, Test, Lint and Style

Agent behavior:
- Create todo's using structured todo list available as a tool.
- Be short and concise, don't do things user didn't asked you.
- If user reject changes you should stop and clarify the reason.

Build / Setup:
- Development envronment is managed by `nix` `devenv` automatically (agent is landing in prepared envronment).
- Re install or install dependencies can be done using `pip install` or with `-r` to do from `requirements.txt`

Test commands:
- Run test with `PYTHONPATH=.`
- Run full test suite: `pytest -q`

Lint:
- Lint with Ruff: `ruff check .`

Code style:
- Types: prefer explicit typing for public functions and module interfaces.
- Naming: snake_case for functions/variables, PascalCase for classes, UPPER_SNAKE for constants.
- Errors: raise specific exceptions; avoid bare except: clauses; log and re-raise with context where appropriate.
- Tests: keep tests deterministic, use fixtures for setup, name tests test_<behavior>.

