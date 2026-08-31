# Changelog

All notable changes to this plugin are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-09-01

First release, packaged as an installable Claude Code plugin.

### Added

- `local-delegate` skill: queue mechanical work and run it on local Ollama
  models through a second Claude Code, at no subscription-token cost.
- Four profiles — `code`, `fast`, `text`, `tiny` — chosen by the nature of the
  task, backed by whichever models the machine can actually hold.
- Queue-then-flush execution: tasks are grouped by model and each model is
  loaded exactly once per flush, heaviest to lightest.
- Guided setup (`references/setup.md`) that measures the machine, researches
  current models, and verifies the ones it installs.
- Update flow (`references/update.md`) for revisiting the model choice later.
- Scripts, Python 3 standard library only:
  `local_agent.py`, `config.py`, `check_machine.py`, `make_profile.py`,
  `ollama_api.py`, `selfcheck.py`, `backup_models.py`.

[1.0.0]: https://github.com/saides-code/local-delegate/releases/tag/v1.0.0
