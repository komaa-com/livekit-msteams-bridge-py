---
title: "Contributing"
description: "Local setup, conventions, and the documentation policy."
---

Contributions are welcome.

## Quick start for contributors

```bash
git clone https://github.com/komaa-com/livekit-msteams-bridge-py
cd livekit-msteams-bridge-py
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q            # no LiveKit server needed - the room side is faked
ruff check src tests
ruff format --check src tests
```

- Runtime dependencies: `aiohttp` plus the official `livekit` / `livekit-api` SDKs (pinned to the tested 1.x range; `tests/test_livekit_sdk_surface.py` guards the API surface against drift).
- CI runs ruff + the test suite on Python **3.10 through 3.13** for every push and PR.
- Releases are tagged `v*` and published to PyPI by CI via trusted publishing - the tag must match the `pyproject.toml` version. See `CHANGELOG.md` for the stability notes.
- **Docs live in `website/`** (this site). Any merged change to `website/` redeploys the site automatically.

## Parity with the Node.js sibling

This package mirrors [`@komaa/livekit-msteams-bridge`](https://github.com/komaa-com/livekit-msteams-bridge): same wire contract, same environment variables, same room naming, same behaviors. When you change observable behavior here, check whether the Node implementation needs the same change (and vice versa) so the two stay drop-in interchangeable.

## Documentation policy

Document how to **connect to** the hosted StandIn service and how the bridge behaves on the wire. Do not document the internals of the hosted media bridge - this repository only depends on its published wire contract.
