// V3 — mirrors trendRoc.ts after the GOOD_THRESHOLD = 128 alignment.
// Adds explicit regression for the band 128-191 that was being wrongly
// filtered in v1/v2.

const UNIT_TO_MS = { "/s": 1000, "/min": 60000, "/hr": 3600000 };
const MAX_SAMPLES = 20, MAX_WINDOW_MS = 5*60*1000, MIN_GOOD_SAMPLES = 3;
const GOOD = 128;   // matches ST_READ_OK used Dashboard-wide

function computeROC(samples, unit = "/min") {
  const empty = { value: 0, unit, samplesUsed: 0, totalInWindow: 0, windowSec: 0, isValid: false };
  if (!samples || samples.length === 0) return empty;

  let lastT = -Infinity;
  for (const s of samples) if (s.t > lastT) lastT = s.t;
  if (!isFinite(lastT)) return empty;
  const windowFloor = lastT - MAX_WINDOW_MS;

  const inWindow = [];
  for (const s of samples) {
    if (s.t < windowFloor) continue;
    inWindow.push(s);
  }
  inWindow.sort((a, b) => a.t - b.t);

  const good = [];
  for (let i = inWindow.length - 1; i >= 0; i--) {
    const s = inWindow[i];
    if (s.q !== undefined && s.q < GOOD) continue;
    if (!Number.isFinite(s.v)) continue;
    good.unshift(s);
    if (good.length >= MAX_SAMPLES) break;
  }

  if (good.length < MIN_GOOD_SAMPLES) {
    return { ...empty, totalInWindow: inWindow.length };
  }

  const t0 = good[0].t;
  let sX = 0, sY = 0, sXY = 0, sX2 = 0;
  for (const s of good) {
    const x = s.t - t0;
    sX += x; sY += s.v; sXY += x*s.v; sX2 += x*x;
  }
  const denom = good.length * sX2 - sX * sX;
  if (denom === 0) {
    return { ...empty, totalInWindow: inWindow.length };
  }
  const slopePerMs = (good.length * sXY - sX * sY) / denom;
  return {
    value: slopePerMs * UNIT_TO_MS[unit],
    unit,
    samplesUsed: good.length,
    totalInWindow: inWindow.length,
    windowSec: (good[good.length-1].t - good[0].t)/1000,
    isValid: true,
  };
}

function formatROC(r, eu) {
  if (!r.isValid) return "—";
  const abs = Math.abs(r.value);
  let dec;
  if (abs >= 100) dec = 0;
  else if (abs >= 10) dec = 1;
  else if (abs >= 0.01 || abs === 0) dec = 2;
  else dec = 4;
  const prefix = r.value > 0 ? "+" : "";
  return `${prefix}${r.value.toFixed(dec)} ${(eu ?? "").trim()}${r.unit}`;
}

let pass = 0, fail = 0;
function assert(cond, msg) {
  if (cond) { pass++; console.log("  PASS", msg); }
  else { fail++; console.log("  FAIL", msg); }
}

const t0 = 1_700_000_000_000;

console.log("Test 1: empty samples → invalid");
let r = computeROC([]);
assert(!r.isValid && r.totalInWindow === 0, "empty isValid=false, totalInWindow=0");

console.log("\nTest 2: linear ascending, slope 2 EU/s");
const linear = Array.from({length: 10}, (_, i) => ({ t: t0 + i*1000, v: 5 + i*2 }));
assert(Math.abs(computeROC(linear, "/min").value - 120) < 1e-6, "linear /min = 120");

console.log("\nTest 3: descending order produces same slope (defensive sort)");
const desc = [...linear].reverse();
assert(Math.abs(computeROC(desc, "/min").value - 120) < 1e-6, "descending /min = 120");

console.log("\nTest 4: negative slope");
const dec = Array.from({length: 5}, (_, i) => ({ t: t0 + i*1000, v: 100 - i*3 }));
assert(Math.abs(computeROC(dec, "/s").value + 3) < 1e-9, "negative = -3");

console.log("\n=== Quality threshold = 128 regression tests ===");

console.log("\nTest 5: pure 128-band data (the user's case) — must be GOOD");
const band128 = Array.from({length: 10}, (_, i) => ({
  t: t0 + i*1000, v: i*2, q: 130,    // 128-191 band, "good with minor flags"
}));
r = computeROC(band128, "/s");
assert(r.isValid, "ST 130 band must be treated as GOOD (was failing before fix)");
assert(r.samplesUsed === 10, `band-128 samplesUsed = ${r.samplesUsed}, expected 10`);
assert(Math.abs(r.value - 2) < 1e-9, `band-128 slope = ${r.value}, expected 2`);

console.log("\nTest 6: ST exactly 128 — boundary case, must be GOOD");
const at128 = Array.from({length: 5}, (_, i) => ({
  t: t0 + i*1000, v: i, q: 128,
}));
r = computeROC(at128, "/s");
assert(r.isValid, "ST = 128 (boundary) must be treated as GOOD");

