# Package-page preparation (#126)

The [canonical English README](../README.md) is the single source of the package
long description (`project.readme = "README.md"`). This document is a verification
procedure, not a second marketing body or evidence that a release is published.

## Intended reader and limits

The README addresses individual users and small teams preparing text locally
before a manual external-LLM handoff. It describes both CLI and Library API,
actual Python support, the implemented `dev` extra and synthetic examples.
Pseudonymization is reversible; detector false negatives, keyring assumptions and
restored plaintext remain explicit before usage examples. Security guarantees
are defined by the [technical security model](SECURITY.md).

Root README links use the exact official GitHub `blob/main/` base so package-index
rendering does not depend on a repository-relative URL context. The bilingual
checker maps only that exact HTTPS repository/ref prefix to local targets while
retaining containment, private-file, symlink, encoding and anchor checks. It does
not establish remote availability or semantic translation equivalence. Other
paired guides retain directory-relative links.

The English candidate documents must reach main through a separately approved
roadmap merge before release-facing links can be declared current on main. The
source-install commands are pinned to the delivered #122 integration commit;
README and INSTALLATION pins and both language versions stay synchronized.

## Artifact and rendering verification

Run the canonical build/metadata/install verifier from a clean candidate commit,
using a temporary root outside the checkout:

```bash
python tools/verify_package_build.py --temp-root /tmp/pgw-package-page-build
```

It builds sdist then wheel from that sdist and checks metadata, exact embedded
README, license, Python support, package contents, clean installs and applicable
synthetic installation/smoke flows. A successful build is not publication.

In a separate disposable validation environment install `twine` and
`readme-renderer[md]`, recording their exact resolved versions. Run only checks:

```bash
python -m twine check --strict /tmp/pgw-package-page-build/pgw-build-normal/dist/*
```

Extract the root PKG-INFO long description from sdist and METADATA long description
from wheel. Compare each byte-decoded body exactly with canonical README, render
both with `readme_renderer.markdown.render`, and inspect actual HTML. Check heading,
code/table/link output and that repository hrefs are absolute. Record artifact
hashes, source SHA, tool versions and results in the task delivery evidence.
Local HTML rendering is not an exact preview of every future PyPI UI/style change.

## Release boundary

[Trusted publishing](ADR-114-trusted-publishing.md), the
[release procedure](RELEASE.md) and [post-publication smoke plan](POST_PUBLICATION.md)
remain authoritative. Environment protection, exact source/tag/version, artifact
identity/digest and owner publication approval are still required. This task does
not dispatch publishing, create/move tags, create releases, change settings,
increment the version or upload anything to PyPI. Existing version `0.5.0` and
license/dependency/support metadata remain unchanged.
