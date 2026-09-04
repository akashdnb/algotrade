"""Tests for the data-pipeline hash (design §1.3, §9.2).

`StrategySpec.params_hash` freezes the strategy's PARAMETERS -- lookback, skip,
entry, exit, universe size and thresholds -- and `generate_recommendations`
refuses to trade when the config no longer hashes to what the portfolio was
created with.

It does not cover how the panel those parameters run over is BUILT. The series
filter, the ETF denylist, the equity allowlist and the fund-name heuristics all
decide which symbols the ranking can even see, and all of them live in code
rather than in the frozen YAML. Change one and every signal changes while the
gate stays green.

This is not hypothetical. `universe.DEFAULT_EXCLUSIONS` carries its own note:

    Found live on 2026-08-20: three precious-metal ETFs had entered the top
    60, two of them inside the entry threshold of 30.

The list was edited during live operation. Nothing recorded that the ranking's
inputs had changed, and nothing would have refused if the edit had been wrong.
"""

from __future__ import annotations

from algotrade.services.signals import pipeline_hash


def test_the_hash_is_stable_across_calls():
    """A content address that moves on its own is worse than none: every run
    would look like tampering and the alarm would be turned off."""
    assert pipeline_hash() == pipeline_hash()


def test_it_is_prefixed_like_the_other_content_addresses():
    assert pipeline_hash().startswith("sha256:")


def test_including_the_be_series_changes_the_hash():
    """Yesterday's near-miss. Including BE alters selection -- measured over
    15 years -- and `params_hash` cannot see the difference."""
    assert pipeline_hash(series=("EQ",)) != pipeline_hash(series=("EQ", "BE"))


def test_adding_an_exclusion_changes_the_hash():
    """The 2026-08-20 edit. Three ETFs were removed from the investable set,
    which changes what the strategy ranks."""
    base = frozenset({"NIFTYBEES", "GOLDBEES"})
    assert pipeline_hash(exclusions=base) != pipeline_hash(
        exclusions=base | {"SILVERBEES"})


def test_adding_a_known_equity_changes_the_hash():
    """The allowlist rescues real companies from the fund heuristics, so it
    decides membership just as much as the denylist does."""
    assert pipeline_hash(known=frozenset({"SKYGOLD"})) != pipeline_hash(
        known=frozenset({"SKYGOLD", "GOLDIAM"}))


def test_changing_the_fund_suffixes_changes_the_hash():
    assert pipeline_hash(suffixes=("BEES", "ETF")) != pipeline_hash(
        suffixes=("BEES", "ETF", "IETF"))


def test_set_ordering_does_not_affect_the_hash():
    """Sets and frozensets have no stable iteration order across processes.
    Hashing them unsorted would make the gate fire at random."""
    a = pipeline_hash(exclusions=frozenset({"A", "B", "C"}))
    b = pipeline_hash(exclusions=frozenset({"C", "A", "B"}))
    assert a == b


def test_series_order_does_affect_the_hash():
    """Unlike the sets, series is an ordered filter argument. Two orderings
    are two different calls, so they are not silently equated."""
    assert pipeline_hash(series=("EQ", "BE")) != pipeline_hash(
        series=("BE", "EQ"))


def test_the_default_matches_the_live_configuration():
    """The zero-argument form must describe what the system actually runs, or
    the stored hash records a pipeline nobody used."""
    from algotrade.core import universe as uv

    assert pipeline_hash() == pipeline_hash(
        series=("EQ",), exclusions=uv.DEFAULT_EXCLUSIONS,
        known=uv.KNOWN_EQUITIES,
    )
