"""Severity rubric. Injected verbatim into every reviewer's system prompt so
findings carry consistent severities across sub-reviewers.
"""

SEVERITY_RUBRIC = """\
## Severity Rubric

- **critical**: Exploitable vulnerability, data corruption, or guaranteed
  production outage. The change MUST NOT merge in its current form.
- **high**: Likely bug or vulnerability that will manifest under normal use,
  or a security weakness with mitigating factors. Strongly request changes.
- **medium**: Real defect under specific conditions (edge cases, error paths,
  unusual inputs). Should be fixed before merge unless explicitly deferred.
- **low**: Minor issue, suboptimal pattern, or future maintenance hazard.
  Nice-to-fix; non-blocking.
- **info**: Observation, question, or suggestion with no defect implied.

## What is NOT a finding

- Pure style issues already enforced by linters.
- Personal preference about naming or structure.
- Hypothetical concerns with no concrete trigger.
- Generic best-practice reminders unrelated to the diff.

If you are not at least 50% confident the issue is real and concrete, do NOT
emit a finding. False positives erode the value of every other finding.
"""
