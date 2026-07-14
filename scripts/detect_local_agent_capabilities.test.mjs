import assert from "node:assert/strict";
import test from "node:test";

import {
  parseClaudeCapabilities,
  parseCodexCapabilities,
} from "./detect_local_agent_capabilities.mjs";

test("parses Codex cached models and CLI options", () => {
  const result = parseCodexCapabilities({
    help: `
      --sandbox <MODE>
        [possible values: read-only, workspace-write, danger-full-access]
      --ask-for-approval <POLICY>
        - untrusted: ask for untrusted commands
        - on-request: model decides
        - never: never ask
    `,
    version: "codex-cli 1.2.3",
    cache: {
      models: [{
        slug: "gpt-test",
        display_name: "GPT Test",
        visibility: "list",
        default_reasoning_level: "medium",
        supported_reasoning_levels: [{ effort: "low" }, { effort: "medium" }],
      }],
    },
    configText: 'model = "gpt-test"\nmodel_reasoning_effort = "low"\n',
    profiles: ["review"],
  });

  assert.deepEqual(result.models[1], {
    id: "gpt-test",
    label: "GPT Test",
    description: "",
    efforts: ["low", "medium"],
    default_effort: "medium",
  });
  assert.deepEqual(result.models[0].efforts, ["low", "medium"]);
  assert.deepEqual(result.options.sandbox, ["read-only", "workspace-write", "danger-full-access"]);
  assert.deepEqual(result.options.approval_policy, ["untrusted", "on-request", "never"]);
  assert.deepEqual(result.options.profile, ["review"]);
});

test("parses Claude aliases, efforts, and permission modes from help", () => {
  const result = parseClaudeCapabilities({
    help: `
      --effort <level> Effort level (low, medium, high, xhigh, max)
      --model <model> Provide an alias for the latest model (e.g. 'fable', 'opus', or 'sonnet')
      --permission-mode <mode> (choices: "acceptEdits", "manual", "plan")
    `,
    version: "2.0.0",
    settings: { effortLevel: "high", permissions: { defaultMode: "plan" } },
    agents: ["reviewer"],
  });

  assert.deepEqual(result.models.map((model) => model.id), ["default", "fable", "opus", "sonnet"]);
  assert.deepEqual(result.models[0].efforts, ["low", "medium", "high", "xhigh", "max"]);
  assert.deepEqual(result.options.permission_mode, ["acceptEdits", "manual", "plan"]);
  assert.deepEqual(result.options.agent, ["reviewer"]);
  assert.equal(result.defaults.effort, "high");
  assert.equal(result.defaults.permission_mode, "plan");
});
