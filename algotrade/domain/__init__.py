"""Pure domain model: dataclasses and rules about money, quantities, and tax.

**This package imports nothing from `repositories`, `services`, `io`, or
`platform`, and never imports pymongo.** Enforced by
`tests/unit/test_domain_purity.py`. Keeping it pure is what makes every rule
about rupees testable with no database, and it is the boundary that stops
Mongo queries leaking into business logic.
"""
