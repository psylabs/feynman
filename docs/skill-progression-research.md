# Research Foundations: Mental Arithmetic Skill Progression

> Companion to the architecture and MVP docs. This file documents the research
> basis for the stage curriculum and mastery model proposed for Feynman. It is
> a working reference for design decisions, not a peer-reviewed survey.

---

## Preamble: an honest note on confidence

The user has explicitly flagged a concern about over-confident, deferential
output that changes tracks on demand. This document is an attempt to ground
the proposed design in claims that can be checked rather than asserted.

The author of this document is Claude, working from training-data knowledge of
the field — not from direct access to the cited papers in the moment of
writing. That has consequences:

- **Author, year, journal, and page-range citations** below are real to the
  best of the author's knowledge. The user should treat them as lookup-able
  references, not as quotations the author has just verified line by line.
- **URLs** are included only where the author has high confidence they
  resolve to the cited document. They are not exhaustive — many cited works
  are paywalled or only available through institutional access.
- **Specific numeric values** (e.g., reaction times) reported in the
  Implications sections are *approximations* drawn from the general
  literature. The exact values vary considerably by study, population, and
  task. They are presented as orders of magnitude, not as canonical numbers.
- **Synthesis vs. citation** is marked explicitly. Where this document moves
  from "this is what the literature says" to "this is the design I'm proposing
  in light of that," the boundary is called out.

If a claim drives a meaningful design decision, the user should spot-check it
against the primary source before relying on it.

---

## 1. Core principles

Each principle below is a claim about how arithmetic skill develops and is
retained. They are not interchangeable; the proposed curriculum is built on
all of them together.

### 1.1 Mastery is two-dimensional: accuracy *and* speed

Cognitive science distinguishes two states for any well-practiced skill:

- A **procedural** state, in which the answer is constructed step by step
  under working memory load.
- An **automatic** state, in which the answer is retrieved directly from
  long-term memory with little or no conscious calculation.

The transition from one to the other is gradual and is one of the central
phenomena studied in skill-acquisition research. A correct answer produced
slowly is qualitatively different from a correct answer produced quickly:
the first reflects calculation, the second reflects retrieval.

**Primary references:**

- Logan, G. D. (1988). Toward an instance theory of automatization.
  *Psychological Review*, 95(4), 492–527.
  *— Foundational theoretical account of how skills move from controlled to automatic processing.*

- Anderson, J. R. (1982). Acquisition of cognitive skill.
  *Psychological Review*, 89(4), 369–406.
  *— ACT theory's three-stage account (declarative → procedural → tuned).*

- Willingham, D. T. (2004). Practice makes perfect — but only if you practice
  beyond the point of perfection. *American Educator*, Spring 2004.
  Available: <https://www.aft.org/periodical/american-educator/spring-2004/ask-cognitive-scientist-practice-makes-perfect>
  *— Accessible synthesis aimed at educators; argues practice past initial accuracy is necessary for automaticity.*

**Implication for this system:** A mastery rule that uses accuracy alone
will mark a slow, deliberate solver as "mastered" when the underlying skill
is still procedural. We model mastery as accuracy *and* median latency below
a target — both required.

### 1.2 Automaticity in basic facts is a prerequisite for procedural skill

Multi-digit arithmetic is procedural: it requires holding intermediate values
in working memory while executing operations like carrying or borrowing. If
basic single-digit facts (sums to 20, multiplication tables) are not
retrieved automatically, working memory is consumed by retrieval and there is
little capacity left for the surrounding procedure. The result is that
multi-digit performance is bottlenecked on single-digit fluency.

**Primary references:**

- National Research Council. (2001). *Adding It Up: Helping Children Learn
  Mathematics* (J. Kilpatrick, J. Swafford, & B. Findell, Eds.). Washington,
  DC: National Academies Press.
  Available: <https://nap.nationalacademies.org/catalog/9822>
  *— Chapter 4 on procedural fluency is the most relevant; the book's "five strands of mathematical proficiency" is one of the most-cited frameworks in math education.*

- Geary, D. C. (2004). Mathematics and learning disabilities.
  *Journal of Learning Disabilities*, 37(1), 4–15.
  *— Argues that fluent fact retrieval is a major dividing line between typically developing learners and those with mathematical difficulties.*

