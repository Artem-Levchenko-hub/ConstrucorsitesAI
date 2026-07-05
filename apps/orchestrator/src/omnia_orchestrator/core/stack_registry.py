"""Single declarative registry of the runtime stacks Omnia can provision.

Phase 7.1 design — see ``docs/plans/phase7-multistack-provision.md`` §2. Today the
knowledge "what does stack X need" is smeared across the provisioning pipeline: the
image-tag formula lives in ``provisioner.py``, the internal port is hardcoded ``:3000``
in ``docker_client.py``, and the template directory is re-derived from the raw template
string. This module collapses the *currently-wired* slice of that knowledge into one
place (APoSD deep module + Parnas "hide what changes" + R-04 DRY-as-knowledge): adding a
stack becomes editing one entry here, not chasing six call-sites.

**Slice A is a behavior-identical seam.** Every value produced here equals what the
pipeline computed inline before, so live apps stay byte-for-byte unaffected. The richer
per-stack fields from the design (readiness probe, env profile, migrate hook, log dialect,
prod overlay) land in their own wiring slices (B/C/D) and are added here only when a
call-site actually reads them — never as dead fields (R-05/YAGNI).
"""

from __future__ import annotations

from dataclasses import dataclass

_IMAGE_PREFIX = "omnia-template-"
_DEV_TAG = "dev"


@dataclass(frozen=True, slots=True)
class StackSpec:
    """Declarative identity of one provisionable stack.

    The registry key, template directory name and image suffix are the same string
    today (e.g. ``nextjs-entities``); they are kept as distinct fields so a future
    stack can decouple them without touching call-sites.
    """

    template_dir: str  # under apps/orchestrator/templates/
    image_tag: str  # "omnia-template-<name>:dev"
    container_port: int = 3000  # internal listen port (current cross-stack convention)


def _dev_image(name: str) -> str:
    return f"{_IMAGE_PREFIX}{name}:{_DEV_TAG}"


def _stack(name: str, *, container_port: int = 3000) -> StackSpec:
    return StackSpec(
        template_dir=name,
        image_tag=_dev_image(name),
        container_port=container_port,
    )


# The five template dirs shipped today (apps/orchestrator/templates/). New stacks plug
# in by adding an entry here once their richer fields (readiness/env/migrate) are wired.
STACKS: dict[str, StackSpec] = {
    name: _stack(name)
    for name in (
        "nextjs-entities",
        "nextjs-postgres-drizzle",
        "vite-react-spa",
        "fastapi-postgres",
        "telegram-bot-aiogram",
    )
}


def get_stack(name: str) -> StackSpec:
    """Resolve a :class:`StackSpec` for a template name.

    A registered stack returns its declared spec. An unregistered name is *synthesized*
    from the same image-tag formula (``omnia-template-<name>:dev``) and ``:3000`` port the
    pipeline used inline before this registry existed, so resolution stays
    behavior-identical and never rejects a template the filesystem still has. Fail-fast
    validation against the registry is a deliberate later behavior change (design §2.2),
    not part of this seam (R-10: we add the rejection path only with its own test).
    """
    return STACKS.get(name) or _stack(name)
