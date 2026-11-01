# Security policy

## Supported versions

thetarelay is in alpha (0.x). Until 1.0, security fixes go only to `main` and to the next release on PyPI. Upgrade with `uv tool upgrade thetarelay`.

## Reporting a vulnerability

Please do not open a public issue. Report privately through GitHub: on the repository page open **Security > Report a vulnerability** ([direct link](https://github.com/thetaplex/thetarelay/security/advisories/new)).

Include the affected version or commit, steps to reproduce, and the impact you expect. We aim to acknowledge reports within 3 working days and to agree a disclosure date with you once a fix is ready.

Issues of particular interest: leaked secrets (API keys, provider credentials) in output, logs or files.
