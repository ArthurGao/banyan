"""The single-file HTML study guide (design §10.2): stored notes rendered through a Jinja2
template, no model call, no network request when opened, light and dark."""

from banyan.export.render import TEMPLATE_VERSION, ExportNode, render_study_guide

__all__ = ["TEMPLATE_VERSION", "ExportNode", "render_study_guide"]