- Willingham, D. T. (2006). How knowledge helps. *American Educator*,
  Spring 2006.
  Available: <https://www.aft.org/periodical/american-educator/spring-2006/how-knowledge-helps>
  *— Accessible argument that automaticity in component skills is a precondition for higher-order learning, grounded in working-memory theory.*

- Gersten, R., Beckmann, S., Clarke, B., Foegen, A., Marsh, L., Star, J. R.,
  & Witzel, B. (2009). *Assisting students struggling with mathematics:
  Response to Intervention (RtI) for elementary and middle schools*. IES
  Practice Guide. Institute of Education Sciences.
  Available: <https://ies.ed.gov/ncee/wwc/PracticeGuide/2>
  *— A federal practice guide that includes "intensive instruction on solving word problems" and "10 minutes per session of work on building fluent retrieval of basic arithmetic facts."*

**Implication for this system:** The curriculum has explicit prerequisites.
Multi-digit stages are gated by the single-digit stages they depend on. A
user is not drilled on 2-digit + 2-digit until single-digit fluency is
demonstrated.

### 1.3 The "problem-size effect" — specific facts are systematically harder

Within the basic arithmetic facts, response times and error rates rise
predictably with operand size. This is one of the most replicated findings
in numerical cognition research — sometimes called the "problem-size effect"
or, in older literature, the "problem-difficulty effect."

For multiplication, larger products (those involving operands 6 through 9)
are reliably slower and more error-prone than smaller products (those
involving 2, 5, or 10). The effect is found across populations and is robust
across decades of studies.

**Primary references:**

- Campbell, J. I. D. (Ed.). (2005). *Handbook of Mathematical Cognition*.
  New York: Psychology Press.
  *— Standard reference; the problem-size effect is discussed across multiple chapters by various authors.*

- Campbell, J. I. D., & Xue, Q. (2001). Cognitive arithmetic across
  cultures. *Journal of Experimental Psychology: General*, 130(2), 299–315.
  *— Replicates the problem-size effect across Chinese-educated, Asian-Canadian, and non-Asian-Canadian adults.*

- LeFevre, J.-A., Bisanz, J., Daley, K. E., Buffone, L., Greenham, S. L., &
  Sadesky, G. S. (1996). Multiple routes to solution of single-digit
  multiplication problems. *Journal of Experimental Psychology: General*,
  125(3), 284–306.
  *— Documents that adults use a mix of retrieval and procedural strategies even for single-digit multiplication, with the strategy mix shifting toward procedure for larger products.*

**Implication for this system:** Multiplication is split into per-factor
stages (×2, ×3, …, ×9) rather than a single "tables" stage. The empirically
hardest facts (the ×6–×9 cluster, particularly ×7 and ×8) get their own
mastery targets. They are not blended into a generic pool that could be
"passed" by being fluent on the easy half.

### 1.4 Spaced retrieval beats massed practice for retention

Once an item is initially learned, retention is markedly improved when
practice is distributed over time rather than concentrated in one session.
This is one of the most robust findings in learning science and applies
across domains. Related: retrieval practice (testing) outperforms
re-exposure (re-study) for long-term retention.

**Primary references:**

- Cepeda, N. J., Pashler, H., Vul, E., Wixted, J. T., & Rohrer, D. (2006).
  Distributed practice in verbal recall tasks: A review and quantitative
  synthesis. *Psychological Bulletin*, 132(3), 354–380.
  *— Meta-analysis of 184 articles; reports a robust spacing effect.*

- Roediger, H. L., III, & Karpicke, J. D. (2006). The power of testing
  memory: Basic research and implications for educational practice.
  *Perspectives on Psychological Science*, 1(3), 181–210.
  *— Reviews the testing/retrieval-practice effect.*

- Cepeda, N. J., Vul, E., Rohrer, D., Wixted, J. T., & Pashler, H. (2008).
  Spacing effects in learning: A temporal ridgeline of optimal retention.
  *Psychological Science*, 19(11), 1095–1102.
  *— Empirical paper on the relationship between spacing interval and retention interval.*

**Implication for this system:** Mastered stages do not disappear from the
schedule. They resurface at growing intervals to detect and prevent decay.
The first-pass implementation can be a simple recency-based scheduler;
later iterations can move toward a more principled spaced-repetition
algorithm (e.g., something in the SuperMemo / Leitner family).

