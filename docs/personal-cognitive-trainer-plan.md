# Feynman — Product Requirements

> A personal, voice-first cognitive trainer that uses the user's real life to keep mental math sharp, adaptive, and measurably strengthening over time.

---

## 1. Why This Exists

Most AI tools take work off your plate — writing, coding, recall, arithmetic. The cost is quiet: the muscles those tasks used to exercise atrophy. Feynman exists to push the other direction. It uses the same AI capabilities to *create* the right kind of effort, at the right moment, calibrated to the edge of the user's ability.

The first wedge is mental math. It is concrete, measurable, and declines visibly when neglected. If the core loop works for arithmetic, the same machinery extends later to navigation, people-memory, and autobiographical recall.

## 2. North Star

A private training rig that gets sharper with use because it is grounded in the user's real life, measures more than right-or-wrong, and keeps presenting problems just past the edge of current ability.

| Quality | What it means in practice |
| --- | --- |
| **Personal** | Problems draw from the user's calendar, purchases, routines, and recurring numbers. |
| **Adaptive** | Difficulty responds to accuracy, latency, hesitation, and recent performance. |
| **Effortful** | The system creates work for the user; it never does the thinking for them. |
| **Sticky** | Sessions feel relevant and well-timed, not abstract or nagging. |
| **Evolvable** | New cognitive modules slot into the same loop without rewriting it. |

## 3. Core Loop

The product works if it can do four things repeatedly and well.

| Step | Behavior |
| --- | --- |
| **Select** | Pick the next skill based on weakness, recency, and fatigue. |
| **Present** | Pose a short, clear problem in a format that demands real mental effort. |
| **Measure** | Capture correctness, onset latency, resolution latency, and self-correction. |
| **Adapt** | Update the user model and decide what to ask next, and how hard. |

Everything else in the product serves this loop.

## 4. Scope

### In scope for the first arc

| Decision | Choice |
| --- | --- |
| Cognitive domain | Mental math only |
| Platform | Mac / Mac Mini |
| Client | Local web app |
| Lane | Active — user chooses to start a session |
| Primary modality | Voice-first, with visual as a comparison mode |
| Grounding sources | Small, curated set of real-life context |
| Single-user first | Architected so additional users can plug in later without a rewrite |

### Explicitly out of scope (for now)

| Deferred | Reason |
| --- | --- |
| Mobile / Android app | The Mac web app is enough to prove the loop. |
| Passive notifications and timing intelligence | Timing optimization comes after session quality is proven. |
| Broad life-logging (full inbox, banking) | Too much ingestion before the value is established. |
| General AI tutoring | This is a trainer, not a conversational assistant. |
| Navigation, people-memory, episodic recall | Future modules; intentionally parked. |

## 5. Users and Use

The first user is the owner of the system. The product should feel like a tool built specifically for them. The architecture allows a second user (e.g. a partner) to add their own credentials and run their own training, but no UX work is spent on multi-user flows in the first arc.

A typical day looks like one or two short sessions, started intentionally, on a Mac. The user opens the web app, runs a 2–6 minute drill by voice, and sees a brief review at the end. Tomorrow's session reflects today's struggles.

## 6. What Grounds It In Real Life

The first version uses only context that is both useful for arithmetic and easy to operationalize.

| Source | Why it belongs early | Examples of problems it generates |
| --- | --- | --- |
| **Calendar** | Time math is naturally personal and constantly relevant | durations, buffers, overlaps, "leave by" calculations, timezone shifts |
| **Receipts / order confirmations** | Produces practical percentage and total-cost problems | tips, tax, discounts, splits, post-tax totals |
| **Recurring personal numbers** | Makes estimation and percentage drills concrete | rent, subscriptions, commute times, savings targets |
| **Performance history** | The system's own memory of how the user thinks | repeated exposure to weak spots, less repetition on mastered skills |

The principle: a small amount of relevant context should make training feel materially better than a generic math app. Broader ingestion is a later question.

## 7. Session Design

The first surface is a short, focused, voice-first session — not an ambient prompt stream.

