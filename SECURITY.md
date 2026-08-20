# Security Policy

## Supported versions

Security fixes are applied on a best-effort basis to the current `main` branch only. There are no long-term support releases yet; this project is early-stage.

| Branch / tag | Supported |
|--------------|-----------|
| `main`       | Yes       |
| Older commits / forks | No dedicated backports |

## Reporting a vulnerability

Please **do not** open a public GitHub Issue for security-sensitive reports (credential leaks, remote code paths, malicious PDF handling, etc.).

Prefer one of:

1. **GitHub Security Advisories** — use [Report a vulnerability](https://github.com/sdsdsssssdsd/PDF2Typora-Studio/security/advisories/new) on this repository (private to maintainers).
2. **Email** — `2790805903@qq.com` with subject starting with `[SECURITY] PDF2Typora-Studio`.

Include enough detail to reproduce (version or commit, OS, steps). Do not attach live API keys; redact secrets and rotate any key that may have been exposed.

You should hear back within a few days when the maintainer is available. Please give a reasonable window before any public disclosure.

## Secrets and API keys

- Never commit API keys, tokens, or `.env` files.
- Do not paste keys into Issues, Discussions, or PR descriptions.
- Configure providers in the app UI; credentials are stored via the OS keyring (`config/user.yaml` must stay local).
- If a key appears in a PR or log by mistake, revoke/rotate it immediately and tell the maintainers privately.

## Scope notes for this project

Relevant attack surface includes:

- **External API credentials** (OpenAI-compatible / DeepSeek-style providers)
- **Local Ollama process management** (subprocess start/stop, HTTP to localhost)
- **PDF and image parsing** (PyMuPDF render/clip; untrusted PDFs can be large or crafted)

Treat untrusted PDFs like untrusted files: open them only in a project you control, and keep workspace/export directories out of public shares when they contain sensitive content.
