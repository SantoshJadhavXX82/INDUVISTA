"""Module entry point — supports `python -m induvista_datahub` and
the `induvista-datahub` script defined in pyproject.toml [project.scripts].
"""

import sys

from induvista_datahub.app import run


def main() -> int:
    """Console entry. Returns the Qt event loop's exit code."""
    return run()


if __name__ == "__main__":
    sys.exit(main())
