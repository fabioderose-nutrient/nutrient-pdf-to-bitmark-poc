// Validates the generated markup with the bitmark Association's official
// parser (@gmb/bitmark-parser-generator) and writes the bitmark JSON next to
// it. Usage: node validate.js <book.bitmark> <book.json> <report.json>
const fs = require("fs");
const { BitmarkParserGenerator } = require("@gmb/bitmark-parser-generator");

async function main() {
  const [src, jsonOut, reportOut] = process.argv.slice(2);
  const markup = fs.readFileSync(src, "utf8");
  const bpg = new BitmarkParserGenerator();
  const started = Date.now();
  let bits = await bpg.convert(markup);
  if (typeof bits === "string") bits = JSON.parse(bits);
  const list = Array.isArray(bits) ? bits : [];

  const counts = {};
  const problems = [];
  for (const entry of list) {
    const bit = entry.bit || {};
    counts[bit.type] = (counts[bit.type] || 0) + 1;
    const errs = (entry.parser && (entry.parser.errors || entry.parser.warnings)) || [];
    for (const e of errs) problems.push({ bit: bit.type, id: bit.id, message: e.message || String(e) });
    if (bit.type === undefined || bit.type === "_error") problems.push({ bit: bit.type, message: "unknown bit" });
  }

  fs.writeFileSync(jsonOut, JSON.stringify(list, null, 2));
  const report = {
    parser: "@gmb/bitmark-parser-generator " + bpg.version(),
    bitmarkVersion: (list[0] && list[0].parser && list[0].parser.bitmarkVersion) || null,
    bits: list.length,
    byType: counts,
    problems,
    valid: problems.length === 0,
    ms: Date.now() - started,
  };
  fs.writeFileSync(reportOut, JSON.stringify(report, null, 2));
  process.stdout.write(JSON.stringify(report) + "\n");
}

main().catch((err) => {
  process.stderr.write(String(err && err.stack || err) + "\n");
  process.exit(1);
});