| Session type | Role | Feel |
| --- | --- | --- |
| **Quick drill** | Maintain fluency through repetition | fast, compact, low friction |
| **Applied drill** | Tie arithmetic to real daily reasoning | slightly slower, contextual, scenario-driven |

Sessions are short enough that latency and hesitation produce clean signal. The active lane is chosen first because user-initiated sessions yield much better measurement than interrupted ambient prompts.

## 8. Voice-First Measurement

The differentiator is not that the app asks math questions. It is that it measures performance in a way that resembles real-life thinking under light pressure.

| Signal | What it tells us |
| --- | --- |
| Correctness | Basic mastery |
| Onset latency (prompt end → speech start) | Retrieval difficulty, hesitation |
| Resolution latency (prompt end → final answer) | Overall fluency |
| Self-correction ("wait, no…") | Unstable knowledge invisible in typed quizzes |
| Modality delta (voice vs. visual) | Whether auditory presentation transfers better |

Mental math heard is meaningfully different from mental math read. The product treats that as a first-class distinction.

## 9. Roadmap

Phases describe product surface, not engineering milestones. Each phase ends when its success sign is real, not when its features are built.

### Phase 0 — Session Prototype

| | |
| --- | --- |
| **Goal** | Prove the core loop is worth using. |
| **Surface** | Local web app on Mac. |
| **Content** | Addition, subtraction, multiplication, percentages, time arithmetic. |
| **Grounding** | Calendar, receipts/orders, seeded recurring numbers. |
| **Success sign** | The user voluntarily comes back because drills feel relevant and well-calibrated. |

### Phase 1 — MVP

| | |
| --- | --- |
| **Goal** | Turn the prototype into a real personal training tool with daily use. |
| **Surface** | Same web app, with session history and progress review. |
| **New** | Adaptive difficulty, skill history, simple explanations, legible progress. |
| **Success sign** | Sessions visibly tighten around weak spots; the app feels like it remembers how the user thinks. |

### Phase 2 — Sticky Personalization

| | |
| --- | --- |
| **Goal** | Make the product hard to replace. |
| **Surface** | Same core app, richer contextual prompts and review. |
| **New** | Stronger personalization from recurring life data, more applied scenarios, smarter session mixing. |
| **Success sign** | Problems feel like they came out of the user's actual week. |

### Phase 3 — Passive Lane

| | |
| --- | --- |
| **Goal** | Deliver practice when the user is most receptive, not on a fixed schedule. |
| **Surface** | Notifications and lightweight entry points layered onto the main product. |
| **New** | Context-aware prompt timing, optional nudges, smarter session triggers. |
| **Success sign** | Engagement rises because timing is better, not because volume is higher. |

### Phase 4 — Broader Cognitive Training

| | |
| --- | --- |
| **Goal** | Generalize the system beyond mental math. |
| **Surface** | Same core product, additional modules. |
| **New** | People-memory (pre/post-meeting), autobiographical recall, episodic review. |
| **Success sign** | New modules slot into the same adaptive loop without diluting the original thesis. |

## 10. Product Principles

| Principle | Practical implication |
| --- | --- |
| **Relevance beats volume** | Fewer, sharper prompts are worth more than constant drills. |
| **Effort stays with the user** | The system supports thinking; it never performs it. |
| **Adaptation must be felt** | The user should notice the app responding to real patterns. |
| **Right/wrong is not enough** | Latency and hesitation are first-class signals. |
| **Narrow is a strength** | A small, strong product beats a broad, weak one. |

## 11. Open Questions for the Next Pass

The next planning round moves from product shape to operating detail. These are the questions that round answers.

| Topic | Why it matters next |
| --- | --- |
| Session flow | How a round starts, runs, and ends, minute to minute. |
| Voice interaction model | How prompts are spoken, answers captured, timing measured. |
| Scoring model | What counts as improvement — and how it is shown back to the user. |
| Context adapters | How calendar and receipt data turn into problems. |
| Adaptation logic | How the system decides what to ask next, and how hard. |

---

*One-line summary: Feynman is a personal, voice-first cognitive trainer that uses the user's real life to make mental math adaptive, relevant, and measurably strengthening over time.*
