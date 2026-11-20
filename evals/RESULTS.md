# Observed validation — November 20, 2026

## Offline workflow

**29 tests passed** with Python3.14 and Pillow13.0.0, including ResourceWarnings
treated as errors. Tests cover decoded format/size/frame validation, exact-byte
originals, PNG/JPEG/WebP, SHA dedup, persistence, Unicode literal search, metadata
limits/SQL parameters, corrupt objects, failed database insert cleanup, stale-draft
transaction guards, proposal validation, request limits, redirects/proxies,
unavailable/nonvision/remote models, no-clobber writes, escaped galleries and CLI
original export.

**5/5 synthetic known-answer checks passed** in
[offline summary](observed/2026-11-20/summary.json): three original byte/hash checks
plus metadata editing/dedup preservation, persistent expected search results and
gallery/JSON export. Alt text/tags are human-authored fixture data. These checks
measure storage/search/export, not image understanding. Fixture drawings were
created with Pillow by `examples/make_fixtures.py`; no external image source or
AI image generator was used.

## Actual model access

The existing workspace Ollama v0.40.13 server was started temporarily with cloud
disabled to inspect already installed model capabilities:

| Installed model | Advertised capabilities |
| --- | --- |
| `qwen3:4b-instruct` | tools, thinking, completion |
| `qwen2.5:3b` | completion, tools |
| `qwen2.5:1.5b` | completion, tools |
| `nomic-embed-text:latest` | embedding |

None advertised vision. The actual `check-model` path refused the selected
text-only model with: `Selected model does not advertise vision; install/configure
a vision model separately`. The refusal happened before image access/transmission.
Full local access evidence is retained in the daily run's `vision-access.json`.
No new model, paid API, hosted image transmission or account connection was used.

**Real AI labeling remains unvalidated and its acceptance requirement is unmet.**
Protocol mocks demonstrate valid/invalid response handling, separate drafts,
unreviewed application, source/prepared-image checks and stale-edit refusal;
they cannot establish visual accuracy or actual vision-model interoperability.
The application is a useful offline milestone toward the larger queued project.

Before calling the AI workflow complete, provide an authorized installed local
vision model, run real descriptions on the synthetic mug/notebook/leaf and broader
images, compare visible facts/unsupported details, and verify editable tags can
retrieve the right images. Product-photo cleanup is not implemented.
