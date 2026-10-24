# Contributing

Report reproducible problems or propose changes through GitHub Issues and pull
requests. Include the software version, operating system, relevant hardware and
steps to reproduce. Remove private experiment information before sharing files.

Use the locked development environment and regression suite:

```powershell
uv sync --locked --group dev --python 3.12
uv run --locked python tools/run_validation.py
```

Preserve supported hardware behavior and the published scientific file contracts.
Add focused regression coverage for changed behavior and use small synthetic
fixtures. Keep hardware capture independent of GUI rendering. See
[application structure](ARCHITECTURE.md) and [system/data checks](TESTING.md).

Do not commit recordings, private configs, credentials, camera vendor wheels or
machine-specific environments. Preserve fixture and firmware bytes and bundled
license notices. Describe the resulting behavior and relevant validation in
each pull request. Contributions follow the project's [MIT license](LICENSE).
