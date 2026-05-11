import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export default function Placeholder({
  title,
  note,
}: {
  title: string;
  note: string;
}) {
  return (
    <div className="max-w-2xl mx-auto mt-12">
      <Card>
        <CardHeader>
          <CardTitle>{title}</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">{note}</p>
          <p className="text-xs text-muted-foreground mt-4">
            Phase 6 ships one view at a time. The diagnostics view is functional today;
            the rest land in subsequent slices.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
