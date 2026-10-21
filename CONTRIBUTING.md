# Contributing

Use Python 3.12 on Windows for the primary development environment:

```powershell
uv sync --locked --group dev --python 3.12
uv run --locked python tools/run_validation.py
```

Read [ARCHITECTURE.md](ARCHITECTURE.md) before changing module boundaries and
[TESTING.md](TESTING.md) for installed-package and hardware checks. Add focused
regressions for acquisition ownership, file publication, timing or scientific
behavior changed by a feature. Keep capture/writer work independent of Qt widgets.
Register new pages by stable key and reuse the existing theme and services.

Software versions and scientific format versions are independent. Update the
single `__version__` in `deepecohab_recorder/__init__.py` for application releases;
change scientific schemas only for an intentional public contract change.

Do not commit recordings, private experiment configs, camera vendor wheels,
machine-specific environments or reference checkouts. Use small synthetic fixtures
for new tests. The reference copy and `Duplicate_test` remain local and excluded
from publication. Preserve original fixture/firmware bytes and bundled resource
hashes; `.gitattributes` prevents Git line-ending conversion for those files.
Generated checks and investigation outputs belong under `.artifacts/`.

Describe the concrete behavior changed and the checks performed in each pull
request. Include SDK/firmware versions for hardware changes and distinguish
simulated results from physical acceptance. See [LICENSE_STATUS.md](LICENSE_STATUS.md)
for the project's pending license decision.
