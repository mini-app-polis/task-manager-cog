"""task-manager-cog: the one writer to the task board.

Install name:  task-manager-cog
Import name:   task_manager_cog
"""

import os
from collections.abc import MutableMapping

from mini_app_polis import load_secrets

from ._version import __version__ as __version__

# Before anything else in the package: a Lambda worker's secrets arrive from
# SSM Parameter Store here, at the first import of the package, so they are
# in the environment before any module that reads it at import: the logger,
# Sentry's init in the worker. A no-op wherever SSM_PARAMETERS is unset (local
# runs under `doppler run`, tests). See mini_app_polis.ssm_secrets.
load_secrets()


def refine_settings_from_groom(environ: MutableMapping[str, str]) -> None:
    """Fill each unset REFINE_* setting from its GROOM_* name.

    Grooming was renamed refining, and the settings with it: GROOM_TIMES is
    REFINE_TIMES, and so on. Until every place that sets them has moved, a
    GROOM_* setting stands in for its REFINE_* name when that is unset.
    """
    for name, value in list(environ.items()):
        if name.startswith("GROOM_"):
            environ.setdefault("REFINE_" + name.removeprefix("GROOM_"), value)


refine_settings_from_groom(os.environ)
