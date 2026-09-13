# Rights and dependency inventory

This inventory separates code, installed dependencies, rule data and graphics.
It is not legal advice and does not replace the license text supplied by each
dependency or asset owner.

## Project code

Bot Bowl source code is distributed under [Apache License 2.0](LICENSE) with the
original project history and attribution preserved. The maintained fork does not
claim that license covers trademarks, game rules text, third-party libraries or
every repository asset.

## Python dependencies

Dependencies are installed from package indexes and are not vendored into Bot
Bowl wheels. The declared direct families and their upstream license identifiers
at the reviewed versions are:

| Capability | Direct packages | Reported upstream license family |
| --- | --- | --- |
| Build | setuptools, Cython | MIT; Apache-2.0 |
| Core | NumPy, untangle | BSD-3-Clause; MIT |
| Web | Flask | BSD-3-Clause |
| Legacy RL | Gym | MIT |
| Gymnasium | Gymnasium | MIT |
| Competition | Docker SDK for Python, tabulate | Apache-2.0; MIT |
| Render | Matplotlib | PSF-based Matplotlib license |
| Development | pytest, build, more-itertools | MIT; MIT; MIT |

Transitive packages and exact resolved versions are recorded by the dated
constraints in `requirements/` and release environment manifests. Consumers
must inspect the metadata/license files of the versions they redistribute; this
table is not a blanket license grant or a lock for every supported interpreter.
The `python:3.11-slim` container base is pinned by digest for the proposal; its
Debian/Python layer licenses remain those supplied by the image and must be
included in any container-level redistribution review.

## Vendored browser code in built distributions

The optional web subset includes AngularJS (MIT), jQuery (MIT), Bootstrap 3.0.0
(Apache-2.0 as stated in the vendored header), normalize.css (MIT), and
wysihtml5 0.3.0 (MIT, XING AG), including its bundled Rangy code (MIT, Tim Down).
The separate bootstrap3-wysihtml5 wrapper notice
([`LICENCE`](botbowl/web/static/lib/wysiwyg/LICENCE), MIT, JFHollingworth LTD)
is also retained; it does not replace the wysihtml5/Rangy attribution in their
file header. Their retained file headers/notices remain authoritative. The package
manifest includes only the JS/CSS/HTML subset needed by the inherited interface;
it excludes vendored fonts and images.

## Rules, teams and formations

Built distributions contain the existing XML/JSON/text rule, team, arena and
formation data required by the engine. `BB2016` and `LRB5-Experimental` are
resource identifiers, not statements that the source license grants ownership
of the underlying game system or trademarks. No new rules or PR #268 resources
are copied by this release preparation. See [rules inventory](docs/lab/rules.md).

## Graphics and other repository assets

The checkout contains inherited pitch, dice, player, team and interface artwork.
Upstream states that team icons are used with FUMBBL permission and identifies
some icons as originating with Fantasy Football Client. Those statements do not
establish a general license for other uses. Documentation screenshots and legacy
font/image directories also remain outside the package license claim.

Wheel, sdist and default container manifests reject `.png`, `.jpg`, `.jpeg`,
`.gif`, `.svg`, `.eot`, `.ttf`, `.woff` and `.woff2` files. This preserves a
headless, artwork-free distribution. Do not copy additional upstream PR assets,
relicense inherited graphics or publish a complete graphical bundle without a
file-level rights review and any required permissions.
