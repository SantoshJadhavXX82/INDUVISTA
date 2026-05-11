/**
 * Reusable CSV import drawer used by Tag Explorer and Register Blocks.
 *
 * Flow:
 *   1. User picks (or pastes) a CSV
 *   2. Parser shows a preview table + per-row validation status
 *   3. Click "Import N rows" → calls onImport(rows)
 *   4. onImport returns per-row results (success/error) which we render
 *
 * Parser handles double-quoted fields and embedded commas (RFC 4180-ish).
 * No external dependency.
 */
import { useMemo, useState, useRef } from "react";
import { Upload, FileText, Check, AlertCircle, Download } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";

export type ImportRowResult = {
  row: number;
  success: boolean;
  message?: string;
};

export type CsvImportProps = {
  /** Column names (in order) expected in the CSV header. */
  expectedColumns: string[];
  /** Columns that must have a non-empty value on every row. */
  requiredColumns: string[];
  /** Example CSV content for the "Download template" button. */
  templateCsv: string;
  /** Filename for the downloaded template. */
  templateFilename: string;
  /**
   * Called when the user commits the import. Receives the parsed rows as
   * an array of records. Should return per-row results so the UI can
   * show what succeeded and what failed.
   */
  onImport: (rows: Record<string, string>[]) => Promise<ImportRowResult[]>;
};

