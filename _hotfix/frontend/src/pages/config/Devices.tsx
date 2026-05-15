/**
 * Devices sub-page — list + drawer create/edit/delete.
 *
 * A device is a single Modbus endpoint (host:port, unit_id) within a
 * channel. Tags belong to devices via register_blocks. Editing host/port
 * triggers the Phase 3.5 worker hot-reload within ~10 seconds.
 */
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2, AlertCircle } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Drawer } from "@/components/ui/drawer";
import { HelpTip } from "@/components/ui/help-tip";
import { help } from "@/lib/help-text";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

type Device = {
  id: number;
  name: string;
  description: string | null;
  channel_id: number;
  channel_name: string;
  host: string;
  port: number;
  unit_id: number;
  duty_role: string;
  redundant_device_id: number | null;
  duty_status_tag_id: number | null;
  manual_override: boolean;
  stale_after_sec: number;
  scan_interval_ms: number;
  enabled: boolean;
  // Phase 8.5
  request_timeout_ms: number;
  retry_count: number;
  reconnect_initial_ms: number;
  reconnect_max_ms: number;
};

type Channel = { id: number; name: string };

export default function Devices() {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<Device | "new" | null>(null);

  const devices = useQuery({
    queryKey: ["devices"],
    queryFn: () => api.get<Device[]>("/devices"),
  });

  const channels = useQuery({
    queryKey: ["channels"],
    queryFn: () => api.get<Channel[]>("/channels"),
    staleTime: 60_000,
  });

  return (
    <div className="space-y-3">
      <div className="flex justify-between items-center">
        <span className="text-sm text-muted-foreground">
          {devices.data ? `${devices.data.length} devices` : "Loading…"}
        </span>
        <Button onClick={() => setEditing("new")} size="sm">
          <Plus className="h-4 w-4 mr-1.5" />
          Add device
        </Button>
      </div>

      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Network</TableHead>
                <TableHead>Host</TableHead>
                <TableHead className="text-right">Port</TableHead>
                <TableHead className="text-right">Unit ID</TableHead>
                <TableHead>Duty</TableHead>
                <TableHead>Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {devices.data?.map((d) => (
                <TableRow
                  key={d.id}
                  onClick={() => setEditing(d)}
                  className="cursor-pointer"
                >
                  <TableCell className="font-medium">{d.name}</TableCell>
                  <TableCell className="text-xs">{d.channel_name}</TableCell>
                  <TableCell className="text-xs font-mono">{d.host}</TableCell>
                  <TableCell className="text-right tabular-nums text-xs">{d.port}</TableCell>
                  <TableCell className="text-right tabular-nums text-xs">{d.unit_id}</TableCell>
                  <TableCell className="text-xs">{d.duty_role}</TableCell>
                  <TableCell>
                    <Badge variant={d.enabled ? "success" : "secondary"} className="text-xs">
                      {d.enabled ? "enabled" : "disabled"}
                    </Badge>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Drawer
        open={editing !== null}
        onClose={() => setEditing(null)}
        title={editing === "new" ? "New device" : `Device: ${editing && editing !== "new" ? editing.name : ""}`}
        size="lg"
      >
        {editing !== null && (
          <DeviceForm
            device={editing === "new" ? null : editing}
            channels={channels.data ?? []}
            allDevices={devices.data ?? []}
            onDone={() => {
              queryClient.invalidateQueries({ queryKey: ["devices"] });
              setEditing(null);
            }}
          />
        )}
      </Drawer>
    </div>
  );
}

// --------------------------------------------------------------------------

type FormState = {
  name: string;
  description: string;
  channel_id: string;
  host: string;
  port: string;
  unit_id: string;
  duty_role: string;
  partner_device_id: string;   // Phase 12 — empty string = no partner
  duty_status_tag_id: string;  // Phase 12.2 — empty string = manual mode
  stale_after_sec: string;
  scan_interval_ms: string;
  enabled: boolean;
  // Phase 8.5
  request_timeout_ms: string;
  retry_count: string;
  reconnect_initial_ms: string;
  reconnect_max_ms: string;
};

function DeviceForm({
  device,
  channels,
  allDevices,
  onDone,
}: {
  device: Device | null;
  channels: Channel[];
  allDevices: Device[];
  onDone: () => void;
}) {
  const isNew = device === null;
  const queryClient = useQueryClient();
  const [form, setForm] = useState<FormState>({
    name: device?.name ?? "",
    description: device?.description ?? "",
    channel_id: device ? String(device.channel_id) : (channels[0] ? String(channels[0].id) : ""),
    host: device?.host ?? "",
    port: device ? String(device.port) : "502",
    unit_id: device ? String(device.unit_id) : "1",
    duty_role: device?.duty_role ?? "none",
    partner_device_id: device?.redundant_device_id ? String(device.redundant_device_id) : "",
    duty_status_tag_id: device?.duty_status_tag_id ? String(device.duty_status_tag_id) : "",
    stale_after_sec: device ? String(device.stale_after_sec) : "30",
    scan_interval_ms: device ? String(device.scan_interval_ms) : "1000",
    enabled: device?.enabled ?? true,
    // Phase 8.5 — defaults match migration 0007's DEFAULT clauses
    request_timeout_ms: device ? String(device.request_timeout_ms) : "3000",
    retry_count: device ? String(device.retry_count) : "1",
    reconnect_initial_ms: device ? String(device.reconnect_initial_ms) : "1000",
    reconnect_max_ms: device ? String(device.reconnect_max_ms) : "30000",
  });
  const [error, setError] = useState<string | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState("");

  // Phase 12 — eligible partner candidates: any other device that's
  // either unpaired OR already paired with us. Filtering out devices
  // already paired with someone else prevents an accidental three-way.
  const partnerCandidates = useMemo(
    () =>
      allDevices.filter(
        (d) =>
          (!device || d.id !== device.id) &&
          (d.redundant_device_id === null || d.redundant_device_id === device?.id),
      ),
    [allDevices, device],
  );

  // Phase 12.2 — tags on THIS device, for the duty-status-tag picker.
  // Filter to boolean-ish data types (bool, int/uint up to 32-bit) since
  // a duty/standby indicator is by nature a small state value.
  const deviceTags = useQuery({
    queryKey: ["tags", "device", device?.id],
    queryFn: () => api.get<Array<{
      id: number;
      name: string;
      data_type: string;
    }>>(`/tags?device_id=${device?.id}`),
    enabled: !!device,
    staleTime: 60_000,
  });
  const dutyStatusCandidates = useMemo(
    () =>
      (deviceTags.data ?? []).filter((t) =>
        ["bool", "int16", "uint16", "int32", "uint32"].includes(t.data_type),
      ),
    [deviceTags.data],
  );

  const save = useMutation({
    mutationFn: async () => {
      // Phase 8.5 fields go in both create and update paths
      const hardening = {
        request_timeout_ms: parseInt(form.request_timeout_ms, 10),
        retry_count: parseInt(form.retry_count, 10),
        reconnect_initial_ms: parseInt(form.reconnect_initial_ms, 10),
        reconnect_max_ms: parseInt(form.reconnect_max_ms, 10),
      };

      // ----- Validate duty/standby pairing intent -----
      // The DB constraint ck_devices_duty_role_consistency requires:
      //   duty_role='none' ⇔ partner_device_id is empty
      // We fail fast in the client with a clearer message than the
      // raw SQL constraint name.
      if (form.duty_role !== "none" && !form.partner_device_id) {
        throw new Error("partner device required when duty role is duty or standby");
      }
      if (form.duty_role === "none" && form.partner_device_id) {
        throw new Error("clear the partner device when duty role is 'none'");
      }

      // ----- CREATE path: device is new, so it can't have a partner yet -----
      // The /pair endpoint requires both devices to exist. So a brand-new
      // device must be created with duty_role='none' first, then paired
      // in a follow-up call. We bundle both into this mutation so the
      // user sees a single Save action.
      if (isNew) {
        const created = await api.post<Device>("/devices", {
          name: form.name,
          description: form.description || null,
          channel_id: parseInt(form.channel_id, 10),
          host: form.host,
          port: parseInt(form.port, 10),
          unit_id: parseInt(form.unit_id, 10),
          duty_role: "none",                  // always create unpaired
          stale_after_sec: parseInt(form.stale_after_sec, 10),
          scan_interval_ms: parseInt(form.scan_interval_ms, 10),
          enabled: form.enabled,
          ...hardening,
        });
        if (form.duty_role !== "none" && form.partner_device_id) {
          return api.post<Device>(`/devices/${created.id}/pair`, {
            partner_device_id: parseInt(form.partner_device_id, 10),
            this_role: form.duty_role,
          });
        }
        return created;
      }

      // ----- UPDATE path -----
      const currentlyPaired = (device.duty_role === "duty" || device.duty_role === "standby");
      const willBePaired = (form.duty_role !== "none");

      // Decide whether to call /pair, /unpair, or just PATCH.
      // (a) Pairing changes (none↔duty/standby, or partner swap, or role swap):
      //     go through /pair or /unpair so both sides update atomically.
      // (b) Other field changes go through PATCH.
      const pairingChanged =
        device.duty_role !== form.duty_role ||
        String(device.redundant_device_id ?? "") !== form.partner_device_id;

      // PATCH the non-pairing fields first.
      const patched = await api.patch<Device>(`/devices/${device.id}`, {
        name: form.name,
        description: form.description || null,
        host: form.host,
        port: parseInt(form.port, 10),
        unit_id: parseInt(form.unit_id, 10),
        stale_after_sec: parseInt(form.stale_after_sec, 10),
        scan_interval_ms: parseInt(form.scan_interval_ms, 10),
        enabled: form.enabled,
        duty_status_tag_id: form.duty_status_tag_id
          ? parseInt(form.duty_status_tag_id, 10)
          : null,
        ...hardening,
      });

      if (!pairingChanged) return patched;

      if (willBePaired) {
        return api.post<Device>(`/devices/${device.id}/pair`, {
          partner_device_id: parseInt(form.partner_device_id, 10),
          this_role: form.duty_role,
        });
      }
      if (currentlyPaired) {
        return api.post<Device>(`/devices/${device.id}/unpair`, {});
      }
      return patched;
    },
    onSuccess: onDone,
    onError: (e: Error) => setError(e instanceof ApiError ? e.detail : e.message),
  });

  const remove = useMutation({
    mutationFn: () => api.delete(`/devices/${device!.id}`),
    onSuccess: onDone,
    onError: (e: Error) => setError(e instanceof ApiError ? e.detail : e.message),
  });

  // Phase 12.4 — manual duty/standby swap for testing and operator
  // control. Only shown for paired devices. The system already exposes
  // POST /devices/{id}/swap-duty; this UI surfaces it inline so testers
  // and operators don't need to drop to curl.
  const [swapNotes, setSwapNotes] = useState("");
  const [swapSuccessAt, setSwapSuccessAt] = useState<Date | null>(null);

  const dutyHistory = useQuery({
    queryKey: ["devices", device?.id, "duty-history"],
    queryFn: () => api.get<Array<{
      id: number;
      device_name: string;
      paired_device_name: string;
      switched_at: string;
      reason: string;
      notes: string | null;
    }>>(`/devices/${device?.id}/duty-history?limit=5`),
    enabled: !!device && device.duty_role !== "none",
    staleTime: 10_000,
  });

  const swap = useMutation({
    mutationFn: () => api.post(`/devices/${device!.id}/swap-duty`, {
      reason: "manual",
      notes: swapNotes || null,
    }),
    onSuccess: () => {
      setError(null);
      setSwapSuccessAt(new Date());
      setSwapNotes("");
      // Refresh both the device list (role badges) and history
      queryClient.invalidateQueries({ queryKey: ["devices"] });
      queryClient.invalidateQueries({ queryKey: ["devices", device?.id, "duty-history"] });
      queryClient.invalidateQueries({ queryKey: ["pair-tags", "live"] });
    },
    onError: (e: Error) => setError(e instanceof ApiError ? e.detail : e.message),
  });

  // Phase 12.5 — toggle manual override mode. While ON, worker reconciliation
  // is suspended for this pair and manual swaps stick. While OFF, the device's
  // self-reported value drives duty_role within ~5s of any change.
  const toggleOverride = useMutation({
    mutationFn: (enable: boolean) =>
      api.post(`/devices/${device!.id}/set-pair-override`, { enable }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["devices"] });
      queryClient.invalidateQueries({ queryKey: ["pair-tags", "live"] });
    },
    onError: (e: Error) => setError(e instanceof ApiError ? e.detail : e.message),
  });

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        setError(null);
        save.mutate();
      }}
      className="space-y-4"
    >
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label htmlFor="name">
            Name <HelpTip entry={help.device.name} />
          </Label>
          <Input
            id="name"
            required
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="channel">
            Network <HelpTip entry={help.device.channel} />
            {!isNew && <span className="normal-case text-muted-foreground"> (immutable)</span>}
          </Label>
          <select
            id="channel"
            disabled={!isNew}
            value={form.channel_id}
            onChange={(e) => setForm({ ...form, channel_id: e.target.value })}
            className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm disabled:opacity-50"
          >
            {channels.map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </select>
        </div>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="description">
          Description <HelpTip entry={help.device.description} />
        </Label>
        <Input
          id="description"
          value={form.description}
          onChange={(e) => setForm({ ...form, description: e.target.value })}
        />
      </div>

      <div className="grid grid-cols-3 gap-3">
        <div className="col-span-2 space-y-1.5">
          <Label htmlFor="host">
            Host <HelpTip entry={help.channel.host} />
          </Label>
          <Input
            id="host"
            required
            value={form.host}
            onChange={(e) => setForm({ ...form, host: e.target.value })}
            placeholder="192.168.1.10 or simulator service name"
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="port">
            Port <HelpTip entry={help.channel.port} />
          </Label>
          <Input
            id="port"
            type="number"
            required
            value={form.port}
            onChange={(e) => setForm({ ...form, port: e.target.value })}
          />
        </div>
      </div>

      <div className="grid grid-cols-3 gap-3">
        <div className="space-y-1.5">
          <Label htmlFor="unit_id">
            Unit ID <HelpTip entry={help.device.unit_id} />
          </Label>
          <Input
            id="unit_id"
            type="number"
            required
            value={form.unit_id}
            onChange={(e) => setForm({ ...form, unit_id: e.target.value })}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="duty_role">Duty role</Label>
          <select
            id="duty_role"
            value={form.duty_role}
            onChange={(e) => {
              // Switching to 'none' clears the partner; otherwise keep
              // whatever the user had selected.
              const next = e.target.value;
              setForm({
                ...form,
                duty_role: next,
                partner_device_id: next === "none" ? "" : form.partner_device_id,
              });
            }}
            className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
          >
            <option value="none">none (standalone)</option>
            <option value="duty">duty (active in HA pair)</option>
            <option value="standby">standby (passive in HA pair)</option>
          </select>
        </div>
        {form.duty_role !== "none" && (
          <div className="space-y-1.5 col-span-2">
            <Label htmlFor="partner_device_id">
              Partner device
              <span className="normal-case text-muted-foreground"> (required for duty/standby)</span>
            </Label>
            <select
              id="partner_device_id"
              value={form.partner_device_id}
              onChange={(e) => setForm({ ...form, partner_device_id: e.target.value })}
              className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
            >
              <option value="">— select partner —</option>
              {partnerCandidates.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name} ({d.channel_name})
                  {d.duty_role !== "none" ? ` — currently ${d.duty_role}` : ""}
                </option>
              ))}
            </select>
            <p className="text-[11px] text-muted-foreground leading-relaxed">
              Saving will set the partner to the opposite role and link them
              both. Either device can later trigger a swap via the duty
              history view or the API.
            </p>
          </div>
        )}
        {form.duty_role !== "none" && device && (
          <div className="space-y-1.5 col-span-2">
            <Label htmlFor="duty_status_tag_id">
              Duty status tag
              <span className="normal-case text-muted-foreground"> (optional — device-led failover)</span>
            </Label>
            <select
              id="duty_status_tag_id"
              value={form.duty_status_tag_id}
              onChange={(e) => setForm({ ...form, duty_status_tag_id: e.target.value })}
              className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
            >
              <option value="">— manual swap only (no auto-reconcile) —</option>
              {dutyStatusCandidates.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name} ({t.data_type})
                </option>
              ))}
            </select>
            <p className="text-[11px] text-muted-foreground leading-relaxed">
              The tag whose value reports this device's self-assessed duty/standby
              role. The worker compares its reading against the system-wide{" "}
              <a href="/global/duty-standby-values" className="underline">
                Duty/Standby Values
              </a>
              {" "}and reconciles the stored role automatically every cycle.
              Only bool / int / uint tags are listed.
            </p>
          </div>
        )}
        <div className="space-y-1.5">
          <Label htmlFor="enabled">
            Status <HelpTip entry={help.device.enabled} />
          </Label>
          <label className="flex items-center gap-2 h-9 text-sm">
            <input
              id="enabled"
              type="checkbox"
              checked={form.enabled}
              onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
              className="h-4 w-4"
            />
            <span className="text-muted-foreground">
              {form.enabled ? "polled" : "skipped"}
            </span>
          </label>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label htmlFor="stale_after_sec">Stale after (sec)</Label>
          <Input
            id="stale_after_sec"
            type="number"
            required
            value={form.stale_after_sec}
            onChange={(e) => setForm({ ...form, stale_after_sec: e.target.value })}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="scan_interval_ms">
            Scan interval (ms) <HelpTip entry={help.device.scan_interval_ms} />
          </Label>
          <Input
            id="scan_interval_ms"
            type="number"
            required
            value={form.scan_interval_ms}
            onChange={(e) => setForm({ ...form, scan_interval_ms: e.target.value })}
          />
        </div>
      </div>

      {/* Phase 8.5 — Modbus hardening config */}
      <details className="rounded-md border bg-secondary/20 p-3 space-y-3" open={!isNew && (
        form.request_timeout_ms !== "3000" ||
        form.retry_count !== "1" ||
        form.reconnect_initial_ms !== "1000" ||
        form.reconnect_max_ms !== "30000"
      )}>
        <summary className="text-sm font-semibold cursor-pointer select-none">
          Modbus hardening
          <span className="text-xs text-muted-foreground font-normal ml-2">
            (timeouts, retries, reconnect backoff — defaults work for LAN)
          </span>
        </summary>
        <div className="grid grid-cols-2 gap-3 mt-2">
          <div className="space-y-1.5">
            <Label htmlFor="request_timeout_ms">
              Request timeout (ms) <HelpTip entry={help.channel.response_timeout_ms} />
            </Label>
            <Input
              id="request_timeout_ms"
              type="number"
              min="100"
              max="60000"
              value={form.request_timeout_ms}
              onChange={(e) => setForm({ ...form, request_timeout_ms: e.target.value })}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="retry_count">
              Retry count <HelpTip entry={help.channel.retries} />
            </Label>
            <Input
              id="retry_count"
              type="number"
              min="0"
              max="10"
              value={form.retry_count}
              onChange={(e) => setForm({ ...form, retry_count: e.target.value })}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="reconnect_initial_ms">
              Reconnect initial (ms)
            </Label>
            <Input
              id="reconnect_initial_ms"
              type="number"
              min="100"
              max="60000"
              value={form.reconnect_initial_ms}
              onChange={(e) => setForm({ ...form, reconnect_initial_ms: e.target.value })}
            />
            <p className="text-xs text-muted-foreground">
              Backoff doubles after each failed connect, capped below.
            </p>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="reconnect_max_ms">
              Reconnect max (ms)
            </Label>
            <Input
              id="reconnect_max_ms"
              type="number"
              min="100"
              max="300000"
              value={form.reconnect_max_ms}
              onChange={(e) => setForm({ ...form, reconnect_max_ms: e.target.value })}
            />
          </div>
        </div>
      </details>

      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800 flex gap-2">
          <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      <div className="flex gap-2">
        <Button type="submit" disabled={save.isPending}>
          {save.isPending ? "Saving…" : isNew ? "Create device" : "Save changes"}
        </Button>
      </div>

      {!isNew && device.duty_role !== "none" && (
        <section className="pt-4 border-t">
          <h3 className="text-sm font-semibold">Duty control</h3>
          <p className="text-xs text-muted-foreground mt-1 mb-3">
            In <b>auto mode</b> the field device drives duty/standby via its configured status tag.
            For sticky manual swaps (commissioning, maintenance, testing), <b>take manual control</b>{" "}
            first — that suspends reconciliation for this pair until you return to auto. The global{" "}
            <a href="/global/duty-standby-values" className="underline">Duty/Standby Values</a>{" "}
            settings define what the device reports (defaults: 1 = duty, 0 = standby).
          </p>

          <div className="rounded-md border bg-secondary/20 p-3 space-y-3 text-sm">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <span className="text-xs text-muted-foreground">This device</span>
                <div className="font-medium flex items-center gap-2">
                  {device.name}
                  <span className={cn(
                    "text-[10px] font-medium px-1.5 py-0.5 rounded",
                    device.duty_role === "duty"
                      ? "bg-emerald-100 text-emerald-800"
                      : "bg-slate-100 text-slate-700"
                  )}>
                    {device.duty_role}
                  </span>
                </div>
              </div>
              <div>
                <span className="text-xs text-muted-foreground">Partner</span>
                <div className="font-medium flex items-center gap-2">
                  {allDevices.find((d) => d.id === device.redundant_device_id)?.name ?? "—"}
                  <span className={cn(
                    "text-[10px] font-medium px-1.5 py-0.5 rounded",
                    device.duty_role === "standby"
                      ? "bg-emerald-100 text-emerald-800"
                      : "bg-slate-100 text-slate-700"
                  )}>
                    {device.duty_role === "duty" ? "standby" : "duty"}
                  </span>
                </div>
              </div>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="swap_notes" className="text-xs">
                Notes (optional — saved to history)
              </Label>
              <Input
                id="swap_notes"
                value={swapNotes}
                onChange={(e) => setSwapNotes(e.target.value)}
                placeholder="e.g. Shift change, testing failover, scheduled maintenance"
                className="h-9"
                disabled={swap.isPending || !device.manual_override}
              />
            </div>

            <div className={cn(
              "rounded-md border p-3 text-xs",
              device.manual_override
                ? "border-amber-200 bg-amber-50 text-amber-900"
                : "border-blue-100 bg-blue-50/40 text-blue-900"
            )}>
              <div className="font-semibold mb-1">
                {device.manual_override ? "⚠ Manual override active" : "🔒 Auto (device-led)"}
              </div>
              <div className="leading-relaxed">
                {device.manual_override ? (
                  <>Worker reconciliation is <b>suspended</b> for this pair. Manual swaps will
                  persist until override is disabled. Remember to return to auto when done.</>
                ) : (
                  <>The pair is following the device's self-reported duty status. Manual swaps
                  would be reconciled back within ~5s. To perform a sticky manual swap (e.g. for
                  commissioning or testing), take manual control first.</>
                )}
              </div>
            </div>

            <div className="flex items-center gap-3 flex-wrap">
              {device.manual_override ? (
                <>
                  <Button
                    type="button"
                    onClick={() => swap.mutate()}
                    disabled={swap.isPending}
                  >
                    {swap.isPending ? "Swapping…" : "Swap duty / standby now"}
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => toggleOverride.mutate(false)}
                    disabled={toggleOverride.isPending}
                  >
                    {toggleOverride.isPending ? "Switching…" : "Return to auto (device-led)"}
                  </Button>
                </>
              ) : (
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => toggleOverride.mutate(true)}
                  disabled={toggleOverride.isPending}
                >
                  {toggleOverride.isPending ? "Switching…" : "Take manual control"}
                </Button>
              )}
              {swapSuccessAt && !swap.isPending && (
                <span className="text-xs text-emerald-700">
                  ✓ Swapped at {swapSuccessAt.toLocaleTimeString()}
                </span>
              )}
            </div>

            {dutyHistory.data && dutyHistory.data.length > 0 && (
              <div className="pt-3 border-t border-border/40">
                <div className="text-xs font-medium text-muted-foreground mb-1.5">
                  Recent switches
                </div>
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-muted-foreground">
                      <th className="text-left font-normal py-1">When</th>
                      <th className="text-left font-normal py-1">Became duty</th>
                      <th className="text-left font-normal py-1">Reason</th>
                      <th className="text-left font-normal py-1">Notes</th>
                    </tr>
                  </thead>
                  <tbody>
                    {dutyHistory.data.slice(0, 5).map((h) => (
                      <tr key={h.id} className="border-t border-border/30">
                        <td className="py-1 tabular-nums">{new Date(h.switched_at).toLocaleString()}</td>
                        <td className="py-1">{h.device_name}</td>
                        <td className="py-1">
                          <span className={cn(
                            "text-[10px] font-medium px-1.5 py-0.5 rounded",
                            h.reason === "manual" && "bg-blue-100 text-blue-800",
                            h.reason === "device_reported" && "bg-purple-100 text-purple-800",
                            h.reason === "startup" && "bg-slate-100 text-slate-700",
                            (h.reason === "primary_failed" || h.reason === "partner_channel_failover") && "bg-red-100 text-red-800",
                          )}>
                            {h.reason}
                          </span>
                        </td>
                        <td className="py-1 text-muted-foreground truncate max-w-[200px]" title={h.notes ?? ""}>
                          {h.notes ?? "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </section>
      )}

      {!isNew && (
        <section className="pt-4 border-t border-red-100">
          <h3 className="text-sm font-semibold text-red-700">Delete device</h3>
          <p className="text-xs text-muted-foreground mt-1 mb-2">
            Deletes all this device's register blocks and tags too. Type{" "}
            <code className="font-mono bg-secondary px-1 rounded">{device.name}</code> to confirm.
          </p>
          <div className="flex gap-2">
            <Input
              value={deleteConfirm}
              onChange={(e) => setDeleteConfirm(e.target.value)}
              placeholder={device.name}
              className="flex-1"
            />
            <Button
              type="button"
              variant="outline"
              disabled={deleteConfirm !== device.name || remove.isPending}
              onClick={() => remove.mutate()}
              className="border-red-200 text-red-700 hover:bg-red-50"
            >
              <Trash2 className="h-4 w-4 mr-1.5" />
              {remove.isPending ? "Deleting…" : "Delete"}
            </Button>
          </div>
        </section>
      )}
    </form>
  );
}
