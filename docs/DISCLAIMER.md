# Disclaimer — no warranty, no liability

**Last updated:** 2026-10-22  
**Product:** DUP Market Radar  
**Author:** Uladzimir Dabranski (DUP Labs)

## Language

This disclaimer is provided in English (controlling) and Russian (convenience). If they conflict, **English controls**.

---

## 1. Not financial advice

DUP Market Radar is a **monitoring and optional order-routing toolkit**. It does **not**:

- provide investment, legal, tax, or trading advice;
- act as a broker, exchange, custodian, or portfolio manager;
- guarantee profits, avoid losses, or time the market correctly.

Any score, setup label (`buy-watch`, `fade-watch`, `long`, `short`, etc.), “nearest” zone, Telegram alert, or dashboard row is a **heuristic** derived from public market data and local configuration. It is **not** a recommendation to buy, sell, or hold any asset.

## 2. Author / DUP Labs — no liability

**TO THE MAXIMUM EXTENT PERMITTED BY APPLICABLE LAW:**

- The software is provided **“AS IS”** and **“AS AVAILABLE”**, without warranty of any kind, express or implied, including but not limited to merchantability, fitness for a particular purpose, and non-infringement.
- **Uladzimir Dabranski, DUP Labs, contributors, and affiliates shall not be liable** for any direct, indirect, incidental, special, consequential, exemplary, or punitive damages, or any loss of profits, data, goodwill, capital, or opportunity, arising from use or inability to use the software — including but not limited to:
  - trading losses, slippage, liquidations, or missed stop-losses;
  - bugs, incorrect scores, stale or degraded snapshots, incomplete MTF coverage;
  - exchange / network / API outages, rate limits, or rejected orders;
  - misconfiguration, leaked API keys, or unauthorized access to your host;
  - auto-trade or Telegram-triggered actions you enabled;
  - reliance on Docker **lab-python** profile vs native fail-closed defaults.

**You use this software entirely at your own risk.** If you do not agree, do not run it, do not add API keys, and do not enable auto-trade.

## 3. Operator duties

You are solely responsible for:

1. Securing `.env`, API keys, hosts, and firewall / bind addresses.
2. Understanding KuCoin (or other venue) fees, limits, and regional restrictions.
3. Complying with laws and exchange ToS in your jurisdiction (including whether automated trading is allowed).
4. Monitoring `ok` / `mtf_ensure_ok` / health / metrics — degraded scans can refuse buys or change stop behavior.
5. Keeping `auto_trade` **OFF** unless you accept fully automated market orders.

## 4. No partnership / no endorsement

References to KuCoin, Binance, Bybit, MEXC, DexScreener, CoinGecko, Telegram, or other third parties do **not** imply partnership, sponsorship, or endorsement. Trademarks belong to their owners.

## 5. Experimental / R&D status

This repository is **R&D / ops-lab** software. It is **not** a certified, audited, or regulated trading system. Evidence in CI is primarily unit tests + offline smoke under a **python-lab** profile unless you build and run native locally.

## 6. Russian summary (не является офертой)

Программное обеспечение предоставляется **«как есть»**. Автор и DUP Labs **не несут ответственности** за убытки, упущенную выгоду, ошибки анализа, сбои бирж/сети, действия авто-режима или утечку ключей. Это **не** инвестиционная рекомендация. Включая ключи и auto ON, вы принимаете все риски на себя. Полный текст — разделы 1–5 выше (приоритет у английской версии).

---

*By cloning, installing, configuring, or running DUP Market Radar, you acknowledge that you have read and accepted this disclaimer.*
