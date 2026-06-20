/**
 * TrendTagTreePicker - drop-in replacement for the flat TagPicker on the
 * Trend page. Same props (selectedIds / onChange / maxTags) so the Trend
 * usage is unchanged, but tags are added through the device > block > tag
 * tree modal (TagTreePicker, the same component used on the Reports Data
 * tab) instead of a flat type-ahead list. Selected tags still render as
 * removable chips; the cap is enforced when merging the tree selection.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Plus, X } from "lucide-react";
import { api } from "@/lib/api";
import type { TrendTag } from "@/types/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { TagTreePicker } from "@/components/reports/TagTreePicker";

type Props = {
  selectedIds: number[];
  onChange: (ids: number[]) => void;
  maxTags?: number;
};

export default function TrendTagTreePicker({
  selectedIds,
  onChange,
  maxTags = 6,
}: Props) {
  const [open, setOpen] = useState(false);

  // Same source the flat picker used - resolves ids -> names for the chips.
  const tagsQuery = useQuery({
    queryKey: ["trend-tags"],
    queryFn: () =>
      api.get<TrendTag[]>("/trends/tags?enabled_only=false&limit=2000"),
    staleTime: 60_000,
  });
  const tags = tagsQuery.data ?? [];
  const selected = tags.filter((t) => selectedIds.includes(t.id));
  const atCap = selectedIds.length >= maxTags;

  const removeTag = (id: number) =>
    onChange(selectedIds.filter((x) => x !== id));

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-1.5">
        {selected.map((t) => (
          <Badge
            key={t.id}
            variant="secondary"
            className="gap-1 pr-1 max-w-[240px]"
          >
            <span className="truncate">{t.name}</span>
            <button
              type="button"
              aria-label={`Remove ${t.name}`}
              onClick={() => removeTag(t.id)}
              className="ml-0.5 rounded hover:bg-muted-foreground/20"
            >
              <X className="h-3 w-3" />
            </button>
          </Badge>
        ))}

        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() => setOpen(true)}
          disabled={atCap}
          title={
            atCap
              ? `Maximum ${maxTags} tags selected`
              : "Browse tags by device"
          }
        >
          <Plus className="h-4 w-4 mr-1.5" />
          {atCap ? `Max ${maxTags} tags` : "Browse tags"}
        </Button>

        {selectedIds.length > 0 && (
          <span className="text-xs text-muted-foreground">
            {selectedIds.length} / {maxTags}
          </span>
        )}
      </div>

      <TagTreePicker
        open={open}
        onClose={() => setOpen(false)}
        alreadyBound={new Set(selectedIds)}
        onConfirm={(chosen) => {
          const room = Math.max(0, maxTags - selectedIds.length);
          const add = chosen
            .map((c) => c.id)
            .filter((id) => !selectedIds.includes(id))
            .slice(0, room);
          if (add.length) onChange([...selectedIds, ...add]);
        }}
      />
    </div>
  );
}
