"""Broker I/O. The only package that talks to a broker's HTTP API.

Separate from `services/` for the same reason `repositories/` is the only place
pymongo appears: it is I/O against a third party, and the code that decides
*what* to do must stay testable without a network.

`base.BrokerClient` is the only interface anything outside this package
imports. Everything else here is an implementation of it.
"""
