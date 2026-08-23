"""Orchestration. Depends on `domain` and `repositories`; never the reverse.

Services may not import each other. Shared logic belongs in `domain`, which
keeps this layer from collapsing into a mutually-recursive ball.
"""
