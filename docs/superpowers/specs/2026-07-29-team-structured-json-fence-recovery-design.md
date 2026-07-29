# Team Structured JSON Fence Recovery Design

## Status

- Decision: approved bounded normalization
- Scope: Team task plans and worker `TaskOutcome` responses
- Compatibility: no API, database, or provider protocol change

## Context

Team Run `af7c358273c54fb0b522b0b66d054a57` failed before creating
tasks. Its Claude Sonnet leader returned a valid task-plan JSON array twice,
but wrapped both responses in a single Markdown `json` code fence.
`_parse_task_plan()` rejected the wrapper before parsing the JSON, so the
continuous cycle failed with `Planner response must not use code fences`.

Removing only the outer fence from the captured response made the existing
task schema validation pass with two valid task definitions. The failure was
therefore a transport-format mismatch, not an invalid plan.

The exact planner error is limited to task creation, but the same mismatch can
also affect worker completion. `parse_task_outcome()` rejects a fenced JSON
object and converts the worker result to `blocked` with
`invalid_task_outcome`.

Mediation and worker information-request protocols have different contracts:
mediation already accepts a surrounding fence, while `needs_info` deliberately
uses a fenced JSON block. Library Drafts use explicit XML-like markers and
must remain unchanged.

## Goal

Prevent semantically valid Team task plans and worker outcomes from failing
solely because a model wrapped the entire JSON payload in one Markdown
`json` fence, without weakening any domain, path, acceptance, or completion
validation.

## Accepted input

The normalizer accepts exactly two outer forms after trimming whitespace:

1. Raw JSON whose first character begins the expected JSON value.
2. One complete lowercase `json` Markdown fence containing the JSON value:

   ````text
   ```json
   <JSON value>
   ```
   ````

Only the outer representation is normalized. The parser that consumes the
result still decides whether the JSON value must be an array or object and
validates every field.

## Rejected input

Normalization must reject or leave invalid all ambiguous representations:

- prose before or after a fence;
- text after the closing fence;
- multiple fenced blocks;
- an unterminated fence;
- a non-`json` fence;
- malformed JSON;
- JSON with missing or unknown fields;
- unsafe or absolute deliverable paths;
- tasks without a required output or verification;
- worker outcomes with invalid status, evidence, or duplicate entries.

The implementation must not search arbitrary prose for the first parseable
JSON fragment.

## Architecture

Add one small pure helper in a Team-owned structured-output module:

```python
def normalize_json_envelope(content: str) -> str:
    """Return raw JSON text, unwrapping one exact outer `json` fence."""
```

The helper performs only envelope recognition and removal. It does not call
`json.loads`, validate a schema, select fields, or repair malformed content.

Use it at two strict parsing boundaries:

1. `_parse_task_plan()` before `json.loads`.
2. `parse_task_outcome()` before `json.loads`.

Do not route mediation, `needs_info`, Library Draft parsing, delivery-file
JSON, or general API JSON through this helper. Their contracts and failure
semantics differ.

## Data flow

```text
model response
  -> normalize_json_envelope
     -> raw JSON or one unwrapped outer json fence
  -> json.loads
  -> existing exact schema and safety validation
  -> task creation or TaskOutcome
```

For planner responses, the existing single retry remains. The retry is still
used for invalid JSON or schema failures, but a valid fenced response succeeds
immediately and does not spend the retry.

For worker outcomes, valid fenced output becomes a normal `TaskOutcome`.
Malformed or unsafe output continues to become `blocked` with
`invalid_task_outcome`.

## Error handling

Envelope normalization must not turn ambiguity into success.

- Raw content is returned after outer whitespace trimming.
- A string beginning with a code fence is unwrapped only when the entire
  trimmed response is exactly one `json` fence.
- Any other fenced representation remains invalid at the consuming parser.
- Existing exception types and stored failure codes remain stable.
- Model response bodies are not added to application logs or database errors.

This design intentionally does not add more model retries. The observed
failure is deterministic envelope formatting, and local normalization is more
reliable and cheaper than asking the same model to repeat an already valid
payload.

## Testing

Unit tests must cover the shared envelope helper and both consumers.

### Envelope tests

- raw JSON remains unchanged apart from outer whitespace;
- one complete `json` fence is unwrapped;
- prose plus a fence is not accepted;
- trailing prose or another fence is not accepted;
- non-`json` and unterminated fences are not accepted.

### Task-plan tests

- raw valid task array succeeds;
- fenced valid task array succeeds;
- malformed JSON still fails;
- missing or unknown fields still fail;
- unsafe output paths and empty acceptance still fail.

### Worker-outcome tests

- raw valid outcome succeeds;
- fenced valid outcome succeeds;
- prose, malformed JSON, invalid status, unknown fields, unsafe paths, and
  invalid verification evidence still produce `TaskOutcomeError`.

### Runtime regression

A continuous cycle whose leader returns the captured fenced task plan must
create its tasks and proceed to `resume()` rather than setting the cycle to
`failed`. A worker returning a fenced valid outcome must reach the same
acceptance path as the equivalent raw JSON outcome.

## Non-goals

- Supporting arbitrary Markdown extraction.
- Repairing malformed JSON.
- Relaxing exact task-plan or TaskOutcome schemas.
- Changing the number of provider retries.
- Changing mediation, `needs_info`, or Library Draft marker parsing.
- Retrying or mutating the already failed `af7c3582` cycle automatically.
- Changing Team Run state transitions, API payloads, or database schema.
