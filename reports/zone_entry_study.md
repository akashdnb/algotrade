# Zone entry study — development window (2010–2021)

Shipped detector after the reliability fixes. Holdout (2022+) untouched. Trials 10–12 in config/trials.jsonl.
Method: algotrade/zones/study.py — first reach of a fresh zone, entry next open, market-adjusted, vs matched random-day controls, month-clustered bootstrap.

## All zones
```
events (real, tradeable first touches): 21481
round-trip cost assumption: 0.35%

== DEMAND — real minus matched control (market-adjusted) ==
  abn_5        n= 11435 real=-0.0002 ctrl=+0.0004 diff=-0.0006 [-0.0020,+0.0007] p=0.383
  abn_10       n= 11367 real=-0.0019 ctrl=-0.0003 diff=-0.0017 [-0.0036,-0.0001] p=0.042
  abn_20       n= 11306 real=-0.0056 ctrl=-0.0020 diff=-0.0037 [-0.0064,-0.0013] p=0.000
  abn_stop_5   n= 11435 real=-0.0022 ctrl=-0.0024 diff=+0.0002 [-0.0013,+0.0016] p=0.817
  abn_stop_10  n= 11367 real=-0.0030 ctrl=-0.0017 diff=-0.0016 [-0.0051,+0.0019] p=0.378
  abn_stop_20  n= 11306 real=-0.0100 ctrl=-0.0078 diff=-0.0023 [-0.0073,+0.0033] p=0.384
  by timeframe:
    daily    n=  6927 real=-0.0015 ctrl=-0.0002 diff=-0.0012 [-0.0033,+0.0007] p=0.223
    monthly  n=  3450 real=-0.0033 ctrl=-0.0011 diff=-0.0027 [-0.0062,+0.0007] p=0.117
    weekly   n=  1464 real=-0.0012 ctrl=-0.0001 diff=-0.0014 [-0.0054,+0.0024] p=0.438
  by score:
    50-59    n=  4864 real=-0.0020 ctrl=+0.0001 diff=-0.0022 [-0.0045,+0.0000] p=0.053
    60+      n=  4625 real=-0.0020 ctrl=-0.0012 diff=-0.0012 [-0.0047,+0.0020] p=0.481
    <50      n=  2248 real=-0.0020 ctrl=-0.0004 diff=-0.0018 [-0.0053,+0.0014] p=0.319
  by displacement:
    2-3      n=  3678 real=-0.0026 ctrl=-0.0010 diff=-0.0018 [-0.0045,+0.0009] p=0.211
    3+       n=  1305 real=+0.0002 ctrl=-0.0008 diff=+0.0005 [-0.0060,+0.0078] p=0.840
    <2       n=  6819 real=-0.0021 ctrl=-0.0001 diff=-0.0021 [-0.0041,-0.0001] p=0.035
  by volume:
    1.5-5x   n=  6637 real=-0.0034 ctrl=-0.0003 diff=-0.0032 [-0.0055,-0.0010] p=0.002
    5x+      n=  2394 real=+0.0007 ctrl=-0.0008 diff=+0.0014 [-0.0022,+0.0055] p=0.448
    <1.5x    n=  2680 real=-0.0015 ctrl=-0.0000 diff=-0.0017 [-0.0047,+0.0012] p=0.237
  by age:
    21-60    n=  1901 real=+0.0004 ctrl=-0.0007 diff=+0.0013 [-0.0018,+0.0047] p=0.451
    61-250   n=  1482 real=+0.0028 ctrl=-0.0018 diff=+0.0039 [-0.0011,+0.0089] p=0.137
    <=20     n=  7347 real=-0.0032 ctrl=-0.0007 diff=-0.0026 [-0.0044,-0.0008] p=0.006
    >250     n=   849 real=-0.0051 ctrl=+0.0027 diff=-0.0083 [-0.0146,-0.0003] p=0.044

== SUPPLY — real minus matched control (market-adjusted) ==
  abn_5        n=  7376 real=+0.0044 ctrl=+0.0037 diff=+0.0008 [-0.0007,+0.0024] p=0.279
  abn_10       n=  7342 real=+0.0060 ctrl=+0.0067 diff=-0.0005 [-0.0025,+0.0016] p=0.610
  abn_20       n=  7288 real=+0.0111 ctrl=+0.0121 diff=-0.0009 [-0.0036,+0.0017] p=0.496
  abn_stop_5   n=  7376 real=+0.0042 ctrl=+0.0041 diff=+0.0002 [-0.0013,+0.0016] p=0.810
  abn_stop_10  n=  7342 real=+0.0073 ctrl=+0.0076 diff=-0.0003 [-0.0026,+0.0019] p=0.807
  abn_stop_20  n=  7288 real=+0.0132 ctrl=+0.0151 diff=-0.0018 [-0.0067,+0.0028] p=0.412
  by timeframe:
    daily    n=  4061 real=+0.0046 ctrl=+0.0049 diff=-0.0005 [-0.0034,+0.0023] p=0.680
    monthly  n=  2650 real=+0.0087 ctrl=+0.0100 diff=-0.0008 [-0.0050,+0.0030] p=0.698
    weekly   n=   842 real=+0.0058 ctrl=+0.0057 diff=+0.0010 [-0.0047,+0.0067] p=0.779
  by score:
    50-59    n=  3200 real=+0.0062 ctrl=+0.0065 diff=-0.0004 [-0.0032,+0.0026] p=0.776
    60+      n=  1697 real=+0.0106 ctrl=+0.0097 diff=+0.0017 [-0.0029,+0.0062] p=0.486
    <50      n=  2648 real=+0.0037 ctrl=+0.0050 diff=-0.0015 [-0.0043,+0.0015] p=0.307
  by displacement:
    2-3      n=  2230 real=+0.0073 ctrl=+0.0076 diff=-0.0003 [-0.0042,+0.0038] p=0.870
    3+       n=   699 real=+0.0115 ctrl=+0.0110 diff=+0.0003 [-0.0062,+0.0071] p=0.900
    <2       n=  4628 real=+0.0048 ctrl=+0.0056 diff=-0.0008 [-0.0030,+0.0015] p=0.472
  by volume:
    1.5-5x   n=  3920 real=+0.0070 ctrl=+0.0075 diff=-0.0006 [-0.0032,+0.0022] p=0.631
    5x+      n=   416 real=+0.0122 ctrl=+0.0104 diff=+0.0023 [-0.0062,+0.0101] p=0.626
    <1.5x    n=  3199 real=+0.0047 ctrl=+0.0053 diff=-0.0003 [-0.0030,+0.0026] p=0.839
  by age:
    21-60    n=  1065 real=+0.0070 ctrl=+0.0083 diff=-0.0006 [-0.0057,+0.0050] p=0.822
    61-250   n=  1089 real=+0.0070 ctrl=+0.0060 diff=+0.0015 [-0.0039,+0.0072] p=0.613
    <=20     n=  4552 real=+0.0055 ctrl=+0.0055 diff=-0.0001 [-0.0024,+0.0023] p=0.927
    >250     n=   746 real=+0.0082 ctrl=+0.0137 diff=-0.0050 [-0.0126,+0.0028] p=0.193

```

