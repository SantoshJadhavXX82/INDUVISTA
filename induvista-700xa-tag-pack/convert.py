"""Convert the Daniel/Emerson 700XA UK Modbus Listing into InduVista tag rows.

Outputs (in /home/claude/700xa/out):
  blocks.sql        — register-block CREATE statements (all blocks)
  tags_core.csv     — ~150 fiscally-useful tags (recommended import)
  tags_full.csv     — every tag (~1500)

Avoids the user's existing GC_SIM_7001_16 block (addresses 7001–7016) so the
new config slots in alongside their working mole-% setup.
"""
from __future__ import annotations
import pandas as pd
import re
import csv
import os

XLS = "/mnt/user-data/uploads/700XA_-_UK_MODBUS_Listing.xls"
OUT = "/home/claude/700xa/out"
DEVICE_NAME = "GC_SIM_001"
BLOCK_PREFIX = "GC700XA"
DEFAULT_SCAN_MS = 5000              # 5s — 700XA cycle is minutes, don't hammer
EXCLUDE_ADDRS = set(range(7001, 7017))  # mole-% already owned by GC_SIM_7001_16

# ---------------------------------------------------------------------------
# 1. Read the spreadsheet
# ---------------------------------------------------------------------------
df = pd.read_excel(XLS, engine="xlrd", header=None, sheet_name="Sheet1")
df.columns = ["addr", "type", "desc", "c3", "rw", "unit"]
df = df.dropna(how="all").reset_index(drop=True)
for c in ("addr", "type", "desc", "rw"):
    df[c] = df[c].astype(str)
df["unit"] = df["unit"].apply(
    lambda x: "" if pd.isna(x) or str(x).strip().lower() == "nan" else str(x).strip()
)

TYPE_MAP = {
    "BOOLEAN":     "bool",
    "INT":         "uint16",
    "Bitmap(INT)": "uint16",
    "LONG":        "int32",
    "FLOAT":       "float32",
}

def short_stem(desc: str) -> str:
    """Compact identifier-like stem from Daniel's prose."""
    s = desc
    repl = [
        (r"Last\s+Analy_?",          ""),
        (r"Last\s+FCalib_?",         "FCAL_"),
        (r"Last\s+Calib_?",          "CAL_"),
        (r"Last\s+Run\s+Data",       "LAST_RUN"),
        (r"Current\s+Value\[?",      "AI_"),
        (r"Calc\s+Result\[?",        "CALC_"),
        (r"Avg\s+Molecular\s+Weight","AVG_MW"),
        (r"\bAverage[s]?\b",         "AVG"),
        (r"\bArchive\b",             "ARCH"),
        (r"GS\(M\)R",                "GSMR"),
        (r"Real\s+Rel\s+Den\s+Gas",  "RHO_REL"),
        (r"Gas\s+Den",               "RHO"),
        (r"Wobbe\s+Index",           "WI"),
        (r"\bSup\b",                 "SUP"),
        (r"\bInf\b",                 "INF"),
        (r"\bDry\b",                 "DRY"),
        (r"\bSat\b",                 "SAT"),
        (r"\bPri\b",                 "P"),
        (r"\bSec\b",                 "S"),
        (r"\bComponent\s+Code\b",    "CCODE"),
        (r"\bComponent\s+Data\b",    "CDATA"),
        (r"\bReference\b",           "REF"),
        (r"\bRel\s+Resp\s+Factor\b", "RRF"),
        (r"\bResp\s+Factor\b",       "RF"),
        (r"\bRet\s+Time\b",          "RT"),
        (r"\bMulti-level\s+Calib\b", "MLC"),
        (r"\bUnnormalized\s+Conc\b", "UNN_CONC"),
        (r"\bDiscrete\s+Output\b",   "DOUT"),
        (r"\bDiscrete\s+Input\b",    "DIN"),
        (r"\bMole\s*%\b",            "MOLE_PCT"),
        (r"\bWeight\s*%\b",          "WT_PCT"),
        (r"\bZ\s*Factor\b",          "Z"),
        (r"\bStream\s+Number\b",     "STREAM_NO"),
        (r"\bCycle\s+Time\b",        "CYCLE_T"),
        (r"\bRun\s+Time\b",          "RUN_T"),
        (r"\bAnalysis\s+Time\b",     "ANALYSIS_T"),
        (r"\bNew\s+Data\s+Available\b", "NDA"),
        (r"\bNew\s+Data\s+Flag\b",   "NDF"),
        (r"\bAnaly/Calib\s+Flag\b",  "ANAL_CAL_FLAG"),
        (r"\bAcknowledge\b",         "ACK"),
        (r"\bAvailable\b",           ""),
        (r"\bCurrent\b",             "CUR"),
        (r"\bAnalog\s+Input\b",      "AI"),
        (r"\bAnalog\s+Output\b",     "AO"),
        (r"\bCalibration\b",         "CALIB"),
    ]
    for pat, sub in repl:
        s = re.sub(pat, sub, s, flags=re.IGNORECASE)
    s = re.sub(r"\b\d+\s*-\s*\d+\s*$", "", s).strip()
    s = re.sub(r"\([^)]*\)", "", s).strip()
    s = re.sub(r"^\d+\s*-\s*Stream\s+\d+_", "STREAM_", s)
    s = re.sub(r"[^\w]+", "_", s).strip("_").upper()
    s = re.sub(r"_+", "_", s)
    return s