### 1.5 Mastery learning as a curricular frame

The idea that learners should reach a high level of competence on one stage
before advancing to the next has its modern roots in Bloom's mastery
learning model and is reflected in contemporary intelligent tutoring
systems and adaptive learning research.

**Primary references:**

- Bloom, B. S. (1968). Learning for mastery. *Evaluation Comment* (UCLA
  Center for the Study of Evaluation of Instructional Programs), 1(2), 1–12.
  *— The seminal short paper proposing mastery learning.*

- Corbett, A. T., & Anderson, J. R. (1995). Knowledge tracing: Modeling the
  acquisition of procedural knowledge. *User Modeling and User-Adapted
  Interaction*, 4(4), 253–278.
  *— Introduces Bayesian Knowledge Tracing, the basis for many modern adaptive tutoring systems including the Cognitive Tutors.*

**Implication for this system:** Stages are gated by demonstrated mastery,
not by attempts attempted or time spent. A user cannot "advance" by
exposure alone.

---

## 2. The proposed curriculum (synthesis, not direct citation)

The specific stage decomposition (~36 stages across addition, subtraction,
multiplication, percent) is a **design decision** informed by the principles
in §1 and by typical primary-school mathematics sequencing. It is not lifted
from a single cited source and should not be presented as such.

The boundaries between stages (where to split A1 from A2, whether ×6 should
share a stage with ×3, etc.) are judgment calls aimed at making each stage:

- Atomic enough that latency on it is a meaningful signal.
- Coarse enough that drilling does not become diluted across too many
  stages.
- Aligned with reasonable prerequisite chains (single-digit before
  multi-digit; basic facts before procedures that require them).

The curriculum is one of the parts of this proposal **most subject to
revision based on actual data**. If the system shows that a particular
stage is too coarse — for example, if the user's accuracy and latency on
"sums to 20" cluster differently for sums ≤14 versus sums >14 — that stage
should split.

The general sequence (single-digit retrieval → multi-digit no-carry →
multi-digit with carry; tables before multi-digit multiplication; etc.) is
consistent with the Common Core State Standards for Mathematics, K–3
operations and algebraic thinking strand, which is the most widely-used
reference point for arithmetic sequencing in U.S. education:

- Common Core State Standards Initiative. *Mathematics Standards*.
  Available: <http://www.corestandards.org/Math/>

The CCSS is itself a synthesis rather than a primary research document; it
is cited here as a reference point for sequencing intuition, not as
authoritative cognitive science.

---

## 3. Latency targets

The latency targets attached to each stage in the proposed curriculum are
**starting points**, not canonical numbers from the literature.

What the literature does support:

- Adult fluent retrieval of single-digit addition and multiplication facts
  typically falls in roughly the **0.8–1.5 second range** in lab studies,
  measured from problem onset to vocal/keystroke response.
- Multi-digit procedural arithmetic typically takes **several seconds
  longer**, scaling with the number of operations and the presence of
  carry/borrow.

What the literature does *not* give us:

- Any single canonical "this is the latency target for an ordinary fluent
  adult on this exact stage" number. Reaction-time data varies by study,
  by population, by stimulus presentation modality, by response modality
  (vocal vs. keypress), and by trial structure.

The targets in our curriculum (e.g., 1.5s for single-digit retrieval, 4–6s
for multi-digit with carry) are **ballpark defaults** chosen within or near
the ranges suggested by the literature. They serve as starting calibration.
The system should adapt them per-user once enough data accumulates: if a
user consistently beats a target by a wide margin, the target should
tighten; if a target proves unreachable despite high accuracy, the target
was probably too aggressive.

This is one place to be especially honest with the user: the design uses
latency as a hard gate, but the specific gate values are best-guess
starting points, not measured constants.

A note on the metric itself: the literature most commonly reports **mean
reaction time on correct trials**, often with outlier trimming. We use
**median resolution latency over the last 10 attempts**, which is more
robust to outliers but is a design choice rather than a literature
convention.

---

## 4. What this document does *not* claim

To draw the boundary explicitly:

- It does not claim the proposed 36-stage curriculum is the only correct
  decomposition. Other decompositions could be reasonable.
- It does not claim the latency targets are precisely calibrated. They are
  starting points the system is expected to refine from the user's own data.
