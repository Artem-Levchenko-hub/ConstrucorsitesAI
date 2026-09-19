from __future__ import annotations


def _merge_seeded_agent_files(
    seeded_files: dict[str, str], generated_files: dict[str, str]
) -> dict[str, str]:
    """Keep the complete verified MAX base while preferring AI customisations.

    The starter is hot-reloaded before the native agent runs. The agent result
    only contains paths it actually wrote, so committing that result alone would
    lose untouched platform files on a brand-new project.
    """

    return {**seeded_files, **generated_files}