def addr_range(s: str) -> list[int]:
    s = s.strip()
    if "-" in s:
        a, b = s.split("-")
        return list(range(int(a), int(b) + 1))
    return [int(s)]

raw = []
for _, r in df.iterrows():
    try:
        addrs = addr_range(r["addr"])
    except ValueError:
        continue
    if r["type"] not in TYPE_MAP:
        continue
    desc = re.sub(r",.*$", "", r["desc"]).strip()
    desc = re.sub(r"\s+", " ", desc)
    # Bitmap(INT) rows have a "0:Unused, 1:..., 14:Analyzer Failure" payload
    # in the description, so the comma-trim above leaves something useless.
    # Override these with stable address-tagged names; the full bit map goes
    # into the description column for reference.
    if r["type"] == "Bitmap(INT)":
        stem = f"SYS_ALARM_BITMAP_{addrs[0]}"
        desc = re.sub(r"\s+", " ", r["desc"])  # restore full bit list
    else:
        stem = short_stem(desc)
    n = len(addrs)
    for i, a in enumerate(addrs, start=1):
        unit = r["unit"]
        if n == 1 and unit:
            name = f"{stem}_{unit}"
        elif n > 1:
            name = f"{stem}_{i}"
        else:
            name = stem
        raw.append({
            "address":   a,
            "type_raw":  r["type"],
            "data_type": TYPE_MAP[r["type"]],
            "desc":      desc,
            "rw":        r["rw"],
            "name_raw":  name,
        })

raw = [r for r in raw if r["address"] not in EXCLUDE_ADDRS]
raw = [r for r in raw if not re.search(r"\bUNUSED\b", r["name_raw"], flags=re.IGNORECASE)]

seen: dict[str, int] = {}
for r in raw:
    nm = re.sub(r"^_+|_+$", "", r["name_raw"])[:50]
    if nm in seen:
        seen[nm] += 1
        nm = f"{nm}_{seen[nm]}"
    else:
        seen[nm] = 1
    r["name"] = nm

# Blocks — contiguous run of same data_type, cap at 32 logical addresses.
# Allow gaps up to MAX_GAP addresses so we don't fragment into single-tag
# blocks just because the spreadsheet has "Unused" rows in the middle. The
# device returns all addresses in the requested range; we simply don't decode
# the gap positions into tags. One bigger poll > many tiny ones.
MAX_BLOCK = 32
MAX_GAP = 4
blocks = []
cur = None
for r in raw:
    same_run = (
        cur is not None
        and r["data_type"] == cur["data_type"]
        and r["address"] > cur["end"]
        and r["address"] - cur["end"] <= MAX_GAP + 1
        and (r["address"] - cur["start"] + 1) <= MAX_BLOCK
    )
    if not same_run:
        if cur:
            blocks.append(cur)
        cur = {"start": r["address"], "end": r["address"],
               "data_type": r["data_type"], "count": 1, "tags": [r]}
    else:
        cur["end"] = r["address"]
        cur["count"] = cur["end"] - cur["start"] + 1   # logical-span, including gaps
        cur["tags"].append(r)
