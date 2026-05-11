/**
 * Devices sub-page — list + drawer create/edit/delete.
 *
 * A device is a single Modbus endpoint (host:port, unit_id) within a
 * channel. Tags belong to devices via register_blocks. Editing host/port
 * triggers the Phase 3.5 worker hot-reload within ~10 seconds.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2, AlertCircle } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Drawer } from "@/components/ui/drawer";
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
  stale_after_sec: number;
  scan_interval_ms: number;
  enabled: boolean;
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
                <TableHead>Channel</TableHead>
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
  stale_after_sec: string;
  scan_interval_ms: string;
  enabled: boolean;
};

function DeviceForm({
  device,
  channels,
  onDone,
}: {
  device: Device | null;
  channels: Channel[];
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
    stale_after_sec: device ? String(device.stale_after_sec) : "30",
    scan_interval_ms: device ? String(device.scan_interval_ms) : "1000",
    enabled: device?.enabled ?? true,
  });
  const [error, setError] = useState<string | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState("");

  const save = useMutation({
    mutationFn: async () => {
      if (isNew) {
        return api.post("/devices", {
          name: form.name,
          description: form.description || null,
          channel_id: parseInt(form.channel_id, 10),
          host: form.host,
          port: parseInt(form.port, 10),
          unit_id: parseInt(form.unit_id, 10),
          duty_role: form.duty_role,
          stale_after_sec: parseInt(form.stale_after_sec, 10),
          scan_interval_ms: parseInt(form.scan_interval_ms, 10),
          enabled: form.enabled,
        });
      }
      // PATCH excludes name and channel_id — they're immutable once a
      // device exists. Sending them silently fails on backends with
      // model_config.extra='forbid'; we just don't include them.
      return api.patch(`/devices/${device.id}`, {
        description: form.description || null,
        host: form.host,
        port: parseInt(form.port, 10),
        unit_id: parseInt(form.unit_id, 10),
        duty_role: form.duty_role,
        stale_after_sec: parseInt(form.stale_after_sec, 10),
        scan_interval_ms: parseInt(form.scan_interval_ms, 10),
        enabled: form.enabled,
      });
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
          <Label htmlFor="name">Name {!isNew && <span className="normal-case text-muted-foreground">(immutable)</span>}</Label>
          <Input
            id="name"
            required
            disabled={!isNew}
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="channel">Channel {!isNew && <span className="normal-case text-muted-foreground">(immutable)</span>}</Label>
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
        <Label htmlFor="description">Description</Label>
        <Input
          id="description"
          value={form.description}
          onChange={(e) => setForm({ ...form, description: e.target.value })}
        />
      </div>

      <div className="grid grid-cols-3 gap-3">
        <div className="col-span-2 space-y-1.5">
          <Label htmlFor="host">Host</Label>
          <Input
            id="host"
            required
            value={form.host}
            onChange={(e) => setForm({ ...form, host: e.target.value })}
            placeholder="192.168.1.10 or simulator service name"
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="port">Port</Label>
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
          <Label htmlFor="unit_id">Unit ID</Label>
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
            onChange={(e) => setForm({ ...form, duty_role: e.target.value })}
            className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
          >
            <option value="none">none (standalone)</option>
            <option value="duty">duty (active in HA pair)</option>
            <option value="standby">standby (passive in HA pair)</option>
          </select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="enabled">Status</Label>
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
          <Label htmlFor="scan_interval_ms">Scan interval (ms)</Label>
          <Input
            id="scan_interval_ms"
            type="number"
            required
            value={form.scan_interval_ms}
            onChange={(e) => setForm({ ...form, scan_interval_ms: e.target.value })}
          />
        </div>
      </div>

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