export function CsvImportContent({
  expectedColumns,
  requiredColumns,
  templateCsv,
  templateFilename,
  onImport,
}: CsvImportProps) {
  const [rawText, setRawText] = useState("");
  const [results, setResults] = useState<ImportRowResult[] | null>(null);
  const [importing, setImporting] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const parsed = useMemo(() => {
    if (!rawText.trim()) return null;
    return parseCsv(rawText);
  }, [rawText]);

  const validation = useMemo(() => {
    if (!parsed) return null;
    return validateParsed(parsed, expectedColumns, requiredColumns);
  }, [parsed, expectedColumns, requiredColumns]);

  function handleFile(file: File) {
    const reader = new FileReader();
    reader.onload = (e) => setRawText(String(e.target?.result ?? ""));
    reader.readAsText(file);
  }

  function downloadTemplate() {
    const blob = new Blob([templateCsv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = templateFilename;
    a.click();
    URL.revokeObjectURL(url);
  }

  async function handleImport() {
    if (!parsed || !validation?.allValid) return;
    setImporting(true);
    try {
      const res = await onImport(parsed.rows);
      setResults(res);
    } finally {
      setImporting(false);
    }
  }

  // After import results come back, show them and offer to import another
  if (results) {
    const successCount = results.filter((r) => r.success).length;
    const failures = results.filter((r) => !r.success);
    return (
      <div className="space-y-4">
        <div className="rounded-md border p-3 bg-secondary/30">
          <h3 className="text-sm font-semibold flex items-center gap-2">
            <Check className="h-4 w-4 text-emerald-700" />
            Import complete
          </h3>
          <p className="text-sm mt-1">
            {successCount} of {results.length} rows imported successfully.
          </p>
        </div>

        {failures.length > 0 && (
          <div className="rounded-md border border-red-200 bg-red-50 p-3 space-y-2">
            <h4 className="text-sm font-medium text-red-800">
              {failures.length} row(s) failed:
            </h4>
            <ul className="text-xs space-y-1 text-red-900 max-h-40 overflow-auto">
              {failures.map((f) => (
                <li key={f.row}>
                  <span className="font-mono">Row {f.row + 1}:</span> {f.message}
                </li>
              ))}
            </ul>
          </div>
        )}

        <Button
          onClick={() => {
            setResults(null);
            setRawText("");
            if (fileInputRef.current) fileInputRef.current.value = "";
          }}
        >
          Import another file
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-sm font-semibold mb-2">1. Get the template</h3>
        <Button variant="outline" size="sm" onClick={downloadTemplate}>
          <Download className="h-4 w-4 mr-1.5" />
          Download CSV template
        </Button>
        <p className="text-xs text-muted-foreground mt-2">
          Expected columns: <code className="bg-secondary px-1 rounded">{expectedColumns.join(", ")}</code>
        </p>
        <p className="text-xs text-muted-foreground mt-1">
          Required: <code className="bg-secondary px-1 rounded">{requiredColumns.join(", ")}</code>
        </p>
      </div>

      <div>
        <h3 className="text-sm font-semibold mb-2">2. Upload your filled CSV</h3>
        <Label htmlFor="csv-file" className="block mb-1">File</Label>
        <input
          ref={fileInputRef}
          id="csv-file"
          type="file"
          accept=".csv,text/csv"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) handleFile(f);
          }}
          className="block w-full text-sm file:mr-3 file:rounded-md file:border file:border-input file:bg-secondary file:px-3 file:py-1.5 file:text-sm file:font-medium hover:file:bg-secondary/80"
        />
        <details className="mt-2">
          <summary className="text-xs text-muted-foreground cursor-pointer">
            Or paste CSV directly…
          </summary>
          <textarea
            value={rawText}
            onChange={(e) => setRawText(e.target.value)}
            placeholder={`${expectedColumns.join(",")}\nrow1col1,row1col2,...`}
            rows={6}
            className="mt-2 w-full text-xs font-mono rounded-md border border-input bg-background p-2"
          />
        </details>
      </div>

      {parsed && validation && (
        <div>
          <h3 className="text-sm font-semibold mb-2">
            3. Preview ({parsed.rows.length} rows)
          </h3>
          {validation.errors.length > 0 ? (
            <div className="rounded-md border border-red-200 bg-red-50 p-3 mb-2 flex gap-2">
              <AlertCircle className="h-4 w-4 mt-0.5 shrink-0 text-red-700" />
              <ul className="text-xs space-y-0.5 text-red-900">
                {validation.errors.slice(0, 5).map((err, i) => (
                  <li key={i}>{err}</li>
                ))}
                {validation.errors.length > 5 && (
                  <li>… and {validation.errors.length - 5} more</li>
                )}
              </ul>
            </div>
          ) : (
            <div className="rounded-md border border-emerald-200 bg-emerald-50 p-2 mb-2 text-xs text-emerald-900 flex gap-2">
              <Check className="h-4 w-4 shrink-0" />
              <span>All rows look valid.</span>
            </div>
          )}

          <div className="border rounded-md max-h-60 overflow-auto">
            <table className="w-full text-xs">
              <thead className="bg-secondary sticky top-0">
                <tr>
                  <th className="px-2 py-1 text-left w-8">#</th>
                  {parsed.headers.map((h) => (
                    <th key={h} className="px-2 py-1 text-left">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {parsed.rows.slice(0, 50).map((row, i) => (
                  <tr key={i} className="border-t">
                    <td className="px-2 py-1 text-muted-foreground tabular-nums">{i + 1}</td>
                    {parsed.headers.map((h) => (
                      <td key={h} className="px-2 py-1 font-mono">{row[h] ?? ""}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
            {parsed.rows.length > 50 && (
              <div className="p-2 text-xs text-muted-foreground text-center">
                Showing first 50 of {parsed.rows.length} rows.
              </div>
            )}
          </div>

          <div className="flex gap-2 mt-3">
            <Button
              onClick={handleImport}
              disabled={!validation.allValid || importing}
            >
              <Upload className="h-4 w-4 mr-1.5" />
              {importing ? "Importing…" : `Import ${parsed.rows.length} rows`}
            </Button>
            <Button variant="outline" onClick={() => { setRawText(""); if (fileInputRef.current) fileInputRef.current.value = ""; }}>
              Clear
            </Button>
          </div>
        </div>
      )}

      {!parsed && (
        <div className="text-xs text-muted-foreground flex gap-2">
          <FileText className="h-3 w-3 mt-0.5" />
          <span>Pick or paste a CSV to see the preview.</span>
        </div>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------
// CSV parser — RFC 4180-ish, handles quoted fields with embedded commas
// --------------------------------------------------------------------------

type Parsed = {
  headers: string[];
  rows: Record<string, string>[];
};

function parseCsv(text: string): Parsed {
  const allLines = parseLines(text);
  if (allLines.length === 0) return { headers: [], rows: [] };
  const headers = allLines[0].map((h) => h.trim());
  const rows: Record<string, string>[] = [];
  for (let i = 1; i < allLines.length; i++) {
    const line = allLines[i];
    if (line.length === 1 && line[0] === "") continue; // skip blank
    const row: Record<string, string> = {};
    headers.forEach((h, j) => {
      row[h] = (line[j] ?? "").trim();
    });
    rows.push(row);
  }
  return { headers, rows };
}

function parseLines(text: string): string[][] {
  const lines: string[][] = [];
  let field = "";
  let row: string[] = [];
  let inQuotes = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inQuotes) {
      if (c === '"' && text[i + 1] === '"') {
        field += '"';
        i++;
      } else if (c === '"') {
        inQuotes = false;
      } else {
        field += c;
      }
    } else {
      if (c === '"') {
        inQuotes = true;
      } else if (c === ",") {
        row.push(field);
        field = "";
      } else if (c === "\n") {
        row.push(field);
        lines.push(row);
        row = [];
        field = "";
      } else if (c === "\r") {
        // skip — \r\n handled by next \n
      } else {
        field += c;
      }
    }
  }
  if (field.length > 0 || row.length > 0) {
    row.push(field);
    lines.push(row);
  }
  return lines;
}

function validateParsed(
  parsed: Parsed,
  expectedColumns: string[],
  requiredColumns: string[],
): { allValid: boolean; errors: string[] } {
  const errors: string[] = [];

  // Header check
  const missingHeaders = expectedColumns.filter((c) => !parsed.headers.includes(c));
  if (missingHeaders.length > 0) {
    errors.push(`Missing header(s): ${missingHeaders.join(", ")}`);
  }

  // Required field check per row
  parsed.rows.forEach((row, i) => {
    for (const c of requiredColumns) {
      if (!row[c] || row[c].trim() === "") {
        errors.push(`Row ${i + 1}: missing required field '${c}'`);
      }
    }
  });

  return { allValid: errors.length === 0 && parsed.rows.length > 0, errors };
}

// --------------------------------------------------------------------------
// CSV export — builds a downloadable file from any row/column shape
// --------------------------------------------------------------------------

export type ExportColumn<T> = {
  /** Header name written to the CSV. Should match the import template's header. */
  header: string;
  /** Function to extract the cell value from each row. Return null/undefined for empty. */
  value: (row: T) => string | number | null | undefined;
};

/**
 * Trigger a browser download of the rows as CSV. Escapes commas, quotes,
 * and newlines per RFC 4180.
 */
export function exportCsv<T>(
  rows: T[],
  columns: ExportColumn<T>[],
  filename: string,
): void {
  const lines: string[] = [];
  lines.push(columns.map((c) => escapeCsvCell(c.header)).join(","));
  for (const row of rows) {
    const cells = columns.map((c) => {
      const v = c.value(row);
      if (v === null || v === undefined) return "";
      return escapeCsvCell(String(v));
    });
    lines.push(cells.join(","));
  }
  const csv = lines.join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function escapeCsvCell(value: string): string {
  if (value.includes(",") || value.includes('"') || value.includes("\n") || value.includes("\r")) {
    return `"${value.replace(/"/g, '""')}"`;
  }
  return value;
}
