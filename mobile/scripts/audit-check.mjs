#!/usr/bin/env node
// The app's dependency audit, held the way the server's mutation gate holds
// its survivor ledger: every high or critical advisory in the production
// dependency tree is either listed in audit-allowlist.json with its reason,
// or the build fails. A listed advisory that no longer appears also fails,
// so the list shrinks when upstream ships a fix instead of quietly growing.
//
// Exit 0: clean against the list. 1: an unlisted advisory or a stale entry.
// 2: no result - npm audit did not produce a report (offline, registry
// down). A question that went unanswered is never reported as a pass.
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';

const allowlist = JSON.parse(readFileSync(new URL('../audit-allowlist.json', import.meta.url), 'utf8'));

let raw;
try {
  raw = execFileSync('npm', ['audit', '--omit=dev', '--json'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
} catch (error) {
  raw = error.stdout; // npm audit exits non-zero whenever it finds anything
}

let report;
try {
  report = JSON.parse(raw);
} catch {
  console.error('audit-check: npm audit produced no report - no result, not a pass.');
  process.exit(2);
}
if (!report.vulnerabilities) {
  console.error(`audit-check: npm audit reported an error - no result: ${JSON.stringify(report.error ?? report)}`);
  process.exit(2);
}

const found = new Map();
for (const vulnerability of Object.values(report.vulnerabilities)) {
  for (const via of vulnerability.via) {
    if (typeof via === 'object' && (via.severity === 'high' || via.severity === 'critical')) {
      found.set(via.url, via);
    }
  }
}

const listed = new Set(allowlist.map((entry) => entry.advisory));
const unlisted = [...found.values()].filter((via) => !listed.has(via.url));
const stale = allowlist.filter((entry) => !found.has(entry.advisory));

for (const via of unlisted) {
  console.error(`UNLISTED  ${via.severity}  ${via.name}  ${via.title}\n          ${via.url}`);
}
for (const entry of stale) {
  console.error(`STALE     ${entry.package}  ${entry.advisory} no longer appears - remove it from audit-allowlist.json`);
}
console.log(`audit-check: ${found.size} high/critical advisory(ies), ${allowlist.length} listed, ` +
  `${unlisted.length} unlisted, ${stale.length} stale.`);
process.exit(unlisted.length || stale.length ? 1 : 0);