if cur:
    blocks.append(cur)
for b in blocks:
    b["name"] = f"{BLOCK_PREFIX}_{b['start']}_{b['count']}"

# Core ranges — fiscal essentials
CORE_RANGES = [
    (1001, 1010), (3033, 3047), (3058, 3065), (3098, 3102),
    (5001, 5002),
    (7017, 7054), (7085, 7094), (7122, 7125),
    (8963, 8964),
    (9006, 9014), (9022, 9035),
]
def in_core(a: int) -> bool:
    return any(lo <= a <= hi for lo, hi in CORE_RANGES)

os.makedirs(OUT, exist_ok=True)

# blocks.sql
with open(f"{OUT}/blocks.sql", "w") as f:
    f.write(f"""-- Daniel/Emerson 700XA — register blocks for InduVista
-- Run AFTER confirming the {DEVICE_NAME} device exists.
-- All blocks created with enabled=FALSE; enable each one only after the
-- corresponding tags are inserted and reviewed.

DO $$
DECLARE
  dev_id INTEGER;
BEGIN
  SELECT id INTO dev_id FROM devices WHERE name = '{DEVICE_NAME}';
  IF dev_id IS NULL THEN
    RAISE EXCEPTION 'Device % not found.', '{DEVICE_NAME}';
  END IF;
""")
    for b in blocks:
        f.write(f"""
  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, '{b['name']}', 3, {b['start']}, {b['count']}, 'ENRON_HOLDING',
     {DEFAULT_SCAN_MS}, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
""")
    f.write("\nEND $$;\n")

# CSVs
CSV_HEADER = [
    "name","device_name","block_name","data_type",
    "function_code","address","register_count","byte_order",
    "engineering_unit","scale","offset","min_value","max_value",
    "description","groups","named_set",
    "is_heartbeat","heartbeat_max_stale_sec","writable",
]

def eu_for(name: str) -> str:
    if "MOLE_PCT" in name or "WT_PCT" in name: return "mol-%"
    if "CV_"  in name: return "MJ/m3"
    if "WI_"  in name or name.endswith("_WI"): return "MJ/m3"
    if "RHO_" in name: return "kg/m3"
    if name.startswith("CYCLE_T") or name.startswith("RUN_T") or name.startswith("ANALYSIS_T"):
        return "s"
    return ""

block_for_addr = {a: b for b in blocks
                  for a in range(b["start"], b["start"] + b["count"])}

def write_csv(path: str, keep):
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(CSV_HEADER)
        for r in raw:
            if not keep(r):
                continue
            b = block_for_addr[r["address"]]
            writable = "true" if r["rw"] == "RD_WR" else "false"
            w.writerow([
                r["name"], DEVICE_NAME, b["name"], r["data_type"],
                3, r["address"], "", "ABCD",
                eu_for(r["name"]), 1, 0, "", "",
                r["desc"][:200], "", "",
                "false", "", writable,
            ])

write_csv(f"{OUT}/tags_core.csv", lambda r: in_core(r["address"]))
write_csv(f"{OUT}/tags_full.csv", lambda r: True)

core_count = sum(1 for r in raw if in_core(r["address"]))
print(f"Total expanded tags     : {len(raw)}")
print(f"Core (fiscal) tags      : {core_count}")
print(f"Blocks generated        : {len(blocks)}")
core_blocks = sorted({block_for_addr[r['address']]['name']
                      for r in raw if in_core(r['address'])})
print(f"Blocks containing core  : {len(core_blocks)}")
print()
print("Core block list:")
for nm in core_blocks:
    b = next(b for b in blocks if b["name"] == nm)
    print(f"  {nm:<22} addr {b['start']}-{b['end']} ({b['count']:>2} tags, {b['data_type']})")
print()
print("First 25 core tags:")
print(f"  {'addr':>6}  {'data_type':<8} {'rw':<2}  name")
n = 0
for r in raw:
    if in_core(r["address"]):
        rw = "W" if r["rw"] == "RD_WR" else "r"
        print(f"  {r['address']:>6}  {r['data_type']:<8} {rw:<2}  {r['name']}")
        n += 1
        if n >= 25:
            break
