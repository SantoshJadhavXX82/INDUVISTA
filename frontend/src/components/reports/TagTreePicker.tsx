/**
 * TagTreePicker — browse & multi-select tags from a hierarchy, OPC-explorer style.
 *
 * Replaces the type-to-search-one-at-a-time flow on the Data tab. Loads every
 * tag with its device + register-block context and renders a collapsible tree:
 *
 *   Device ▸ Register block ▸ Tag (addr · type · unit)
 *
 * Tri-state checkboxes at every level (a device/block checkbox selects or clears
 * all its tags). Already-bound tags show as checked + disabled. A search box
 * filters the tree (parents stay visible if any child matches). Confirm returns
 * all newly-selected tags at once.
 */
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronRight, ChevronDown, Search, X, Check } from "lucide-react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

type TagFull = {
  id: number; name: string;
  device_id: number; device_name: string;
  register_block_id: number | null; register_block_name: string | null;
  unit_code: string | null; unit_label: string | null; engineering_unit: string | null;
  address: number; data_type: string;
};
type State = "none" | "some" | "all";

function unitOf(t: TagFull): string {
  return t.unit_code || t.engineering_unit || "";
}
function leafInfo(t: TagFull): string {
  const u = unitOf(t);
  return [`@${t.address}`, t.data_type, u].filter(Boolean).join(" · ");
}

function TriCheck({ state, onClick, disabled }:
  { state: State; onClick: () => void; disabled?: boolean }) {
  return (
    <button type="button" onClick={onClick} disabled={disabled}
      className="h-4 w-4 inline-flex items-center justify-center rounded shrink-0"
      style={{
        border: "1px solid var(--separator)",
        backgroundColor: state === "none" ? "transparent" : "var(--ios-blue, #007aff)",
        opacity: disabled ? 0.45 : 1, cursor: disabled ? "default" : "pointer",
      }}>
      {state === "all" && <Check className="h-3 w-3" style={{ color: "#fff" }} />}
      {state === "some" && <span style={{ width: 8, height: 2, backgroundColor: "#fff", borderRadius: 1 }} />}
    </button>
  );
}