## Pre-registered variants
```
events: 21481
round-trip cost assumption: 0.35%; Bonferroni threshold p < 0.0167

H10 confluence (control = parent zone's matched controls)
    confluence=True [abn_stop_10]    n=  3995 real=-0.0011 ctrl=-0.0027 diff=+0.0014 [-0.0026,+0.0054] p=0.5230
    confluence=False [abn_stop_10]   n=  7426 real=-0.0041 ctrl=-0.0010 diff=-0.0031 [-0.0069,+0.0006] p=0.1010
H11 trend-aligned (controls conditioned on the same trend state)
    trend_up=True [abn_stop_10]      n=  5037 real=-0.0035 ctrl=-0.0029 diff=-0.0006 [-0.0038,+0.0025] p=0.7230
    trend_up=False [abn_stop_10]     n=  5323 real=-0.0019 ctrl=-0.0009 diff=-0.0011 [-0.0056,+0.0032] p=0.5720
H12 long horizon, weekly+monthly demand
    weekly+monthly [abn_stop_40]     n=  4671 real=-0.0225 ctrl=-0.0220 diff=-0.0008 [-0.0071,+0.0067] p=0.8700
    weekly+monthly [abn_stop_60]     n=  4576 real=-0.0429 ctrl=-0.0423 diff=-0.0001 [-0.0135,+0.0122] p=0.9420
    weekly+monthly [abn_40]          n=  4671 real=-0.0132 ctrl=-0.0070 diff=-0.0062 [-0.0120,-0.0000] p=0.0500
    weekly+monthly [abn_60]          n=  4576 real=-0.0238 ctrl=-0.0144 diff=-0.0092 [-0.0172,-0.0014] p=0.0220
    weekly [abn_stop_40]             n=  1418 real=-0.0178 ctrl=-0.0193 diff=+0.0012 [-0.0093,+0.0119] p=0.8350
    monthly [abn_stop_40]            n=  3358 real=-0.0250 ctrl=-0.0234 diff=-0.0019 [-0.0096,+0.0059] p=0.6330
```
