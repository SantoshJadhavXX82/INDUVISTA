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
  const [form, setForm] = useState<FormState>({
    name: device?.name ?? "",
    description: device?.description ?? "",
    channel_id: device ? String(device.channel_id) : (channels[0] ? String(channels[0].id) : ""),
    host: device?.host ?? "",
    port: device ? String(device.port) : "502",
    unit_id: device ? String(device.unit_id) : "1",
    duty_role: device?.duty_role ?? "none",
    partner_device_id: device?.redundant_device_id ? String(device.redundant_device_id) : "",
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
