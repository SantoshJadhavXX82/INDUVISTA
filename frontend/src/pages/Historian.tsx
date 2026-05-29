/**
 * Historian — dedicated Diagnose tab for historian storage analytics.
 *
 * Currently hosts the site-wide storage projection (rows/day, year cost,
 * reduction vs every-sample, per-protocol split, noisiest tags). Future
 * historian tooling (retention status, compression stats, buffer health)
 * can live here too.
 */
import { PageHeader } from "@/components/ui/page-header";
import { StorageProjectionCard } from "@/components/diagnostics/storage-projection";

export default function Historian() {
  return (
    <div className="space-y-4 max-w-7xl mx-auto">
      <PageHeader
        title="Historian"
        subtitle="Storage projection and logging analytics — see how much history each tag will consume and where to tune logging policy"
      />
      <StorageProjectionCard />
    </div>
  );
}
