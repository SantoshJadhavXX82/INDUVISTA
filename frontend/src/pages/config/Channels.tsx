/**
 * Channels sub-page — list + drawer create/edit/delete.
 *
 * A channel is a logical network connection (TCP/RTU/Serial) that hosts
 * one or more devices. The protocol_connector dropdown is keyed by code
 * ('modbus' for now); future protocols (OPC UA, MQTT) add new options.
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

type Channel = {
  id: number;
  name: string;
  description: string | null;
  protocol_connector_id: number;
  protocol_connector: string;
  transport: string;
  enabled: boolean;
};

type ProtocolConnector = { id: number; code: string };

export default function Channels() {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<Channel | "new" | null>(null);

  const channels = useQuery({
    queryKey: ["channels"],
    queryFn: () => api.get<Channel[]>("/channels"),
  });

  const connectors = useQuery({
    queryKey: ["protocol-connectors"],
    queryFn: () => api.get<ProtocolConnector[]>("/protocol-connectors"),
    staleTime: 60_000,
  });

  return (
    <div className="space-y-3">
      <div className="flex justify-between items-center">
        <span className="text-sm text-muted-foreground">
          {channels.data ? `${channels.data.length} channels` : "Loading…"}
        </span>
        <Button onClick={() => setEditing("new")} size="sm">
          <Plus className="h-4 w-4 mr-1.5" />
          Add channel
        </Button>
      </div>

      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Protocol</TableHead>
                <TableHead>Transport</TableHead>
                <TableHead>Description</TableHead>
                <TableHead>Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {channels.data?.map((c) => (
                <TableRow
                  key={c.id}
                  onClick={() => setEditing(c)}
                  className="cursor-pointer"
                >
                  <TableCell className="font-medium">{c.name}</TableCell>
                  <TableCell className="text-xs">{c.protocol_connector}</TableCell>
                  <TableCell className="text-xs">{c.transport}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {c.description ?? "—"}
                  </TableCell>
                  <TableCell>
                    <Badge variant={c.enabled ? "success" : "secondary"} className="text-xs">
                      {c.enabled ? "enabled" : "disabled"}
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
        title={editing === "new" ? "New channel" : `Channel: ${editing && editing !== "new" ? editing.name : ""}`}
      >
        {editing !== null && (
          <ChannelForm
            channel={editing === "new" ? null : editing}
            connectors={connectors.data ?? []}
            onDone={() => {
              queryClient.invalidateQueries({ queryKey: ["channels"] });
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
  protocol_connector: string;  // the code, e.g. "modbus"
  transport: string;
  enabled: boolean;
};

function ChannelForm({
  channel,
  connectors,
  onDone,
}: {
  channel: Channel | null;
  connectors: ProtocolConnector[];
  onDone: () => void;
}) {
  const isNew = channel === null;
  const [form, setForm] = useState<FormState>({
    name: channel?.name ?? "",
    description: channel?.description ?? "",
    protocol_connector: channel?.protocol_connector ?? (connectors[0]?.code ?? "modbus"),
    transport: channel?.transport ?? "tcp",
    enabled: channel?.enabled ?? true,
  });
  const [error, setError] = useState<string | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState("");

  const save = useMutation({
    mutationFn: async () => {
      if (isNew) {
        return api.post("/channels", {
          name: form.name,
          description: form.description || null,
          transport: form.transport,
          protocol_connector: form.protocol_connector,
        });
      }
      // PATCH only accepts these three fields — name and protocol_connector
      // are immutable once a channel exists.
      return api.patch(`/channels/${channel.id}`, {
        description: form.description || null,
        transport: form.transport,
        enabled: form.enabled,
      });
    },
    onSuccess: onDone,
    onError: (e: Error) => setError(e instanceof ApiError ? e.detail : e.message),
  });

  const remove = useMutation({
    mutationFn: () => api.delete(`/channels/${channel!.id}`),
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
        <Label htmlFor="description">Description</Label>
        <Input
          id="description"
          value={form.description}
          onChange={(e) => setForm({ ...form, description: e.target.value })}
        />
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label htmlFor="protocol">Protocol {!isNew && <span className="normal-case text-muted-foreground">(immutable)</span>}</Label>
          <select
            id="protocol"
            disabled={!isNew}
            value={form.protocol_connector}
            onChange={(e) => setForm({ ...form, protocol_connector: e.target.value })}
            className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm disabled:opacity-50"
          >
            {connectors.map((c) => (
              <option key={c.id} value={c.code}>{c.code}</option>
            ))}
            {/* On create, allow typing a new code by exposing common defaults */}
            {isNew && !connectors.find((c) => c.code === "modbus") && (
              <option value="modbus">modbus</option>
            )}
          </select>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="transport">Transport</Label>
          <select
            id="transport"
            value={form.transport}
            onChange={(e) => setForm({ ...form, transport: e.target.value })}
            className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
          >
            <option value="tcp">tcp</option>
            <option value="rtu">rtu</option>
            <option value="serial">serial</option>
          </select>
        </div>
      </div>

      {!isNew && (
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
              {form.enabled ? "enabled (workers will poll devices on this channel)" : "disabled"}
            </span>
          </label>
        </div>
      )}

      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800 flex gap-2">
          <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      <div className="flex gap-2">
        <Button type="submit" disabled={save.isPending}>
          {save.isPending ? "Saving…" : isNew ? "Create channel" : "Save changes"}
        </Button>
      </div>

      {!isNew && (
        <section className="pt-4 border-t border-red-100">
          <h3 className="text-sm font-semibold text-red-700">Delete channel</h3>
          <p className="text-xs text-muted-foreground mt-1 mb-2">
            Channels with devices can't be deleted — delete the devices first. Type{" "}
            <code className="font-mono bg-secondary px-1 rounded">{channel.name}</code> to confirm.
          </p>
          <div className="flex gap-2">
            <Input
              value={deleteConfirm}
              onChange={(e) => setDeleteConfirm(e.target.value)}
              placeholder={channel.name}
              className="flex-1"
            />
            <Button
              type="button"
              variant="outline"
              disabled={deleteConfirm !== channel.name || remove.isPending}
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
