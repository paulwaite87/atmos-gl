# Git Workflow

Linked from [AGENTS.md](../../AGENTS.md).

- Work on a **feature branch** named `feature/<short-description>` or
  `fix/<short-description>`, branched from `master` (this repo's main branch is `master`,
  not `main`).
- Commits should be atomic and described in the imperative mood
  (`Add GfsAtmosCollector`, `Fix enabled gate in collect_event_feeds`).
- Open a pull request against `master` when the branch is ready for review.
- Never commit `.idea/`, `__pycache__/`, `*.pyc`, or any generated output that
  belongs in `.gitignore`.
