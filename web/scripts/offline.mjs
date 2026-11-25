import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { existsSync } from "node:fs";
const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const python = resolve(root, ".venv/bin/python");
if (!existsSync(python))
  throw new Error("请先在项目根目录安装后端 .venv 依赖。");
const apiPort = process.env.DRUGPAPER_DEV_API_PORT || "18001";
const webPort = process.env.DRUGPAPER_DEV_WEB_PORT || "5173";
const webHost = process.env.DRUGPAPER_DEV_WEB_HOST || "127.0.0.1";
const fixture = resolve(root, "tests/fixtures/jmedchem_2c00949");
const args = [
  "-m",
  "scripts.dev_workbench",
  "--port",
  apiPort,
  "--data-dir",
  resolve(root, "data/workbench-dev"),
];
if (existsSync(fixture)) args.push("--fixture", fixture);
if (process.env.DRUGPAPER_DEV_CONNECTED === "1") args.push("--connected");
console.log(
  process.env.DRUGPAPER_DEV_CONNECTED === "1"
    ? "启动服务联调工作台：使用 data/workbench-dev 隔离开发库，启动不执行推理。"
    : "启动隔离离线工作台：仅导入现有解析产物，持久化至 data/workbench-dev，不修改业务库。",
);
const children = [
  spawn(python, args, { cwd: root, stdio: "inherit" }),
  spawn(
    process.execPath,
    [
      resolve(root, "web/node_modules/vite/bin/vite.js"),
      "--host",
      webHost,
      "--port",
      webPort,
    ],
    {
      cwd: resolve(root, "web"),
      stdio: "inherit",
      detached: process.platform !== "win32",
      env: {
        ...process.env,
        DRUGPAPER_API_TARGET: `http://127.0.0.1:${apiPort}`,
      },
    },
  ),
];
let stopping = false;
function stop(code = 0) {
  if (stopping) return;
  stopping = true;
  process.exitCode = code;
  for (const child of children)
    if (child.exitCode === null) child.kill("SIGTERM");
}
process.once("SIGINT", () => stop());
process.once("SIGTERM", () => stop());
for (const child of children) {
  child.on("error", (e) => {
    console.error(e.message);
    stop(1);
  });
  child.on("exit", (code, signal) => {
    if (!stopping) stop(code ?? (signal ? 1 : 0));
  });
}
