# ORACLE SEED: US-Iran Ceasefire by March 31

**Seed ID:** CEASEFIRE-IRAN-2026-0331
**Generated:** 2026-03-15
**Event Score:** 0.727 (HIGH PRIORITY)

---

## Event Summary

The United States launched military strikes against Iranian targets, including Kharg Island (Iran's primary oil export terminal). The question is whether a ceasefire agreement between the US and Iran will be reached by March 31, 2026 — a 16-day window from seed generation. The market currently prices this at 15% YES, reflecting deep skepticism that active military operations can de-escalate into a formal ceasefire within two weeks.

---

## Event Score Breakdown

```
EVENT_SCORE = narrative_ambiguity x market_relevance x repricing_window x volume_potential
EVENT_SCORE = 0.85 x 1.0 x 0.90 x 0.95 = 0.727
```

| Factor              | Score | Rationale |
|---------------------|-------|-----------|
| narrative_ambiguity | 0.85  | Two equally credible framings: strikes as "decisive strength forcing quick deal" vs "costly escalation with no exit ramp" |
| market_relevance    | 1.00  | Direct Polymarket contract with $8.57M volume — exact match to prediction target |
| repricing_window    | 0.90  | Weekend timing, thin markets, breaking news cycle creates fast repricing opportunity |
| volume_potential    | 0.95  | High liquidity, active trading, mainstream media attention driving retail flow |

---

## Competing Narrative Interpretations

### BULL CASE (Ceasefire YES — currently 15%)

- **"Shock and awe forces capitulation"**: Historical precedent of overwhelming strikes creating conditions for rapid negotiation (Libya 2011 model). Trump administration frames strikes as leverage, not occupation.
- **Backchannel diplomacy**: Multiple reports of Omani and Qatari intermediaries. The ceasefire-by-April-30 market at 36.5% suggests traders see a deal as plausible on a slightly longer timeline — momentum could compress this.
- **Domestic political pressure**: US consumer sentiment at 56.4 (historically low). Oil spiking 17% in 4 days. Administration faces economic blowback that incentivizes quick resolution.
- **Iran's energy chokepoint hit**: Kharg Island strike directly threatens Iran's revenue. Regime may calculate that a ceasefire preserves what remains of its export capacity.
- **Gulf state pressure on Iran**: Hamas urging Iran to halt attacks on Gulf states signals fracturing of the resistance axis. Iran increasingly isolated.

### BEAR CASE (Ceasefire NO — currently 85%)

- **No historical precedent for 16-day ceasefire after active strikes**: The US-Iran dynamic has no diplomatic framework analogous to what produced rapid ceasefires in other conflicts. No ambassador, no direct line.
- **Escalation spiral underway**: Drone attacks on UAE oil terminals, Hegseth's "no quarter" rhetoric (a war crime signal), nuclear escalation warnings from Trump advisers — all point to deepening conflict, not de-escalation.
- **Rally-around-the-flag in Iran**: Strikes on sovereign territory historically consolidate domestic support for hardliners, making concessions politically impossible for Iranian leadership.
- **Regional contagion**: Lebanon strikes, Iraq embassy evacuations, Strait of Hormuz tensions with UK warship requests — the conflict is widening, not narrowing.
- **Trump's political incentives unclear**: UK leader Starmer's "distraction from Epstein files" framing suggests the administration may benefit from prolonged crisis. No clear political win from quick ceasefire.

---

## Current Polymarket Prices (Related Markets)

| Market | YES Price | Volume |
|--------|-----------|--------|
| US x Iran ceasefire by March 15 | 0.5% | $9.9M |
| **US x Iran ceasefire by March 31** | **15.0%** | **$8.57M** |
| US x Iran ceasefire by April 30 | 36.5% | $2.4M |
| US x Iran ceasefire by June 30 | 58.5% | $980K |
| US forces enter Iran by March 31 | 41.5% | $8.8M |
| Iranian regime fall by March 31 | 3.7% | $32.4M |
| Iranian regime fall before 2027 | 38.5% | $9.8M |

**Term structure signal**: The ceasefire curve (0.5% -> 15% -> 36.5% -> 58.5%) implies the market's median ceasefire date is approximately late May / early June. A March 31 ceasefire would be a significant acceleration vs consensus.

---

## Reddit Signals

| Post | Score | Velocity | Subreddit |
|------|-------|----------|-----------|
| US attacks Iran's Kharg Island, Trump says | 19,667 | 757/hr | r/worldnews |
| Trump calls on UK and others to send warships to Strait of Hormuz | 15,900 | 1,544/hr | r/worldnews |
| Drones attack one of the world's largest oil terminals in the UAE | 13,047 | 914/hr | r/worldnews |
| Israel is running critically low on interceptors, US officials say | 11,895 | 3,143/hr | r/worldnews |
| Secretary Of Defense Hegseth Casually Promises Iranians 'No Quarter' — A War Crime | 8,377 | 1,070/hr | r/politics |
| U.K. leader: Trump is 'losing allies' and perhaps using war to 'distract from Epstein files' | 8,797 | 1,461/hr | r/politics |
| Trump Adviser Warns of Possible Israel Nuclear Escalation in Iran Conflict | 7,673 | 1,297/hr | r/politics |

**Velocity analysis**: The interceptor story is accelerating fastest (3,143/hr), suggesting growing concern about Israel's defensive capacity — a factor that could either accelerate ceasefire pressure or deepen US commitment to offensive strikes.

---

## FRED Economic Context

| Indicator | Value | Trend | Relevance |
|-----------|-------|-------|-----------|
| WTI Crude Oil | $94.65 | SPIKING (+17% in 4 days, from $80.88) | Direct war premium; further escalation = $100+ imminent |
| CPI | 327.46 | Rising | Energy-driven inflation surge likely if conflict persists |
| Fed Funds Rate | 3.64% | Stable (Fed pausing) | Fed unlikely to cut during supply shock — stagflation risk |
| Unemployment | 4.4% | Stable | Labor market holds for now; oil shock lag = 2-3 months |
| Consumer Sentiment | 56.4 | Rising but historically low | Fragile confidence; oil spike could reverse gains |

**Key dynamic**: Oil at $94.65 and rising fast. If conflict persists through March 31, $100+ oil is near-certain (the $100 market is already at 85% YES). This creates intense economic pressure on the administration to seek resolution, but the 16-day window may be physically insufficient for ceasefire mechanics.

---

## News Headlines Context

- "US attacks Iran's Kharg Island, Trump says" — Direct confirmation of strikes on Iran's most critical energy infrastructure
- "More US Marines and warships being moved to Middle East" — Force buildup suggests preparation for sustained operations, not quick resolution
- "Hamas urges key ally Iran to halt attacks on Gulf states" — Fracturing within the resistance axis
- "Russia exploiting Iran war, Zelensky says" — Geopolitical complexity; multiple actors with competing interests
- "'Gruesome' war bets fuel calls for crackdown on prediction markets" — Regulatory risk to the prediction market itself
- "U.S. Embassy in Baghdad again urges Americans to leave Iraq" — Regional spillover expanding

---

## Simulation Instructions

When running Monte Carlo simulations for this seed:

1. **Base distribution**: Start from the current market price (15% YES) as the prior. This is a liquid, actively-traded market with informed participants.

2. **Key branching variables to model**:
   - Probability of backchannel talks initiating within 72 hours (estimate: 30-45%)
   - Conditional probability of talks producing framework agreement within 10 days (estimate: 15-25%)
   - Probability of major escalation event (ground invasion, nuclear threat, Hormuz closure) that kills ceasefire prospects (estimate: 25-35%)
   - Probability of Iran retaliatory strike on US assets that makes ceasefire politically impossible for Trump (estimate: 20-30%)

3. **Correlation adjustments**:
   - Ceasefire probability is NEGATIVELY correlated with oil price (higher oil = more pressure to deal, but also signals deeper conflict)
   - Ceasefire probability is NEGATIVELY correlated with "US forces enter Iran" market (ground entry = prolonged conflict)
   - Ceasefire probability is POSITIVELY correlated with April 30 ceasefire market movements (leading indicator)

4. **Information decay**: Weight news from the last 24 hours at 1.0x, 24-48 hours at 0.7x, 48-72 hours at 0.4x. This conflict is evolving hourly.

5. **Repricing triggers to monitor**:
   - Any confirmed diplomatic contact between US and Iran (direct or via intermediary) -> +8-12% YES
   - Iran retaliatory strike on US military assets -> -5-8% YES
   - Strait of Hormuz closure or mining -> -10% YES (signals total escalation)
   - Trump public statement expressing willingness to negotiate -> +5-8% YES
   - UN Security Council emergency session -> +3-5% YES

6. **Edge detection**: The sharpest edge exists if the market underprices the speed of "deal-making under duress" scenarios. Trump's negotiating style historically involves maximum pressure followed by rapid pivot to deal. If backchannel signals emerge, the 15% -> 35%+ repricing could happen in hours.

7. **Risk flag**: The "'Gruesome' war bets" headline signals regulatory risk. Factor in a 5-10% probability that Polymarket restricts or modifies this market before resolution.
