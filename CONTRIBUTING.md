# Contributing to Open News Monitor

Thank you for improving Open News Monitor.

## Before you start

1. Search existing issues and open a focused issue before beginning substantial work.
2. Keep one pull request limited to one problem or improvement.
3. Never commit `.env` files, API keys, tokens, personal profiles, or collected private data.

## Development checks

Open News Monitor supports Python 3.11 and newer. Install the project and run its test suite before opening a pull request:

```bash
python -m pip install .
python -m unittest discover -s tests
```

Please update documentation and example configuration whenever a user-facing behavior changes.

## Pull requests

Explain what changed, why it changed, and how you verified it. Changes that add a new source should include a direct public source URL and a short note about the source format.
