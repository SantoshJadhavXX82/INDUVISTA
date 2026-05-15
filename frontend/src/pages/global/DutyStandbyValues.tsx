/**
 * Setup → Duty/Standby Values — Phase 12.2
 *
 * Global setting: which numeric values reported by a device's duty
 * status tag mean "I am duty" vs "I am standby." Different vendors use
 * different conventions; defaults are 1/0.
 *
 * The worker reads these every reconciliation cycle (~5s), so a change
 * here takes effect on the next polling cycle without restart.
 */
import { useState, useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, Save } from "lucide-react";

import { api, ApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent } from "@/components/ui/card";

type DutyStandbySettings = {
  duty_value: number;
  standby_value: number;
};

export default function DutyStandbyValues() {
  const queryClient = useQueryClient();
  const [dutyValue, setDutyValue] = useState<string>("1");
  const [standbyValue, setStandbyValue] = useState<string>("0");
  const [error, setError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<Date | null>(null);

  const settings = useQuery({
    queryKey: ["settings", "duty-standby"],
    queryFn: () => api.get<DutyStandbySettings>("/settings/duty-standby"),
    staleTime: 60_000,
  });

  // Sync form state when the query resolves
  useEffect(() => {
    if (settings.data) {
      setDutyValue(String(settings.data.duty_value));
      setStandbyValue(String(settings.data.standby_value));
    }
  }, [settings.data]);

  const save = useMutation({
    mutationFn: () => {
      const duty = parseInt(dutyValue, 10);
      const standby = parseInt(standbyValue, 10);
      if (isNaN(duty) || isNaN(standby)) {
        throw new Error("Both values must be integers");
      }
      if (duty === standby) {
        throw new Error("duty value and standby value must be different");
      }
      return api.patch<DutyStandbySettings>("/settings/duty-standby", {
        duty_value: duty,
        standby_value: standby,
      });
    },
    onSuccess: () => {
      setError(null);
      setSavedAt(new Date());
      queryClient.invalidateQueries({ queryKey: ["settings", "duty-standby"] });
    },
    onError: (e: Error) => setError(e instanceof ApiError ? e.detail : e.message),
  });

  return (
    <div className="space-y-4 max-w-2xl">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Duty/Standby Values</h1>
        <p className="text-sm text-muted-foreground mt-1">
          System-wide convention for the numeric values that field devices use to
          report their duty/standby role. Different vendors use different polarities;
          configure once here and the worker applies it to every paired device.
        </p>
      </div>

      <Card>
        <CardContent className="p-6 space-y-5">
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <Label htmlFor="duty_value">Value meaning "duty"</Label>
              <Input
                id="duty_value"
                type="number"
                value={dutyValue}
                onChange={(e) => setDutyValue(e.target.value)}
                disabled={settings.isLoading || save.isPending}
              />
              <p className="text-[11px] text-muted-foreground leading-relaxed">
                When a device's duty status tag reads this value, the worker
                marks the device as <b>duty</b>.
              </p>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="standby_value">Value meaning "standby"</Label>
              <Input
                id="standby_value"
                type="number"
                value={standbyValue}
                onChange={(e) => setStandbyValue(e.target.value)}
                disabled={settings.isLoading || save.isPending}
              />
              <p className="text-[11px] text-muted-foreground leading-relaxed">
                When the duty status tag reads this value, the worker marks the
                device as <b>standby</b>.
              </p>
            </div>
          </div>

          <div className="rounded-md border border-blue-100 bg-blue-50/50 p-3 text-xs text-blue-900 leading-relaxed">
            <p className="font-medium mb-1">How this is used</p>
            <p>
              On the device configuration page, you designate which tag carries the
              duty/standby signal for each paired device. The worker reads that
              tag every cycle, compares its value to the two settings above, and
              reconciles the stored <code className="bg-blue-100 px-1 rounded">duty_role</code>
              {" "}to match. Any other value, or a stale read, is ignored.
            </p>
            <p className="mt-1.5">
              <b>Tip</b>: most PLCs use 1 = duty / 0 = standby. Some Daniel/Emerson
              configurations use 2 = duty / 1 = standby. Check your device's Modbus
              map.
            </p>
          </div>

          {error && (
            <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800 flex gap-2">
              <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          <div className="flex items-center gap-3">
            <Button onClick={() => save.mutate()} disabled={save.isPending || settings.isLoading}>
              <Save className="h-4 w-4 mr-1.5" />
              {save.isPending ? "Saving…" : "Save"}
            </Button>
            {savedAt && (
              <span className="text-xs text-muted-foreground">
                Saved {savedAt.toLocaleTimeString()}. Worker picks this up on the next cycle (~5s).
              </span>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
