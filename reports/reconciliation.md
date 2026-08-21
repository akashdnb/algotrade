# Corporate-action reconciliation — full history

Generated 2026-08-21T17:53:27+05:30

## Inputs

| Panel sessions | 3,993 |
|---|---|
| Panel rows | 6,384,242 |
| Date range | 2010-06-10 .. 2026-08-20 |
| Cached actions | 34,659 |
| Price discontinuities | 2,583 |

## Rename chains

| From price continuity | 434 |
|---|---|
| From shared ISIN | 12 |
| Merged (price wins) | 446 |

## Outcomes

| Outcome | Count |
|---|---|
| matched | 858 |
| factor_mismatch | 54 |
| action_no_move | 179 |
| move_no_action | 1,671 |

**Match rate among paired events: 94.1%**

## Largest factor mismatches

```
shape: (15, 5)
┌────────────┬────────────┬───────────────┬──────────────┬───────────┐
│ date       ┆ symbol     ┆ implied_ratio ┆ price_factor ┆ deviation │
│ ---        ┆ ---        ┆ ---           ┆ ---          ┆ ---       │
│ date       ┆ str        ┆ f64           ┆ f64          ┆ f64       │
╞════════════╪════════════╪═══════════════╪══════════════╪═══════════╡
│ 2026-01-16 ┆ BESTAGRO   ┆ 14.644182     ┆ 1.5          ┆ 8.762788  │
│ 2025-06-16 ┆ BAJFINANCE ┆ 9.947761      ┆ 2.0          ┆ 3.973881  │
│ 2022-01-18 ┆ ONEPOINT   ┆ 7.164179      ┆ 1.5          ┆ 3.776119  │
│ 2026-03-06 ┆ SILVERTUC  ┆ 9.525124      ┆ 2.0          ┆ 3.762562  │
│ 2014-06-20 ┆ DEEPAKNTR  ┆ 9.4           ┆ 2.0          ┆ 3.7       │
│ …          ┆ …          ┆ …             ┆ …            ┆ …         │
│ 2024-12-20 ┆ LINC       ┆ 4.025728      ┆ 2.0          ┆ 1.012864  │
│ 2023-03-02 ┆ 360ONE     ┆ 4.020633      ┆ 2.0          ┆ 1.010316  │
│ 2025-09-26 ┆ NAZARA     ┆ 3.985714      ┆ 2.0          ┆ 0.992857  │
│ 2013-07-03 ┆ ASHOKA     ┆ 2.980952      ┆ 1.5          ┆ 0.987302  │
│ 2022-09-13 ┆ BAJAJFINSV ┆ 9.602493      ┆ 5.0          ┆ 0.920499  │
└────────────┴────────────┴───────────────┴──────────────┴───────────┘
```

## Unexplained moves (excluded from the universe)

127 symbols, 1,077 (symbol, date) rows.

Windows are dropped rather than whole symbols: excluding a symbol's entire
history over one bad day removes ~17% of universe names, which reintroduces
the selection bias this project exists to avoid.

```
shape: (25, 3)
┌────────────┬───────────┬───────────────┐
│ date       ┆ symbol    ┆ implied_ratio │
│ ---        ┆ ---       ┆ ---           │
│ date       ┆ str       ┆ f64           │
╞════════════╪═══════════╪═══════════════╡
│ 2010-07-05 ┆ RNRL      ┆ 1.38121       │
│ 2010-07-06 ┆ TULIP     ┆ 5.088443      │
│ 2010-07-30 ┆ PRATIBHA  ┆ 5.101502      │
│ 2010-08-13 ┆ MAGMA     ┆ 4.78967       │
│ 2010-08-17 ┆ SURANAT&P ┆ 2.992823      │
│ …          ┆ …         ┆ …             │
│ 2011-06-20 ┆ KSOILS    ┆ 1.271768      │
│ 2011-07-29 ┆ ADANIENT  ┆ 1.271648      │
│ 2011-08-16 ┆ KSOILS    ┆ 1.456647      │
│ 2011-08-17 ┆ COROENGG  ┆ 1.267138      │
│ 2011-08-23 ┆ SGFL      ┆ 1.306818      │
└────────────┴───────────┴───────────────┘
```
