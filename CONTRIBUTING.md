# Contributing

Thanks for helping improve this project.

## Development setup

1. Fork the repository and clone your fork.
2. Copy `.env.example` to `.env` and provide local-only values.
3. Install the documented dependencies.
4. Run the test suite before making changes.

## Branches

Use short, focused branches:

- `feat/<short-name>`
- `fix/<short-name>`
- `docs/<short-name>`
- `refactor/<short-name>`

## Tests

Run the project-specific commands documented in `README.md`. New behavior should include automated tests when practical.

## Commits

Use Conventional Commits:

`feat(scope): add a useful capability`

Examples: `fix(queue): prevent duplicate jobs`, `docs(api): clarify authentication`.

## Pull requests

Every pull request should explain the problem, solution, implementation, testing, risks, and any screenshots or migration notes. Keep changes focused and update documentation when behavior changes.

Before requesting review:

- [ ] Tests pass locally
- [ ] Documentation is updated
- [ ] No secrets or generated files are included
- [ ] Breaking changes are clearly documented