export function TagTreePicker({
  open, onClose, onConfirm, alreadyBound,
}: {
  open: boolean;
  onClose: () => void;
  onConfirm: (tags: { id: number; name: string }[]) => void;
  alreadyBound: Set<number>;
}) {
  const tagsQ = useQuery({
    queryKey: ["tag-tree-all"],
    queryFn: () => api.get<TagFull[]>("/tags?limit=1000"),
    enabled: open,
  });
  const [sel, setSel] = useState<Set<number>>(new Set());
  const [q, setQ] = useState("");
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  const all = tagsQ.data ?? [];
  const filtered = useMemo(() => {
    const n = q.trim().toLowerCase();
    if (!n) return all;
    return all.filter((t) =>
      t.name.toLowerCase().includes(n) ||
      unitOf(t).toLowerCase().includes(n) ||
      String(t.address).includes(n) ||
      (t.device_name || "").toLowerCase().includes(n));
  }, [all, q]);

  const tree = useMemo(() => {
    const devs = new Map<number, { name: string; blocks: Map<string, { name: string; tags: TagFull[] }> }>();
    for (const t of filtered) {
      if (!devs.has(t.device_id)) devs.set(t.device_id, { name: t.device_name || `device ${t.device_id}`, blocks: new Map() });
      const d = devs.get(t.device_id)!;
      const bkey = t.register_block_id == null ? "nb" : `b${t.register_block_id}`;
      const bname = t.register_block_name ?? "(no register block)";
      if (!d.blocks.has(bkey)) d.blocks.set(bkey, { name: bname, tags: [] });
      d.blocks.get(bkey)!.tags.push(t);
    }
    return devs;
  }, [filtered]);

  if (!open) return null;

  const forceOpen = q.trim().length > 0;
  const isOpen = (k: string) => forceOpen || !collapsed.has(k);
  const toggleOpen = (k: string) =>
    setCollapsed((p) => { const n = new Set(p); n.has(k) ? n.delete(k) : n.add(k); return n; });

  const pickable = (tags: TagFull[]) => tags.filter((t) => !alreadyBound.has(t.id));
  const stateOf = (tags: TagFull[]): State => {
    const p = pickable(tags);
    if (p.length === 0) return "all"; // all already bound
    const n = p.filter((t) => sel.has(t.id)).length;
    return n === 0 ? "none" : n === p.length ? "all" : "some";
  };
  const toggleMany = (tags: TagFull[]) => {
    const p = pickable(tags);
    const allSel = p.length > 0 && p.every((t) => sel.has(t.id));
    setSel((prev) => {
      const next = new Set(prev);
      for (const t of p) { if (allSel) next.delete(t.id); else next.add(t.id); }
      return next;
    });
  };
  const toggleOne = (t: TagFull) => {
    if (alreadyBound.has(t.id)) return;
    setSel((prev) => { const n = new Set(prev); n.has(t.id) ? n.delete(t.id) : n.add(t.id); return n; });
  };

  const confirm = () => {
    const chosen = all.filter((t) => sel.has(t.id)).map((t) => ({ id: t.id, name: t.name }));
    onConfirm(chosen); setSel(new Set()); onClose();
  };
  const cancel = () => { setSel(new Set()); onClose(); };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ backgroundColor: "rgba(0,0,0,0.35)" }} onClick={cancel}>
      <div className="w-full max-w-2xl rounded-xl flex flex-col"
        style={{ backgroundColor: "var(--bg-elevated, #fff)", maxHeight: "82vh",
                 border: "0.5px solid var(--separator)" }}
        onClick={(e) => e.stopPropagation()}>
        {/* header */}
        <div className="flex items-center gap-2 px-4 py-3"
          style={{ borderBottom: "0.5px solid var(--separator)" }}>
          <span className="text-[14px] font-semibold" style={{ color: "var(--text-primary)" }}>Select tags</span>
          <span className="text-[12px]" style={{ color: "var(--ios-gray-1)" }}>browse by device · multi-select</span>
          <button onClick={cancel} className="ml-auto" style={{ color: "var(--text-secondary)" }}><X className="h-4 w-4" /></button>
        </div>

        {/* search */}
        <div className="px-4 pt-3">
          <div className="relative">
            <Search className="h-3.5 w-3.5 absolute left-2.5 top-1/2 -translate-y-1/2" style={{ color: "var(--ios-gray-1)" }} />
            <Input className="pl-8" placeholder="Filter by name, unit, address, device…"
              value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
        </div>

        {/* tree */}
        <div className="flex-1 overflow-auto px-2 py-2 mt-1" style={{ minHeight: 200 }}>
          {tagsQ.isLoading && <div className="px-3 py-6 text-center text-[12px]" style={{ color: "var(--ios-gray-1)" }}>Loading tags…</div>}
          {!tagsQ.isLoading && tree.size === 0 && (
            <div className="px-3 py-6 text-center text-[12px]" style={{ color: "var(--ios-gray-1)" }}>No tags match.</div>
          )}
          {Array.from(tree.entries()).map(([devId, dev]) => {
            const dkey = `d${devId}`;
            const devTags = Array.from(dev.blocks.values()).flatMap((b) => b.tags);
            return (
              <div key={dkey}>
                <Node depth={0} open={isOpen(dkey)} onToggleOpen={() => toggleOpen(dkey)}
                  state={stateOf(devTags)} onCheck={() => toggleMany(devTags)}
                  label={dev.name} meta={`${devTags.length} tag${devTags.length === 1 ? "" : "s"}`} bold />
                {isOpen(dkey) && Array.from(dev.blocks.entries()).map(([bkey, blk]) => {
                  const bk = `${dkey}${bkey}`;
                  return (
                    <div key={bk}>
                      <Node depth={1} open={isOpen(bk)} onToggleOpen={() => toggleOpen(bk)}
                        state={stateOf(blk.tags)} onCheck={() => toggleMany(blk.tags)}
                        label={blk.name} meta={`${blk.tags.length}`} />
                      {isOpen(bk) && blk.tags.map((t) => {
                        const boundAlready = alreadyBound.has(t.id);
                        return (
                          <Leaf key={t.id} depth={2}
                            state={(boundAlready || sel.has(t.id)) ? "all" : "none"}
                            disabled={boundAlready}
                            onCheck={() => toggleOne(t)}
                            label={t.name} info={leafInfo(t)} boundAlready={boundAlready} />
                        );
                      })}
                    </div>
                  );
                })}
              </div>
            );
          })}
        </div>

        {/* footer */}
        <div className="flex items-center gap-2 px-4 py-3" style={{ borderTop: "0.5px solid var(--separator)" }}>
          <span className="text-[12px]" style={{ color: "var(--ios-gray-1)" }}>
            {sel.size} selected
          </span>
          <div className="ml-auto flex gap-2">
            <Button variant="ghost" size="sm" onClick={cancel}>Cancel</Button>
            <Button size="sm" onClick={confirm} disabled={sel.size === 0}>
              Add {sel.size > 0 ? sel.size : ""} tag{sel.size === 1 ? "" : "s"}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

function Node({
  depth, open, onToggleOpen, state, onCheck, label, meta, bold,
}: {
  depth: number; open: boolean; onToggleOpen: () => void;
  state: State; onCheck: () => void; label: string; meta?: string; bold?: boolean;
}) {
  return (
    <div className="flex items-center gap-2 py-1 rounded hover:bg-[var(--bg-grouped,#f2f2f7)]"
      style={{ paddingLeft: 8 + depth * 18 }}>
      <button onClick={onToggleOpen} style={{ color: "var(--text-secondary)" }}>
        {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
      </button>
      <TriCheck state={state} onClick={onCheck} />
      <span className={`text-[12.5px] truncate ${bold ? "font-semibold" : ""}`}
        style={{ color: "var(--text-primary)" }}>{label}</span>
      {meta && <span className="text-[10.5px] ml-1" style={{ color: "var(--ios-gray-1)" }}>{meta}</span>}
    </div>
  );
}

function Leaf({
  depth, state, disabled, onCheck, label, info, boundAlready,
}: {
  depth: number; state: State; disabled?: boolean; onCheck: () => void;
  label: string; info: string; boundAlready?: boolean;
}) {
  return (
    <div className="flex items-center gap-2 py-1 rounded hover:bg-[var(--bg-grouped,#f2f2f7)]"
      style={{ paddingLeft: 8 + depth * 18 }}>
      <span className="w-3.5 shrink-0" />
      <TriCheck state={state} onClick={onCheck} disabled={disabled} />
      <span className="text-[12.5px] truncate" style={{ color: "var(--text-primary)" }}>{label}</span>
      <span className="text-[10.5px] truncate" style={{ color: "var(--ios-gray-1)" }}>{info}</span>
      {boundAlready && (
        <span className="text-[9.5px] ml-1 px-1 rounded shrink-0"
          style={{ backgroundColor: "var(--bg-grouped,#eee)", color: "var(--ios-gray-1)" }}>bound</span>
      )}
    </div>
  );
}