- It does not import any specific intelligent tutoring system's mastery
  model wholesale. The "accuracy ≥ X% AND median latency ≤ T" rule is a
  synthesis of the cited principles, not a reproduction of a particular
  published algorithm such as Bayesian Knowledge Tracing.
- It does not address motivation, affective factors, anxiety, or the social
  context of practice. Those are real and important factors but out of
  scope for the current arithmetic-fluency work.
- It does not draw on the much larger literature on mathematical word
  problems, multi-step reasoning, or conceptual understanding. The current
  scope is narrow: drilling factual and procedural fluency for basic
  arithmetic. Conceptual layers come later, if at all.

---

## 5. Reading list

Books and reports worth direct engagement for this design:

- *Adding It Up: Helping Children Learn Mathematics* (NRC, 2001). Free PDF
  via the National Academies Press.
  <https://nap.nationalacademies.org/catalog/9822>
- Campbell, J. I. D. (Ed.). *Handbook of Mathematical Cognition* (2005).
  Psychology Press. — Reference work; library or interlibrary loan.
- Willingham, D. T. *Why Don't Students Like School?* (2009). Jossey-Bass.
  — Accessible synthesis for educators of cognitive-science principles
  relevant to learning.
- Geary, D. C. *Children's Mathematical Development: Research and Practical
  Applications* (1994). American Psychological Association. — Older but
  authoritative on developmental sequencing of arithmetic skill.

Articles freely available online:

- Willingham, D. T. (2004). Practice makes perfect — but only if you
  practice beyond the point of perfection.
  <https://www.aft.org/periodical/american-educator/spring-2004/ask-cognitive-scientist-practice-makes-perfect>
- Willingham, D. T. (2006). How knowledge helps.
  <https://www.aft.org/periodical/american-educator/spring-2006/how-knowledge-helps>
- IES Practice Guide on Math RtI (Gersten et al., 2009).
  <https://ies.ed.gov/ncee/wwc/PracticeGuide/2>

Paywalled but high-leverage primary sources:

- Logan, G. D. (1988). *Psychological Review*, 95(4), 492–527.
- Cepeda et al. (2006). *Psychological Bulletin*, 132(3), 354–380.
- Campbell & Xue (2001). *Journal of Experimental Psychology: General*,
  130(2), 299–315.
- LeFevre et al. (1996). *Journal of Experimental Psychology: General*,
  125(3), 284–306.

---

## 6. Open questions worth tracking

These are points where the design depends on judgment that the literature
does not resolve cleanly. They should be revisited as data accumulates.

| Question | Why it's open |
|---|---|
| Are 36 stages the right granularity, or should it be ~25 or ~50? | Boundary choices are judgment calls. Data on within-stage clustering will inform splits. |
| Is "median over last 10 attempts" the right latency aggregator? | Literature uses mean of correct trials, sometimes trimmed. Median is robust to outliers but loses information. |
| Should the accuracy threshold be 85%, 90%, or 95%? | "Mastery" thresholds in the literature vary; common values include 80% (educational), 90% (research), 95% (high-stakes). |
| Should mastered stages return at fixed intervals or adaptive ones? | Spaced-repetition algorithms range from simple (Leitner) to elaborate (FSRS, SuperMemo). Initial implementation can be simple; refinement comes later. |
| How should context-grounded problems (real bills, real calendar entries) integrate with the stage system? | The stage taxonomy is built around abstract problems. Grounded problems should map to stages, but the mapping isn't trivial — a "$63.42 bill, 18% tip" problem touches both percent stages and could span multiple. |

---

## A final note from the author

This document exists because the user asked for it explicitly, after
expressing concern about over-confident, deferential AI output that changes
position whenever pushed.

The intent here is not to win an argument. It is to produce a reference
that any future session — whether with this assistant or another — can
consult to understand *why* the curriculum was structured the way it was.
If a claim in §1 is wrong, the document should be amended and the design
revisited. If a stage in §2 is wrong, the data will eventually show it and
that section should be revised.

The principles in §1 are well-established and the author is willing to
defend them. The curriculum in §2 is a synthesis the author is willing to
defend but not claim is uniquely correct. The latency targets in §3 are
starting points, not science. Those distinctions matter, and this document
is written to keep them visible.
