#!/usr/bin/env python3
"""Copy AWS Architecture Icons into the aws-diagram-design skill assets,
and generate INDEX.md + aws-icons.html gallery.

Usage:
    python3 scripts/build_aws_icons.py /path/to/Icon-package_<release>

The icon package is the official AWS Architecture Icons "Asset Package"
(https://aws.amazon.com/architecture/icons/), unzipped. Output goes to this
skill's own assets/aws-icons/ — rerun after downloading a newer release.
"""
import pathlib
import shutil
import html
import sys

if len(sys.argv) != 2 or not pathlib.Path(sys.argv[1]).is_dir():
    sys.exit("usage: build_aws_icons.py <path-to-unzipped-AWS-icon-package>")
PKG = pathlib.Path(sys.argv[1]).resolve()
SKILL = pathlib.Path(__file__).resolve().parent.parent
OUT = SKILL / "assets" / "aws-icons"

CATEGORY_COLORS = {
    "Analytics": "#8C4FFF",
    "Application-Integration": "#E7157B",
    "Artificial-Intelligence": "#01A88D",
    "Blockchain": "#ED7100",
    "Business-Applications": "#DD344C",
    "Cloud-Financial-Management": "#7AA116",
    "Compute": "#ED7100",
    "Containers": "#ED7100",
    "Customer-Enablement": "#C925D1",
    "Databases": "#C925D1",
    "Developer-Tools": "#C925D1",
    "End-User-Computing": "#01A88D",
    "Front-End-Web-Mobile": "#DD344C",
    "Games": "#8C4FFF",
    "General-Icons": "#232F3E",
    "Internet-of-Things": "#7AA116",
    "Management-Tools": "#E7157B",
    "Media-Services": "#ED7100",
    "Migration-Modernization": "#01A88D",
    "Networking-Content-Delivery": "#8C4FFF",
    "Quantum-Technologies": "#ED7100",
    "Satellite": "#C925D1",
    "Security-Identity": "#DD344C",
    "Storage": "#7AA116",
}

def copy_service():
    entries = {}
    src_root = PKG / "Architecture-Service-Icons_07312026"
    for cat_dir in sorted(src_root.iterdir()):
        if not cat_dir.is_dir():
            continue
        cat = cat_dir.name.removeprefix("Arch_")
        dst = OUT / "service" / cat
        dst.mkdir(parents=True, exist_ok=True)
        files = []
        for svg in sorted((cat_dir / "48").glob("*.svg")):
            shutil.copy2(svg, dst / svg.name)
            files.append(svg.name)
        entries[cat] = files
    return entries

def copy_resource():
    entries = {}
    src_root = PKG / "Resource-Icons_07312026"
    for cat_dir in sorted(src_root.iterdir()):
        if not cat_dir.is_dir():
            continue
        cat = cat_dir.name.removeprefix("Res_")
        files = []
        dst = OUT / "resource" / cat
        for svg in sorted(list(cat_dir.rglob("*_48.svg")) + list(cat_dir.rglob("*_48_Light.svg"))):
            if "_Dark" in str(svg):
                continue
            dst.mkdir(parents=True, exist_ok=True)
            shutil.copy2(svg, dst / svg.name)
            files.append(svg.name)
        if files:
            entries[cat] = files
    return entries

def copy_group():
    dst = OUT / "group"
    dst.mkdir(parents=True, exist_ok=True)
    files = []
    for svg in sorted((PKG / "Architecture-Group-Icons_07312026").glob("*.svg")):
        shutil.copy2(svg, dst / svg.name)
        files.append(svg.name)
    return files

def copy_category():
    dst = OUT / "category"
    dst.mkdir(parents=True, exist_ok=True)
    files = []
    for svg in sorted((PKG / "Category-Icons_07312026" / "Arch-Category_48").glob("*.svg")):
        shutil.copy2(svg, dst / svg.name)
        files.append(svg.name)
    return files

def pretty(name: str) -> str:
    n = name
    for pre in ("Arch_", "Res_", "Arch-Category_"):
        n = n.removeprefix(pre)
    n = n.removesuffix("_48.svg").removesuffix("_32.svg").removesuffix(".svg")
    return n.replace("_", " / ").replace("-", " ")

