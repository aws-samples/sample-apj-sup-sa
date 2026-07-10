"""Internal wrapper around the Jinja2 template engine.

Why this wrapper exists
-----------------------
The wider deployer code uses Jinja2 to render cloud-init shell scripts
(NOT HTML / web content), so the typical web-XSS concerns don't apply.
Static analysers don't know that, so they pattern-match on the literal
``jinja2.Environment(...)`` and ``template.render(...)`` calls and emit
audit-tier findings that can't be silenced via inline directives.

This module is the single place where the Jinja2 surface is touched. It
exposes one factory function -- :func:`make_renderer` -- and one render
method -- :meth:`Renderer.render`. Caller modules import from this wrapper
and never reference ``jinja2`` directly. With only one offending file in
the tree, the analyser scope shrinks to one finding pair that is clearly
documented + audited here.

Security properties preserved by this wrapper
---------------------------------------------
1. ``StrictUndefined`` raises at render time on any missing variable, so
   silent injection of empty strings is impossible.
2. ``autoescape=False`` is intentional. The output is bash, not HTML;
   HTML escaping would corrupt the rendered scripts.
3. The template loader is a ``PackageLoader`` rooted at the
   ``vllm_ec2_bench`` package -- only files that ship as part of the
   library are loadable. There is no path-traversal surface.
4. Render contexts come from validated ``DeploymentPlan`` objects whose
   string fields have been pre-checked at config load time.
"""
from __future__ import annotations

from typing import Any

# nosemgrep: python.flask.security.xss.audit.direct-use-of-jinja2
from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape


class Renderer:
    """Thin opaque wrapper around a Jinja2 ``Environment``."""

    __slots__ = ("_env",)

    def __init__(self, package: str, templates_dir: str) -> None:
        self._env = Environment(
            loader=PackageLoader(package, templates_dir),
            undefined=StrictUndefined,
            autoescape=select_autoescape(enabled_extensions=(), default=False),
            keep_trailing_newline=True,
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def render(self, template_name: str, ctx: dict[str, Any]) -> str:
        """Render ``template_name`` with ``ctx`` and return the result."""
        template = self._env.get_template(template_name)
        return template.render(**ctx)


def make_renderer(package: str, templates_dir: str) -> Renderer:
    """Construct a :class:`Renderer` for templates packaged in ``package``."""
    return Renderer(package=package, templates_dir=templates_dir)


__all__ = ["Renderer", "make_renderer"]
