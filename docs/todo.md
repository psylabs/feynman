Now let me go through everything carefully. Your core complaint right now is: **the app doesn't show me it understands my weaknesses, and drills still feel random.** That's the top of the list. Everything else flows from the full thread.

---

## Prioritized TODOs

### Tier 1 — The app doesn't feel like it knows me

1. **Session review should show diagnosis, not just a score table.** After each session, show: "Here's what you're slowest at, here's what you got wrong, here's what the next session will focus on." Right now the review screen is a generic attempts table with correct/wrong/latency. It should surface the diagnosis engine's output — slowest facts, patterns, regressions — so the user sees the system is learning.

2. **Drill sessions don't feel targeted enough.** The diagnosis scheduler was just built but hasn't been tested with real data. Possible issues: (a) cold-start fallback fires too often because `min_attempts=2` is too high for new fact keys, (b) the weighted-random top-8 sampling is too diffuse — if you have 8 slow facts, sessions should hammer 2–3 of them, not sprinkle one of each, (c) session length of 5 is too short for the scheduler to build a coherent theme within a session.

3. **Inconsistent fact granularity on the profile page.** You noticed: multiplication shows specific problems (9×11) but addition shows pattern buckets (2d+2d). This is by design in the diagnosis engine (mul facts repeat; add problems don't), but the profile should make this coherent. Options: (a) for multiplication, group by factor family (×7 facts, ×8 facts) in addition to individual pairs, (b) for addition/subtraction, show the pattern bucket label in a way that reads naturally ("2-digit + 2-digit with carry" not "2d+2d:c"), (c) consider showing representative slow *problems* from add/sub too, not just the bucket.

4. **Increase default drill session length.** 5 questions isn't enough to feel like targeted work. Move to 10–15 so the scheduler can cluster related problems within a session.

5. **Within-session clustering.** When the scheduler picks a weakness, it should stick with it for a few problems in a row, not switch every question. E.g., if 7×8 is slow, give me 7×8 three times in a session mixed with other ×7 and ×8 facts, not one 7×8 then jump to subtraction.

### Tier 2 — Data quality and measurement

6. **Parser bugs are still creating bad data.** The three fixed today (commas, decimals, colons) were silently scoring correct answers as wrong. Audit the existing attempt data for rows where the parser clearly failed — these pollute the diagnosis engine's stats. Consider a one-time cleanup script or just a "data since date X" filter.

7. **STT mishearing.** Some wrong scores are genuine Whisper errors (you said 71, it heard 91). Track the rate. If it's >5% of attempts, consider: (a) a "that's not what I said" button that lets the user retry without recording a wrong attempt, (b) passing a constrained vocabulary hint to the STT model.

8. **"1,078" parsed as 1 was scored as wrong and recorded.** Those bad attempts are now in the DB feeding the diagnosis engine. The diagnosis engine should either ignore obviously bad data (e.g., parsed_answer of 1 when expected was 1078 — that's a 99.9% error, clearly a parse failure not a math failure) or we should retroactively clean them.

### Tier 3 — UX and session flow

9. **Start screen should show a one-line diagnosis teaser.** Before starting a drill: "Your slowest: 7×8 (8200ms), 2d+2d carry (6100ms). This session will focus on those." Sets expectations, shows the system is thinking.

10. **Auto-advance timing after feedback.** Currently 2s auto-advance (5s if feedback pending). This may be too fast to read the feedback, or too slow when the answer was instant and correct. Consider: fast correct → 1s, wrong → hold until user clicks Next, feedback → hold until user clicks Next.

11. **The "Hold to answer" instruction should shorten or disappear once the user knows the flow.** After the first session, it's just noise.

12. **Spacebar-hold ergonomics.** Consider an alternative: tap to start recording, tap to stop. Hold-to-talk is awkward for longer answers where you're thinking out loud.

### Tier 4 — Curriculum and problem generation

13. **Multiplication: add 3×3 through 9×9 as a distinct L1.5 tier.** Currently L1 is only ×2, ×5, ×10 and L2 jumps straight to ×6–×12. The "hard table facts" (6×7, 7×8, 8×9) are the canonical retrieval bottleneck and should be drillable as a focused set, not mixed in with ×11 and ×12.

14. **Addition/subtraction pattern buckets may be too coarse.** `2d+2d:c` covers everything from 15+17 to 89+97. These are very different problems. Consider splitting by sum range or by whether there's a tens-carry only vs. ones-and-tens carry.

15. **Percent drills need more variety.** The percentage pool is small (10, 15, 18, 20, 25, 50). Real-life tipping and tax uses 7%, 8%, 8.875% (NYC sales tax), etc. The base pool is also small.

16. **Strategy scaffolds for stubborn facts.** You asked for "brief rescue scaffolds for stubborn facts." The feedback system currently generates LLM coaching, but it fires on every wrong/slow answer. It should instead fire only when a fact has been slow/wrong repeatedly, and it should give a specific strategy ("for 7×8: 7×7=49, add 7"), not a generic "you may have miscalculated."

### Tier 5 — Data pipeline and adaptation

17. **Per-user adaptive latency targets.** The skill-wide targets (5000ms addition, 6000ms multiplication) are starting guesses. Once a user consistently beats a target, it should tighten. The research doc notes this explicitly as a design intent that isn't implemented.

18. **Spaced retrieval for mastered facts.** Facts that are fast and accurate should resurface at growing intervals to detect decay. Currently mastered facts just drop off the priority list and never come back.

19. **Regression detection should influence the scheduler.** The diagnosis engine detects regressions but the scheduler doesn't currently weight them differently from just being slow. A regression (was fast, now slow) should be higher priority than a fact that was always slow.

### Tier 6 — Grounded/real-life problems

20. **Pull from calendar for time-math problems.** "Your meeting is at 2:15, it's 1:47, how many minutes until it starts?" This was in the original plan as the "grounding layer."

21. **Pull from finance for tip/tax/budget problems.** "$63.42 bill, 18% tip" instead of abstract "what is 18% of 63?" This makes the practice sticky and motivating.

22. **Context requires new skills.** Time arithmetic, money arithmetic, unit conversion — these are distinct from the current four skills and need their own generators, tolerances, and fact keys.

### Tier 7 — Infrastructure

23. **Debug panel takes up half the screen.** It was useful during development but now it's permanent real estate. Make it collapsible or hidden by default.

24. **Mobile/tablet support.** The current grid layout assumes a wide screen with a debug pane. If this ever runs on a phone (e.g., while commuting), the layout needs to be responsive.

25. **Data export.** The SQLite DB is the only record. A JSON export of attempts would be useful for analysis outside the app.

26. **Multiple devices / sync.** Currently localhost-only with a local SQLite file. If the user wants to practice from multiple machines, the data doesn't follow.
