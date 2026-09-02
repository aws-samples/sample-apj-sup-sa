# Third-party licenses

aws-diagram-design is MIT-licensed (see [`LICENSE`](LICENSE)). This Agent Plugins packaging is a derivative of
[masangbeom/aws-diagram-design](https://github.com/masangbeom/aws-diagram-design) (MIT, © Sangbeom), itself a derivative of
[cathrynlavery/diagram-design](https://github.com/cathrynlavery/diagram-design) (MIT, © Cathryn Lavery),
re-skinned to the AWS brand with the official AWS Architecture Icons bundled. It redistributes
content from the following third-party sources under their respective licenses.

## diagram-design (upstream base)

- **License:** MIT
- **Upstream:** https://github.com/cathrynlavery/diagram-design (base version 2.3.2)
- **Used in:** the entire skill structure — `skills/aws-diagram-design/` (SKILL.md, references, templates, examples, scripts) and the thin `skills/export-diagram/`, `skills/import-drawio/`, `skills/import-mermaid/` wrappers.

## AWS Architecture Icons

- **License / terms:** AWS Architecture Icons terms — https://aws.amazon.com/architecture/icons/
- **Copyright:** © Amazon Web Services, Inc. or its affiliates.
- **Release:** Icon package 07312026.
- **Used in:** `skills/aws-diagram-design/assets/aws-icons/` (812 SVGs: 305 service, 466 resource, 15 group, 26 category), the gallery `assets/aws-icons.html`, and `references/primitive-aws-icons.md`.

Provided by AWS for use in architecture diagrams per the terms above. The icons must not be
altered, recolored, or used to imply AWS sponsorship of non-AWS products.

## Amazon Ember (font — bundled)

- **License / terms:** Amazon Ember Licensing Guidelines — shipped alongside the fonts at
  `skills/aws-diagram-design/assets/fonts/Amazon-Ember-Licensing-Guidelines.pdf`.
- **Copyright:** © Amazon Technologies, Inc.
- **Source:** Amazon's public typography download,
  https://developer.amazon.com/en-US/alexa/branding/echo-guidelines/identity-guidelines/typography
  (`Amazon_Typefaces_Complete_Font_Set_Mar2020.zip`), redistributed unmodified.
- **Used in:** `skills/aws-diagram-design/assets/fonts/` — woff2 webfonts (Amazon Ember
  300/400/400i/600/700/800, Amazon Ember Mono 400/700) referenced by generated diagrams via
  `@font-face`, and desktop TTFs installable with `skills/aws-diagram-design/scripts/install_fonts.sh`.

Diagrams always declare the full fallback stack
(`'Amazon Ember', 'Helvetica Neue', Helvetica, Arial, sans-serif`); environments that cannot
load the bundled files render the fallbacks. Review the licensing guidelines PDF before
reusing the fonts outside this skill.

## Geist Mono

- **License:** SIL Open Font License 1.1
- **Upstream:** https://vercel.com/font (loaded at render time from Google Fonts)
- **Used in:** legacy pre-baked example assets only (`skills/aws-diagram-design/assets/example-*.html`, built under earlier skins) and the opt-in terminal variant — loaded via `fonts.googleapis.com` at render time, not bundled. Newly generated diagrams use the bundled **Amazon Ember Mono** for all mono roles instead.

## Tabler Icons

- **License:** MIT
- **Upstream:** https://github.com/tabler/tabler-icons
- **Used in:** stroked icons embedded in `skills/aws-diagram-design/references/primitive-icons.md` and `skills/aws-diagram-design/assets/icons.html` (categories: Compute, People, Network, Data, Kubernetes, Action, DevOps, plus the stroked Brand outlines for Docker, Terraform, AWS, Azure, GitHub).

## Simple Icons

- **License:** CC0 1.0 Universal (Public Domain Dedication)
- **Upstream:** https://github.com/simple-icons/simple-icons
- **Used in:** filled brand silhouettes embedded in `skills/aws-diagram-design/references/primitive-icons.md` and `skills/aws-diagram-design/assets/icons.html` (Kubernetes, Google Cloud, PostgreSQL, Nginx, Gitea, Keycloak, MinIO, Apache NiFi, Apache Airflow, Trino, Apache Superset, Jupyter, Python, R).

## log-z/logos

- **License:** MIT
- **Upstream:** https://github.com/log-z/logos/tree/main/website-logos
- **Used in:** filled brand silhouettes embedded in the same generic icon set — MySQL, Redis, StarRocks.

## Devicon

- **License:** MIT
- **Upstream:** https://github.com/devicons/devicon
- **Used in:** the RStudio and SPSS icons embedded in the generic icon set.

## One-off sourced icons

The generic icon set embeds one-off sourced icons for SAS, from [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:SAS_logo_horiz.svg) (public domain), and Stata, from the [IcePanel Technology Icons collection](https://icon.icepanel.io/Technology/svg/Stata.svg) published via techicons.dev.

## Trademarks

Brand logos remain the trademarks of their respective owners. Their inclusion in this icon set
is for documentation and illustrative use only. The presence of a brand mark in this repository
does not imply endorsement, sponsorship, or affiliation.
