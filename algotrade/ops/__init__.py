"""Operational tooling: backup, restore, health scoring, runtime exports.

Separate from `services/` because none of this is part of the trading
lifecycle -- it is what keeps the lifecycle recoverable and observable.
"""
