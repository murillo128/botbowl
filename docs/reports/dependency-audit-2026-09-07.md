# Dependency audit — 2026-09-07

pip-audit 2.10.1 queried live PyPI advisories in strict mode, with no ignored
advisories. The inherited 2023 snapshot produced 66 response records: **52 distinct
package/advisory-ID pairs**, with duplicate records preserved in raw evidence.
The updated local Linux 3.11 RL/development/build/tool environment resolved 62
third-party distributions with **zero known advisories**. Hosted audits separately
cover core 3.11/3.14 and RL 3.11. A separate identity scan covers Windows-only
pywin32 312, colorama 0.4.6 and core NumPy 2.4.6 (zero known advisories).

Old snapshot scanning used `pip-audit --strict --disable-pip --no-deps -r FILE
--format json`: this examines recorded identities without resolving the obsolete
whole environment. Current scans first install coherently and run `pip check`,
then audit every installed third-party identity. Only the unpublished local
`botbowl` project is omitted from registry lookup; other CI jobs test that source.
No third-party package or advisory is ignored.

All affected retained families below are upgraded. `zipp` is absent from the
supported resolved environment. Other obsolete unused snapshot packages no longer
install merely because they once appeared in a freeze; pyproject metadata selects
actual runtime/extras/build dependencies.

| Package | Old version | Current CI version | Advisory IDs (deduplicated per package) |
| --- | --- | --- | --- |
| certifi | 2023.5.7 | 2026.7.22 | PYSEC-2023-135, PYSEC-2024-230 |
| click | 8.1.3 | 8.5.0 | PYSEC-2026-2132 |
| flask | 2.3.2 | 3.1.3 | PYSEC-2026-2151 |
| fonttools | 4.39.3 | 4.64.0 | CVE-2023-45139, CVE-2025-66034 |
| idna | 3.4 | 3.19 | PYSEC-2024-60, PYSEC-2026-215 |
| jinja2 | 3.1.2 | 3.1.6 | PYSEC-2026-1471, PYSEC-2026-1472, PYSEC-2026-1473, PYSEC-2026-1474, PYSEC-2026-1475 |
| pillow | 9.5.0 | 12.3.0 | PYSEC-2023-175, PYSEC-2023-227, PYSEC-2026-165, PYSEC-2026-1793, PYSEC-2026-1794, PYSEC-2026-2253, PYSEC-2026-2254, PYSEC-2026-2255, PYSEC-2026-2256, PYSEC-2026-2257, PYSEC-2026-2874, PYSEC-2026-3451, PYSEC-2026-3453, PYSEC-2026-3454, PYSEC-2026-3493, PYSEC-2026-3494, PYSEC-2026-3495, PYSEC-2026-3496, PYSEC-2026-457 |
| pytest | 7.3.1 | 9.1.1 | PYSEC-2026-1845 |
| requests | 2.31.0 | 2.34.2 | PYSEC-2026-1872, PYSEC-2026-1873, PYSEC-2026-2275 |
| urllib3 | 2.0.2 | 2.7.0 | PYSEC-2023-192, PYSEC-2023-212, PYSEC-2026-141, PYSEC-2026-1994, PYSEC-2026-1995, PYSEC-2026-1996, PYSEC-2026-1998, PYSEC-2026-1999 |
| werkzeug | 2.3.3 | 3.1.8 | PYSEC-2023-221, PYSEC-2026-2043, PYSEC-2026-2044, PYSEC-2026-2045, PYSEC-2026-2046, PYSEC-2026-2320, PYSEC-2026-3417 |
| zipp | 3.15.0 | removed | PYSEC-2026-2074 |

Release versions and Requires-Python were checked against the primary
[PyPI JSON API](https://docs.pypi.org/api/json/), including
[certifi](https://pypi.org/pypi/certifi/json),
[Pillow](https://pypi.org/pypi/Pillow/json), and
[urllib3](https://pypi.org/pypi/urllib3/json). Advisory data comes from the PyPI
version endpoints queried by [pip-audit](https://github.com/pypa/pip-audit).
Old upstream PR target versions were not used as current security decisions.

Core pins NumPy 2.4.6 on Python 3.11 and 2.5.3 on 3.12–3.14; legacy RL pins
Gym 0.26.2 and NumPy 1.26.4 on 3.11/3.12. Other direct families are Flask 3.1.3,
Docker 7.2.0, tabulate 0.10.0, pytest 9.1.1, build 1.6.0, more-itertools 11.1.0,
matplotlib 3.11.1, Cython 3.3.0 and setuptools 84.0.0. `requirements/` records
transitive pins. Gym remains unmaintained legacy compatibility work under #17;
absence of a known advisory is not a maintenance or Gymnasium conformance claim.
Library ranges are distinct from exact CI pins: this audit does not certify every
historical version combination allowed by those ranges.

Actions were verified against upstream release metadata and tag-to-commit refs:

| Action | Maintained release | Immutable commit |
| --- | --- | --- |
| [checkout](https://github.com/actions/checkout/releases/tag/v7.0.1) | v7.0.1 | `3d3c42e5aac5ba805825da76410c181273ba90b1` |
| [setup-python](https://github.com/actions/setup-python/releases/tag/v7.0.0) | v7.0.0 | `5fda3b95a4ea91299a34e894583c3862153e4b97` |
| [upload-artifact](https://github.com/actions/upload-artifact/releases/tag/v7.0.1) | v7.0.1 | `043fb46d1a93c77aae656e7c1c64a875d1fc6a0a` |

[actionlint v1.7.12](https://github.com/rhysd/actionlint/releases/tag/v1.7.12)
uses the upstream Linux amd64 archive verified against published SHA-256
`8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8`.
Ruff E9/F63/F7/F82 detects syntax and definite errors without a formatting sweep.
Actionlint checks all workflows; its config declares the existing `codex` runner
label without changing the launcher or its restrictions.

Raw audit JSON, resolved environments and logs live under
`/tmp/botbowl-issue6-evidence/`. Hosted artifacts retain their exact source revision
and inventory. [The CI report](ci-issue-6.md) records job results and negative
controls. These are dated observations; advisory data can change after this date.

Additional bootstrap finding: captured 3.11 venv inventories retained setuptools
65.5.0 on Windows/macOS and 79.0.1 on Linux from ensurepip, despite the build and
audit profiles using 84.0.0. Explicit identity audits found advisories in both
seed versions. Fresh CI venvs and the fresh-sdist native install now upgrade
pip/setuptools under the same audited constraints before installing Bot Bowl.
The native sdist smoke also records its complete freeze. This closes the gap
between a constrained build environment and the independently seeded installer.

- setuptools 65.5.0: PYSEC-2022-43012, PYSEC-2025-49, PYSEC-2026-1918, PYSEC-2026-3447; upgraded to 84.0.0.

- setuptools 79.0.1: PYSEC-2026-3447; upgraded to 84.0.0.
