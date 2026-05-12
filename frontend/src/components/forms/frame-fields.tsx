/**
 * Phase 7 Batch 2 (extension) — Frame field breakdown.
 *
 * Parses a captured frame's hex bytes into labeled field cards:
 *   MBAP header: TID · PROTO · LEN · UID
 *   PDU:        FC · ADDR · COUNT · BC ...
 *   Data:       REGISTER[0..N], BYTE[0..N], EXC code
 *
 * Each field renders as a small colored card showing label, raw hex,
 * and the decoded value. The colors group related fields (MBAP, PDU,
 * data, exception) so the structure of a frame is visible at a glance.
 */
import { cn } from "@/lib/utils";
import type { Frame } from "@/types/api";

type Category = "mbap" | "pdu" | "data" | "exception";

type Field = {
  label: string;
  hex: string;       // pretty-spaced ("41 3D")
  decoded: string;
  category: Category;
};

const FC_NAMES: Record<number, string> = {
  1: "Read Coils",
  2: "Read Discrete Inputs",
  3: "Read Holding Registers",
  4: "Read Input Registers",
  5: "Write Single Coil",
  6: "Write Single Register",
  15: "Write Multiple Coils",
  16: "Write Multiple Registers",
};

const EXCEPTION_NAMES: Record<number, string> = {
  0x01: "Illegal function",
  0x02: "Illegal data address",
  0x03: "Illegal data value",
  0x04: "Slave device failure",
  0x05: "Acknowledge",
  0x06: "Slave device busy",
  0x08: "Memory parity error",
  0x0A: "Gateway path unavailable",
  0x0B: "Gateway target failed to respond",
};

function hexToBytes(s: string): number[] {
  return s
    .trim()
    .split(/\s+/)
    .map((b) => parseInt(b, 16))
    .filter((n) => !isNaN(n));
}

function hexSlice(bytes: number[], start: number, len: number): string {
  return bytes
    .slice(start, start + len)
    .map((b) => b.toString(16).toUpperCase().padStart(2, "0"))
    .join(" ");
}

function u16(bytes: number[], offset: number): number {
  return (bytes[offset] << 8) | bytes[offset + 1];
}

function u16hex(v: number): string {
  return "0x" + v.toString(16).toUpperCase().padStart(4, "0");
}

/**
 * Parse a Modbus TCP frame (MBAP + PDU) into field-level breakdown.
 * Returns an empty array if the input is too short to be a valid frame.
 */
export function parseFrame(frame: Frame): Field[] {
  const bytes = hexToBytes(frame.hex_bytes);
  if (bytes.length < 7) return [];

  const fields: Field[] = [
    {
      label: "TID",
      hex: hexSlice(bytes, 0, 2),
      decoded: String(u16(bytes, 0)),
      category: "mbap",
    },
    {
      label: "PROTO",
      hex: hexSlice(bytes, 2, 2),
      decoded: u16(bytes, 2) === 0 ? "Modbus" : `0x${u16(bytes, 2).toString(16)}`,
      category: "mbap",
    },
    {
      label: "LEN",
      hex: hexSlice(bytes, 4, 2),
      decoded: `${u16(bytes, 4)} bytes follow`,
      category: "mbap",
    },
    {
      label: "UID",
      hex: hexSlice(bytes, 6, 1),
      decoded: String(bytes[6]),
      category: "mbap",
    },
  ];

  if (bytes.length < 8) return fields;
  const rawFc = bytes[7];
  const isException = (rawFc & 0x80) !== 0;
  const fc = rawFc & 0x7F;

  // FC byte
  fields.push({
    label: "FC",
    hex: hexSlice(bytes, 7, 1),
    decoded: isException
      ? `0x${rawFc.toString(16).toUpperCase()} — Exception`
      : `${fc} — ${FC_NAMES[fc] ?? "Unknown"}`,
    category: isException ? "exception" : "pdu",
  });

  if (isException && bytes.length >= 9) {
    fields.push({
      label: "EXC",
      hex: hexSlice(bytes, 8, 1),
      decoded: `0x${bytes[8].toString(16).padStart(2, "0")} — ${EXCEPTION_NAMES[bytes[8]] ?? "Unknown"}`,
      category: "exception",
    });
    return fields;
  }

  // PDU body — varies by FC and direction
  if (frame.direction === "tx") {
    // Read requests: FC1-4 = ADDR(2) + COUNT(2)
    // Write single: FC5/6 = ADDR(2) + VALUE(2)
    // Write multi: FC15/16 = ADDR(2) + COUNT(2) + BC(1) + DATA
    if ([1, 2, 3, 4].includes(fc) && bytes.length >= 12) {
      const addr = u16(bytes, 8);
      const count = u16(bytes, 10);
      fields.push({
        label: "ADDR",
        hex: hexSlice(bytes, 8, 2),
        decoded: `${addr} (${u16hex(addr)})`,
        category: "pdu",
      });
      fields.push({
        label: "COUNT",
        hex: hexSlice(bytes, 10, 2),
        decoded: String(count),
        category: "pdu",
      });
    } else if (fc === 5 && bytes.length >= 12) {
      const addr = u16(bytes, 8);
      const val = u16(bytes, 10);
      fields.push({
        label: "ADDR",
        hex: hexSlice(bytes, 8, 2),
        decoded: `${addr} (${u16hex(addr)})`,
        category: "pdu",
      });
      fields.push({
        label: "VALUE",
        hex: hexSlice(bytes, 10, 2),
        decoded: val === 0xFF00 ? "ON" : val === 0x0000 ? "OFF" : `?(${u16hex(val)})`,
        category: "data",
      });
    } else if (fc === 6 && bytes.length >= 12) {
      const addr = u16(bytes, 8);
      const val = u16(bytes, 10);
      fields.push({
        label: "ADDR",
        hex: hexSlice(bytes, 8, 2),
        decoded: `${addr} (${u16hex(addr)})`,
        category: "pdu",
      });
      fields.push({
        label: "VALUE",
        hex: hexSlice(bytes, 10, 2),
        decoded: `${val} (${u16hex(val)})`,
        category: "data",
      });
    } else if ([15, 16].includes(fc) && bytes.length >= 13) {
      fields.push({
        label: "ADDR",
        hex: hexSlice(bytes, 8, 2),
        decoded: String(u16(bytes, 8)),
        category: "pdu",
      });
      fields.push({
        label: "COUNT",
        hex: hexSlice(bytes, 10, 2),
        decoded: String(u16(bytes, 10)),
        category: "pdu",
      });
      const bc = bytes[12];
      fields.push({
        label: "BC",
        hex: hexSlice(bytes, 12, 1),
        decoded: `${bc} bytes`,
        category: "pdu",
      });
      pushDataFields(fields, bytes, 13, bc, fc === 16 ? "reg" : "bits");
    }
  } else {
    // RX (response)
    if ([3, 4].includes(fc) && bytes.length >= 9) {
      const bc = bytes[8];
      fields.push({
        label: "BC",
        hex: hexSlice(bytes, 8, 1),
        decoded: `${bc} bytes`,
        category: "pdu",
      });
      pushDataFields(fields, bytes, 9, bc, "reg");
    } else if ([1, 2].includes(fc) && bytes.length >= 9) {
      const bc = bytes[8];
      fields.push({
        label: "BC",
        hex: hexSlice(bytes, 8, 1),
        decoded: `${bc} bytes`,
        category: "pdu",
      });
      pushDataFields(fields, bytes, 9, bc, "bits");
    } else if ([5, 6].includes(fc) && bytes.length >= 12) {
      fields.push({
        label: "ADDR",
        hex: hexSlice(bytes, 8, 2),
        decoded: String(u16(bytes, 8)),
        category: "pdu",
      });
      fields.push({
        label: "VALUE",
        hex: hexSlice(bytes, 10, 2),
        decoded: String(u16(bytes, 10)),
        category: "data",
      });
    } else if ([15, 16].includes(fc) && bytes.length >= 12) {
      fields.push({
        label: "ADDR",
        hex: hexSlice(bytes, 8, 2),
        decoded: String(u16(bytes, 8)),
        category: "pdu",
      });
      fields.push({
        label: "COUNT",
        hex: hexSlice(bytes, 10, 2),
        decoded: String(u16(bytes, 10)),
        category: "pdu",
      });
    }
  }

  return fields;
}

