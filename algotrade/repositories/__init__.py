"""Persistence. The only package in the codebase that imports pymongo.

Enforced by `tests/unit/test_domain_purity.py`. Everything above this layer
speaks in domain entities and Python `Decimal`; the translation to BSON,
`Decimal128`, and business-date strings happens here and nowhere else.
"""
