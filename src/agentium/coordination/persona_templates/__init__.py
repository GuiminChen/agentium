"""Bundled persona templates with four Markdown planes (IDENTITY / SOUL / TOOLS / USER).

Design intent: ship opinionated defaults under ``bundled_roles/`` and allow extra
roles via ``AGENTIUM_PERSONA_TEMPLATES_DIR`` (same layout per subdirectory).
"""

from agentium.coordination.persona_templates.loader import (
    PersonaTemplateRole,
    load_persona_templates,
)

__all__ = ["PersonaTemplateRole", "load_persona_templates"]
