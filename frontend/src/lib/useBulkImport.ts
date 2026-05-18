/**
 * Phase 14.11 - React Query hooks for alarm rule bulk import.
 *
 * Three operations:
 *   - dryRunImport: upload a file, parse + validate, return preview
 *                   (does not write to DB)
 *   - commitImport: same upload, dry_run=false, writes OK rows in
 *                   one transaction. Strict mode blocks any commit
 *                   if errors present.
 *   - downloadTemplate: triggers a browser download of the CSV or
 *                       XLSX template
 *
 * URL note: the api wrapper (@/lib/api) doesn't expose multipart
 * upload, so we use raw fetch with a relative /api/... path. Vite's
 * dev server proxies that to the backend; same-origin works in prod.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { ImportSummary } from "@/types/alarmsImport";


async function uploadFile(
  file: File,
  dryRun: boolean,
  strict: boolean,
): Promise<ImportSummary> {
  const fd = new FormData();
  fd.append("file", file);
  const url = `/api/alarms/rules/import?dry_run=${dryRun}&strict=${strict}`;
  const resp = await fetch(url, { method: "POST", body: fd });
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try {
      const body = await resp.json();
      detail = body.detail ?? detail;
    } catch {
      // ignore parse errors
    }
    throw new Error(detail);
  }
  return await resp.json();
}


export function useDryRunImport() {
  return useMutation({
    mutationFn: ({ file, strict }: { file: File; strict: boolean }) =>
      uploadFile(file, true, strict),
  });
}


export function useCommitImport() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ file, strict }: { file: File; strict: boolean }) =>
      uploadFile(file, false, strict),
    onSuccess: () => {
      // Refresh the rules list so the new rows appear immediately.
      qc.invalidateQueries({ queryKey: ["alarms-rules"] });
    },
  });
}


export function downloadTemplate(format: "csv" | "xlsx"): void {
  // Direct navigation triggers the browser's download flow via the
  // Content-Disposition header from the backend.
  const url = `/api/alarms/rules/import/template?format=${format}`;
  window.location.href = url;
}
