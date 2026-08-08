const { spawnSync } = require("node:child_process");
const path = require("node:path");

const action = process.argv[2];

if (action !== "start" && action !== "stop") {
  throw new Error("runtime_action_must_be_start_or_stop");
}

const script = process.platform === "win32"
  ? path.join(__dirname, action + "_local_runtime.ps1")
  : process.platform === "darwin"
    ? path.join(__dirname, action + "_local_runtime.sh")
    : null;

if (script === null) {
  throw new Error("unsupported_runtime_platform: " + process.platform);
}

const command = process.platform === "win32" ? "powershell.exe" : "bash";
const args = process.platform === "win32"
  ? ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script]
  : [script];
const result = spawnSync(command, args, { stdio: "inherit" });

if (result.error) {
  throw result.error;
}

process.exitCode = result.status ?? 1;