def write_index(service, resource, group, category):
    lines = [
        "# AWS Architecture Icons — asset index",
        "",
        "Official AWS Architecture Icons, release 07312026. Copied from the AWS icon",
        "package (SVG, 48px light variants; group icons 32px). Look up the file here,",
        "then Read it and inline its contents per `references/primitive-aws-icons.md`.",
        "",
        f"Totals: {sum(len(v) for v in service.values())} service, "
        f"{sum(len(v) for v in resource.values())} resource, "
        f"{len(group)} group, {len(category)} category icons.",
        "",
        "## Service icons (`service/<Category>/`)",
        "",
    ]
    for cat, files in service.items():
        color = CATEGORY_COLORS.get(cat, "")
        lines.append(f"### {cat} ({color})")
        lines.append("")
        for f in files:
            lines.append(f"- `service/{cat}/{f}`")
        lines.append("")
    lines += ["## Resource icons (`resource/<Category>/`)", ""]
    for cat, files in resource.items():
        lines.append(f"### {cat}")
        lines.append("")
        for f in files:
            lines.append(f"- `resource/{cat}/{f}`")
        lines.append("")
    lines += ["## Group icons (`group/`)", ""]
    for f in group:
        lines.append(f"- `group/{f}`")
    lines += ["", "## Category icons (`category/`)", ""]
    for f in category:
        lines.append(f"- `category/{f}`")
    lines.append("")
    (OUT / "INDEX.md").write_text("\n".join(lines), encoding="utf-8")

def write_gallery(service, resource, group, category):
    def section(title, items):
        cells = "\n".join(
            f'<figure><img src="{html.escape(rel)}" alt="" loading="lazy" width="48" height="48">'
            f"<figcaption>{html.escape(pretty(pathlib.Path(rel).name))}</figcaption></figure>"
            for rel in items
        )
        return f"<h2>{html.escape(title)}</h2>\n<div class='grid'>\n{cells}\n</div>"

    parts = []
    for cat, files in service.items():
        parts.append(section(f"Service — {cat}", [f"aws-icons/service/{cat}/{f}" for f in files]))
    parts.append(section("Group", [f"aws-icons/group/{f}" for f in group]))
    parts.append(section("Category", [f"aws-icons/category/{f}" for f in category]))
    for cat, files in resource.items():
        parts.append(section(f"Resource — {cat}", [f"aws-icons/resource/{cat}/{f}" for f in files]))
    body = "\n".join(parts)
    doc = f"""<meta charset="utf-8">
<title>AWS Architecture Icons — gallery</title>
<style>
  body {{ font-family: 'Amazon Ember', 'Helvetica Neue', Arial, sans-serif; background: #ffffff;
         color: #232F3E; margin: 2rem; }}
  h1 {{ font-weight: 700; }}
  h2 {{ font-size: 1rem; border-bottom: 1px solid rgba(35,47,62,0.15); padding-bottom: 4px;
       margin-top: 2rem; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 12px; }}
  figure {{ margin: 0; padding: 8px; border: 1px solid rgba(35,47,62,0.10); border-radius: 4px;
           text-align: center; }}
  figcaption {{ font-size: 10px; color: #545B64; margin-top: 6px; word-break: break-word; }}
</style>
<h1>AWS Architecture Icons (07312026)</h1>
<p>Local gallery — relative <code>&lt;img&gt;</code> references into <code>assets/aws-icons/</code>. Open this file directly in a browser.</p>
{body}
"""
    (SKILL / "assets" / "aws-icons.html").write_text(doc, encoding="utf-8")

if OUT.exists():
    shutil.rmtree(OUT)
service = copy_service()
resource = copy_resource()
group = copy_group()
category = copy_category()
write_index(service, resource, group, category)
write_gallery(service, resource, group, category)
print("service:", sum(len(v) for v in service.values()), "resource:", sum(len(v) for v in resource.values()),
      "group:", len(group), "category:", len(category))
print("done ->", OUT)
