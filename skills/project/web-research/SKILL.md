---
name: web-research
description: Structured workflow for researching topics on the web and returning concise, cited results.
license: MIT
allowed-tools: internet_search
---

# Web Research Skill

## When to Use
- User asks for factual information not guaranteed to be in local files.
- User asks for recent updates, latest status, news, schedules, or prices.
- User asks for links/sources/citations.

## Workflow
1. Clarify objective and output shape (summary, comparison, timeline, etc.).
2. Run focused searches with specific keywords and entities.
3. Prefer multiple credible sources over a single source.
4. For time-sensitive facts, check recency and include exact dates.
5. Return concise findings with direct URLs and key caveats.

## Output Style
- Keep answers practical and short.
- Use bullets for findings.
- Include a `Sources:` section with URLs.

## Reliability Checks
- If sources conflict, call out the disagreement.
- Distinguish facts from assumptions.
- State when data may change over time.
