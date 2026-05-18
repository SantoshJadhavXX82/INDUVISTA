/**
 * Phase 14.11 - AlarmsBulkImport modal.
 *
 * Mounted from AlarmsRules.tsx via a "Bulk Import" button.
 * Workflow:
 *   1. Operator opens modal, optionally downloads CSV/XLSX template
 *   2. Operator picks a file -> auto dry-run
 *   3. Preview table shows per-row status (ok / error / duplicate)
 *   4. Operator clicks "Import N valid rows" -> commits all OK rows
 *      in one transaction. Strict mode (default ON) blocks the commit
 *      if any row has errors.
 *
 * The dry-run/commit split prevents the operator from accidentally
 * importing a half-broken batch. They see exactly what will happen
 * before pressing the commit button.
 */

import { useState } from "react";
import {
  X, Upload, Download, FileSpreadsheet, AlertCircle, CheckCircle2,
  AlertTriangle, Loader2,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  useDryRunImport, useCommitImport, downloadTemplate,
} from "@/lib/useBulkImport";
import type { ImportSummary, ImportRowResult } from "@/types/alarmsImport";


interface Props {
  open: boolean;
  onClose: () => void;
}


export default function AlarmsBulkImport({ open, onClose }: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<ImportSummary | null>(null);
  const [strict, setStrict] = useState(true);
  const [committed, setCommitted] = useState(false);

  const dryRun = useDryRunImport();
  const commit = useCommitImport();

  if (!open) return null;

  function reset() {
    setFile(null);
    setPreview(null);
    setCommitted(false);
    dryRun.reset();
    commit.reset();
  }

  function handleClose() {
    reset();
    onClose();
  }

  async function handleFileSelect(f: File | null) {
    setFile(f);
    setPreview(null);
    setCommitted(false);
    if (!f) return;
    try {
      const summary = await dryRun.mutateAsync({ file: f, strict });
      setPreview(summary);
    } catch (e) {
      // Error is rendered via dryRun.error below.
    }
  }

  async function handleCommit() {
    if (!file) return;
    try {
      const summary = await commit.mutateAsync({ file, strict });
      setPreview(summary);
      setCommitted(summary.committed);
    } catch (e) {
      // Error is rendered via commit.error below.
    }
  }

  const canCommit = preview !== null
    && !committed
    && preview.ok_count > 0
    && (!strict || preview.error_count === 0);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="w-full max-w-5xl max-h-[90vh] overflow-hidden flex flex-col bg-background rounded-lg shadow-2xl border">
        {/* Header */}
        <div className="flex items-center justify-between border-b p-4">
          <div>
            <h2 className="text-xl font-semibold flex items-center gap-2">
              <FileSpreadsheet className="h-5 w-5" />
              Bulk Import Alarm Rules
            </h2>
            <p className="text-sm text-muted-foreground mt-1">
              Upload a CSV or XLSX file. Each row becomes one alarm rule.
              Strict mode blocks the import if any row has errors.
            </p>
          </div>
          <Button variant="ghost" size="icon" onClick={handleClose}>
            <X className="h-5 w-5" />
          </Button>
        </div>

        {/* Toolbar */}
        <div className="flex items-center gap-2 border-b p-4 flex-wrap">
          <label className="cursor-pointer">
            <input
              type="file"
              accept=".csv,.xlsx"
              onChange={(e) => handleFileSelect(e.target.files?.[0] ?? null)}
              className="hidden"
            />
            <span className="inline-flex items-center gap-2 px-3 py-2 rounded-md border bg-secondary hover:bg-secondary/80 text-sm font-medium">
              <Upload className="h-4 w-4" />
              {file ? `Selected: ${file.name}` : "Choose CSV/XLSX file"}
            </span>
          </label>

          <Button variant="outline" size="sm" onClick={() => downloadTemplate("csv")}>
            <Download className="h-4 w-4 mr-1" />
            Template (CSV)
          </Button>
          <Button variant="outline" size="sm" onClick={() => downloadTemplate("xlsx")}>
            <Download className="h-4 w-4 mr-1" />
            Template (XLSX)
          </Button>

          <label className="ml-auto flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={strict}
              onChange={(e) => setStrict(e.target.checked)}
            />
            Strict mode (refuse import if any row has errors)
          </label>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-4">
          {dryRun.isPending && (
            <div className="flex items-center gap-2 text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Validating file...
            </div>
          )}

          {dryRun.error && (
            <Card className="border-destructive">
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-destructive">
                  <AlertCircle className="h-5 w-5" />
                  Parse failure
                </CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-sm">{(dryRun.error as Error).message}</p>
              </CardContent>
            </Card>
          )}

          {preview && <PreviewSection summary={preview} committed={committed} />}

          {commit.error && (
            <Card className="border-destructive mt-4">
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-destructive">
                  <AlertCircle className="h-5 w-5" />
                  Commit failed
                </CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-sm">{(commit.error as Error).message}</p>
              </CardContent>
            </Card>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between border-t p-4">
          <div className="text-sm text-muted-foreground">
            {preview && !committed && (
              <>
                {preview.ok_count} ready,{" "}
                {preview.error_count} error{preview.error_count === 1 ? "" : "s"},{" "}
                {preview.duplicate_count} duplicate{preview.duplicate_count === 1 ? "" : "s"}
              </>
            )}
            {committed && (
              <span className="text-green-600 font-medium">
                Successfully imported {preview?.ok_count ?? 0} rules
              </span>
            )}
          </div>
          <div className="flex gap-2">
            <Button variant="outline" onClick={handleClose}>
              {committed ? "Close" : "Cancel"}
            </Button>
            <Button
              onClick={handleCommit}
              disabled={!canCommit || commit.isPending}
            >
              {commit.isPending && <Loader2 className="h-4 w-4 mr-1 animate-spin" />}
              {committed
                ? "Done"
                : preview
                  ? `Import ${preview.ok_count} valid row${preview.ok_count === 1 ? "" : "s"}`
                  : "Import"}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}


// ---- Preview table ----

function PreviewSection({
  summary, committed,
}: { summary: ImportSummary; committed: boolean }) {
  if (committed) {
    return (
      <Card className="border-green-500">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-green-600">
            <CheckCircle2 className="h-5 w-5" />
            Import complete
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm">
            {summary.ok_count} alarm rule{summary.ok_count === 1 ? "" : "s"} inserted.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <div>
      <div className="grid grid-cols-4 gap-2 mb-4">
        <SummaryTile label="Total" value={summary.total_rows} />
        <SummaryTile label="Valid" value={summary.ok_count} tone="ok" />
        <SummaryTile label="Errors" value={summary.error_count} tone="error" />
        <SummaryTile label="Duplicates" value={summary.duplicate_count} tone="dup" />
      </div>

      <div className="border rounded-md overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-muted/50">
            <tr>
              <th className="text-left p-2 w-12">Row</th>
              <th className="text-left p-2 w-24">Status</th>
              <th className="text-left p-2">Tag</th>
              <th className="text-left p-2 w-24">Type</th>
              <th className="text-left p-2 w-24">Severity</th>
              <th className="text-left p-2 w-24">Threshold</th>
              <th className="text-left p-2">Notes</th>
            </tr>
          </thead>
          <tbody>
            {summary.rows.map((r) => <RowLine key={r.row_number} row={r} />)}
          </tbody>
        </table>
      </div>
    </div>
  );
}


function SummaryTile({
  label, value, tone,
}: { label: string; value: number; tone?: "ok" | "error" | "dup" }) {
  const colorClass = tone === "ok"    ? "text-green-600"
                   : tone === "error" ? "text-red-600"
                   : tone === "dup"   ? "text-yellow-600"
                   : "text-foreground";
  return (
    <div className="border rounded-md p-3 bg-card">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={`text-2xl font-semibold ${colorClass}`}>{value}</div>
    </div>
  );
}


function RowLine({ row }: { row: ImportRowResult }) {
  const badge = row.status === "ok"        ? <Badge className="bg-green-600">OK</Badge>
              : row.status === "duplicate" ? <Badge className="bg-yellow-600">DUP</Badge>
              : <Badge variant="destructive">ERR</Badge>;

  const notes = row.errors.length > 0
    ? <span className="text-red-600">{row.errors.join("; ")}</span>
    : row.warnings.length > 0
      ? <span className="text-yellow-600 flex items-start gap-1">
          <AlertTriangle className="h-3 w-3 mt-0.5 shrink-0" />
          {row.warnings.join("; ")}
        </span>
      : <span className="text-muted-foreground">Ready to insert</span>;

  return (
    <tr className="border-t hover:bg-muted/30">
      <td className="p-2 text-muted-foreground">{row.row_number}</td>
      <td className="p-2">{badge}</td>
      <td className="p-2 font-mono text-xs">{row.tag_name || "—"}</td>
      <td className="p-2">{row.rule_type || "—"}</td>
      <td className="p-2">{row.severity || "—"}</td>
      <td className="p-2 font-mono">{row.threshold ?? "—"}</td>
      <td className="p-2">{notes}</td>
    </tr>
  );
}
