import { readFile } from "node:fs/promises";

// Het add-onimage bouwt boven op de gewone bridge-image. Houd deze kleine
// logger daarom hier zelfstandig: bij een add-onupdate is hij al actief vóór
// de bridgecode geladen wordt.
const formatter = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Europe/Brussels",
  year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", second: "2-digit",
  hourCycle: "h23", timeZoneName: "longOffset"
});
const originalConsole = Object.fromEntries(
  ["log", "info", "warn", "error"].map((method) => [method, console[method].bind(console)])
);
for (const method of Object.keys(originalConsole)) {
  console[method] = (...args) => {
    const parts = formatter.formatToParts(new Date());
    const value = (type) => parts.find((part) => part.type === type)?.value;
    originalConsole[method](
      `[${value("year")}-${value("month")}-${value("day")} ${value("hour")}:${value("minute")}:${value("second")} ${value("timeZoneName")}]`,
      ...args
    );
  };
}

const optionsPath = "/data/options.json";
let options;

try {
  options = JSON.parse(await readFile(optionsPath, "utf8"));
} catch (error) {
  console.error(`Unable to read Home Assistant app configuration: ${error.message}`);
  process.exit(1);
}

if (typeof options.bridge_api_key !== "string" || options.bridge_api_key.length < 16) {
  console.error("bridge_api_key must contain at least 16 characters");
  process.exit(1);
}

process.env.BRIDGE_API_KEY = options.bridge_api_key;
process.env.DATA_DIR = "/data";
process.env.HOST = "0.0.0.0";
process.env.PORT = "3000";

await import("./src/server.js");