function pushDataFields(
  fields: Field[],
  bytes: number[],
  offset: number,
  byteCount: number,
  kind: "reg" | "bits",
): void {
  if (kind === "reg") {
    const numRegs = Math.floor(byteCount / 2);
    for (let i = 0; i < numRegs; i++) {
      const o = offset + i * 2;
      if (o + 1 >= bytes.length) break;
      const val = u16(bytes, o);
      fields.push({
        label: `REG[${i}]`,
        hex: hexSlice(bytes, o, 2),
        decoded: `${val} (${u16hex(val)})`,
        category: "data",
      });
    }
  } else {
    for (let i = 0; i < byteCount; i++) {
      const o = offset + i;
      if (o >= bytes.length) break;
      fields.push({
        label: `BYTE[${i}]`,
        hex: hexSlice(bytes, o, 1),
        decoded: `0b${bytes[o].toString(2).padStart(8, "0")}`,
        category: "data",
      });
    }
  }
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

export function FrameFields({ frame }: { frame: Frame }) {
  const fields = parseFrame(frame);
  if (fields.length === 0) {
    return (
      <p className="text-[11px] text-muted-foreground italic">
        Frame too short to decode ({frame.byte_count} bytes).
      </p>
    );
  }

  // Highlight key counts in the legend
  const dataCount = fields.filter((f) => f.category === "data").length;

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-3 text-[10px] text-muted-foreground">
        <LegendChip color="mbap" label="MBAP" />
        <LegendChip color="pdu" label="PDU" />
        {dataCount > 0 && <LegendChip color="data" label={`Data (${dataCount})`} />}
        {fields.some((f) => f.category === "exception") && (
          <LegendChip color="exception" label="Exception" />
        )}
      </div>
      <div className="flex flex-wrap gap-1.5">
        {fields.map((f, i) => (
          <FieldCard key={i} field={f} />
        ))}
      </div>
    </div>
  );
}

function FieldCard({ field }: { field: Field }) {
  return (
    <div
      className={cn(
        "rounded border px-1.5 py-1 min-w-[68px] font-mono",
        "bg-background hover:shadow-sm transition-shadow",
        categoryClass(field.category),
      )}
    >
      <div className="text-[9px] font-bold uppercase tracking-wider opacity-80">
        {field.label}
      </div>
      <div className="text-[10px] font-medium">{field.hex}</div>
      <div className="text-[9px] text-muted-foreground truncate" title={field.decoded}>
        {field.decoded}
      </div>
    </div>
  );
}

function LegendChip({ color, label }: { color: Category; label: string }) {
  return (
    <span className="inline-flex items-center gap-1">
      <span className={cn("inline-block h-2 w-2 rounded-sm border", categoryClass(color))} />
      <span>{label}</span>
    </span>
  );
}

function categoryClass(c: Category): string {
  switch (c) {
    case "mbap":
      return "border-sky-400 bg-sky-50";
    case "pdu":
      return "border-amber-400 bg-amber-50";
    case "data":
      return "border-emerald-400 bg-emerald-50";
    case "exception":
      return "border-rose-400 bg-rose-50";
  }
}
