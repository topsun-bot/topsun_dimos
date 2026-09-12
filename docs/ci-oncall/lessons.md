<!--
SPDX-License-Identifier: Apache-2.0
Adapted from anthropics/oncall-kit (Apache-2.0)
https://github.com/anthropics/oncall-kit
https://claude.com/blog/ai-ci-cd-on-call
-->

# lessons.md: living incident log

Status: empty seed; no topsun-bot CI incidents recorded yet.

Append after each real incident or died hypothesis. Do not paste
fictional kit examples here. Search by `#tag`; do not ingest the whole
file once it grows.

## Entry formats

**Incident entry** (default: resolved incident or died hypothesis):

```
## {{date}} · {{one-line title}} · #{{failure-class-tag}} · {{issue/PR/run URL}}
- What happened: {{one fresh-reader sentence: symptom}}
- Root cause: {{mechanism, with evidence link}}
- Fix: {{what resolved it, who decided}}
- Gotcha / rule: {{reusable lesson}}
- Tags: #{{class}} {{optional extra tags}}
- Links: {{Actions run, PR, issue}}
```

**Investigation entry** (long or inconclusive trail):

```
## {{date}} · Investigation: {{what}} · #{{tag}} · {{URL}}
| step | result |
|---|---|
| {{check run}} | {{pass/fail + link}} |
- Checked out on paper: {{what SHOULD work, each verified}}
- Could not verify: {{what, why, one-line command for the owner}}
- Rabbit holes eliminated: {{hypothesis -> evidence that killed it}}
- Confidence: {{~N%, and what the remaining % is}}
```

**GOTCHA one-liner** (tool or environment surprise, not an incident):

```
- GOTCHA ({{date}}): {{the surprise, and the rule it implies}}
```

## Graduation

When a tag accumulates **3 entries with the same mechanism**, or an entry
becomes a procedure someone could follow cold, promote it into that
class's file under `references/` by PR and replace the entries with one
pointer line.

## Classes

`#test-failures` · `#cache-publish` · `#runner-infra`

---

<!-- Real entries go below this line. -->
