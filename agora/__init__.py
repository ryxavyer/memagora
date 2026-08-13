"""MemAgora team server — the deployable half of the palace→agora pipe.

Engineers' local palaces classify structured facts and POST them here.
This package is deployed per team; it is never installed alongside the
engineer-side ``mempalace`` package (separate ``pyproject.toml``, separate
dependency profile — see ROADMAP.md "v0.3").

Nothing in ``agora`` may import from ``mempalace``. The only shared code is
the ``contracts`` package (pure dataclasses, no dependencies).
"""

__version__ = "0.3.0"

__all__ = ["__version__"]