console.log("\nTest 7: ST 127 — boundary on the wrong side, must be filtered");
const at127 = Array.from({length: 5}, (_, i) => ({
  t: t0 + i*1000, v: i, q: 127,
}));
r = computeROC(at127, "/s");
assert(!r.isValid, "ST = 127 (UNCERTAIN) must be filtered");
assert(r.samplesUsed === 0 && r.totalInWindow === 5,
       `127-band: samplesUsed=${r.samplesUsed} totalInWindow=${r.totalInWindow}`);

console.log("\nTest 8: mixed 192 (clean) and 130 (minor flags) — both kept");
const mixed = [
  { t: t0,         v: 0,   q: 192 },
  { t: t0 + 1000,  v: 1,   q: 130 },
  { t: t0 + 2000,  v: 2,   q: 192 },
  { t: t0 + 3000,  v: 3,   q: 150 },
  { t: t0 + 4000,  v: 4,   q: 200 },
];
r = computeROC(mixed, "/s");
assert(r.samplesUsed === 5, `mixed-good samplesUsed = ${r.samplesUsed}, expected 5`);
assert(Math.abs(r.value - 1) < 1e-9, `mixed-good slope = ${r.value}, expected 1`);

console.log("\nTest 9: ST 0 (truly BAD) still filtered");
const badMixed = [
  { t: t0,         v: 0,   q: 130 },
  { t: t0 + 1000,  v: 999, q: 0   },   // BAD - filtered
  { t: t0 + 2000,  v: 2,   q: 130 },
  { t: t0 + 3000,  v: 3,   q: 130 },
];
r = computeROC(badMixed, "/s");
assert(r.samplesUsed === 3, `BAD-mixed samplesUsed = ${r.samplesUsed}, expected 3`);
assert(Math.abs(r.value - 1) < 1e-9, `slope after BAD-filter = ${r.value}, expected 1`);

console.log("\nTest 10: all UNCERTAIN (64-127) → invalid with diagnostic info");
const allUncertain = Array.from({length: 10}, (_, i) => ({
  t: t0 + i*1000, v: i, q: 100,
}));
r = computeROC(allUncertain, "/s");
assert(!r.isValid, "all UNCERTAIN isValid=false");
assert(r.totalInWindow === 10 && r.samplesUsed === 0,
       `all-uncertain: totalInWindow=${r.totalInWindow} samplesUsed=${r.samplesUsed}`);

console.log("\nTest 11: 5-minute window cap");
const wide = Array.from({length: 30}, (_, i) => ({ t: t0 + i*60_000, v: i }));
r = computeROC(wide, "/s");
assert(r.samplesUsed === 6 && r.totalInWindow === 6,
       `5-min cap: samplesUsed=${r.samplesUsed} totalInWindow=${r.totalInWindow}`);

console.log("\nTest 12: 20-sample cap from end of window");
const many = Array.from({length: 100}, (_, i) => ({ t: t0 + i*100, v: i }));
r = computeROC(many, "/s");
assert(r.samplesUsed === 20, `cap = ${r.samplesUsed}, expected 20`);

console.log("\nTest 13: NaN/Infinity rejected even when ST is GOOD");
const dirty = [
  { t: t0,         v: 1,        q: 192 },
  { t: t0 + 1000,  v: NaN,      q: 192 },
  { t: t0 + 2000,  v: 3,        q: 192 },
  { t: t0 + 3000,  v: Infinity, q: 192 },
  { t: t0 + 4000,  v: 5,        q: 192 },
  { t: t0 + 5000,  v: 6,        q: 192 },
];
r = computeROC(dirty, "/s");
assert(r.samplesUsed === 4, `cleaned samplesUsed = ${r.samplesUsed}, expected 4`);

console.log("\nTest 14: q absent → treated as GOOD (frontend buffer case)");
const noQ = Array.from({length: 5}, (_, i) => ({ t: t0 + i*1000, v: i*2 }));
r = computeROC(noQ, "/s");
assert(r.isValid && r.samplesUsed === 5, "no-q treated as good");

console.log("\nTest 15: formatROC formatting unchanged");
const mkR = (v) => ({ value: v, unit: "/min", samplesUsed: 5, totalInWindow: 5, windowSec: 60, isValid: true });
assert(formatROC(mkR(0.42), "°C") === "+0.42 °C/min", `+0.42 °C/min`);
assert(formatROC(mkR(-125), "bar") === "-125 bar/min", `-125 bar/min`);
assert(formatROC(mkR(0), "kg") === "0.00 kg/min", `0.00 kg/min`);
assert(formatROC(mkR(0.00123), "ppm") === "+0.0012 ppm/min", `+0.0012 ppm/min`);
assert(formatROC(mkR(45.7), "psi") === "+45.7 psi/min", `+45.7 psi/min`);

console.log("\nTest 16: too few good → invalid, totalInWindow tracked");
r = computeROC([{ t: t0, v: 1 }, { t: t0 + 1000, v: 2 }], "/s");
assert(!r.isValid && r.totalInWindow === 2, "2-sample invalid, totalInWindow=2");

console.log("\nTest 17: all same timestamp → zero-denom invalid");
r = computeROC([
  { t: t0, v: 1 }, { t: t0, v: 2 }, { t: t0, v: 3 }, { t: t0, v: 4 }
], "/s");
assert(!r.isValid, "zero denom invalid");

console.log(`\n${pass} PASS / ${fail} FAIL`);
process.exit(fail > 0 ? 1 : 0);
