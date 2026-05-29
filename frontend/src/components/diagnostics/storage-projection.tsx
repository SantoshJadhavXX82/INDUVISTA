/**
 * Phase 22 — site-wide storage projection card.
 *
 * Calls GET /api/diagnostics/storage-projection and shows:
 *   - total projected rows/day, MB/day, GB/yr vs the every_sample baseline
 *   - overall reduction %
 *   - per-protocol split + per-device breakdown
 *   - the noisiest tags (highest projected write rate) — click to tune
 *
 * Read-only; the math is grounded in the measured bytes/row of the live
 * hypertable, so it reflects real compression, not a guess.
 */
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { api } from "@/lib/api";
import { SectionCard } from "@/components/ui/section-card";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";

type StorageTagRow = {
  tag_id: number;
  tag_name: string;
  device_name: string;
  protocol: string;
  log_mode: string;
  log_enabled: boolean;
  scan_interval_ms: number;
  rows_per_day_every: number;
  rows_per_day_projected: number;
  bytes_per_day_projected: number;
};
type StorageDeviceRow = {
  device_name: string;
  protocol: string;
  tag_count: number;
  rows_per_day_projected: number;
  bytes_per_day_projected: number;
};
type StorageProjection = {
  measured_bytes_per_row: number;
  tag_values_total_bytes: number | null;
  db_total_bytes: number | null;
  enabled_tag_count: number;
  rows_per_day_projected: number;
  rows_per_day_every_sample: number;
  reduction_pct: number;
  bytes_per_day_projected: number;
  bytes_per_year_projected: number;
  bytes_per_year_every_sample: number;
  by_device: StorageDeviceRow[];
  by_protocol: Record<string, number>;
  noisiest: StorageTagRow[];
  note: string;
};

function fmtBytes(b: number | null): string {
  if (b == null) return "—";
  if (b < 1024) return `${b} B`;
  if (b < 1024 ** 2) return `${(b / 1024).toFixed(0)} KB`;
  if (b < 1024 ** 3) return `${(b / 1024 ** 2).toFixed(1)} MB`;
  if (b < 1024 ** 4) return `${(b / 1024 ** 3).toFixed(2)} GB`;
  return `${(b / 1024 ** 4).toFixed(2)} TB`;
}
function fmtNum(n: number): string {
  return Math.round(n).toLocaleString();
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg border p-3" style={{ borderColor: "var(--border)" }}>
      <div className="text-xs uppercase tracking-wider" style={{ color: "var(--text-secondary)" }}>{label}</div>
      <div className="text-lg font-semibold mt-0.5" style={{ color: "var(--text-primary)" }}>{value}</div>
      {hint && <div className="text-xs mt-0.5" style={{ color: "var(--text-secondary)" }}>{hint}</div>}
    </div>
  );
}

export function StorageProjectionCard() {
  const q = useQuery({
    queryKey: ["storage-projection"],
    queryFn: () => api.get<StorageProjection>("/diagnostics/storage-projection"),
    refetchInterval: 60_000,
  });

  if (q.isLoading) {
    return <SectionCard><div className="text-sm py-2" style={{ color: "var(--text-secondary)" }}>Computing storage projection…</div></SectionCard>;
  }
  if (q.isError || !q.data) {
    return <SectionCard><div className="text-sm py-2" style={{ color: "var(--ios-red)" }}>Could not load storage projection.</div></SectionCard>;
  }
  const d = q.data;

  return (
    <SectionCard>
      <div className="space-y-4">
        <div>
          <h3 className="text-base font-semibold" style={{ color: "var(--text-primary)" }}>Storage projection</h3>
          <p className="text-xs mt-0.5" style={{ color: "var(--text-secondary)" }}>
            Across {d.enabled_tag_count} enabled tags · {d.measured_bytes_per_row.toFixed(0)} bytes/row measured · current DB {fmtBytes(d.db_total_bytes)} (history {fmtBytes(d.tag_values_total_bytes)})
          </p>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <Stat label="Projected / day" value={`${fmtNum(d.rows_per_day_projected)} rows`} hint={fmtBytes(d.bytes_per_day_projected)} />
          <Stat label="Projected / year" value={fmtBytes(d.bytes_per_year_projected)} />
          <Stat label="If every-sample / yr" value={fmtBytes(d.bytes_per_year_every_sample)} hint="baseline (no on_change)" />
          <Stat
            label="Reduction"
            value={`${d.reduction_pct.toFixed(0)}%`}
            hint={d.reduction_pct > 0 ? "vs every-sample" : "all every-sample"}
          />
        </div>

        {Object.keys(d.by_protocol).length > 0 && (
          <div className="text-xs" style={{ color: "var(--text-secondary)" }}>
            By protocol (rows/day):{" "}
            {Object.entries(d.by_protocol).map(([p, v], i) => (
              <span key={p}>{i > 0 ? " · " : ""}<strong style={{ color: "var(--text-primary)" }}>{p}</strong> {fmtNum(v)}</span>
            ))}
          </div>
        )}

        {d.noisiest.length > 0 && (
          <div>
            <div className="text-sm font-medium mb-1" style={{ color: "var(--text-primary)" }}>Noisiest tags (highest write rate)</div>
            <div className="rounded-lg border overflow-hidden" style={{ borderColor: "var(--border)" }}>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Tag</TableHead>
                    <TableHead>Device</TableHead>
                    <TableHead>Mode</TableHead>
                    <TableHead className="text-right">Rows/day</TableHead>
                    <TableHead className="text-right">MB/day</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {d.noisiest.map((t) => (
                    <TableRow key={t.tag_id}>
                      <TableCell className="text-xs">
                        <Link to={`/tags?focus=${t.tag_id}`} className="hover:underline" style={{ color: "var(--ios-blue, #007AFF)" }}>
                          {t.tag_name}
                        </Link>
                      </TableCell>
                      <TableCell className="text-xs">{t.device_name}</TableCell>
                      <TableCell className="text-xs">{t.log_enabled ? t.log_mode : "disabled"}</TableCell>
                      <TableCell className="text-right text-xs tabular-nums">{fmtNum(t.rows_per_day_projected)}</TableCell>
                      <TableCell className="text-right text-xs tabular-nums">{(t.bytes_per_day_projected / 1e6).toFixed(2)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </div>
        )}

        <p className="text-xs" style={{ color: "var(--text-secondary)" }}>{d.note}</p>
      </div>
    </SectionCard>
  );
}
