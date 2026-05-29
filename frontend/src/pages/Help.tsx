/**
 * Help — in-product help center. Quick start, task recipes ("How do I…"),
 * concept guides, troubleshooting, and support contact. Content lives here
 * inline now; can deep-link to a full docs site later (Phase 29).
 */
import { Link } from "react-router";
import { Rocket, ListChecks, BookOpen, Wrench, LifeBuoy, Info } from "lucide-react";
import { PageHeader } from "@/components/ui/page-header";
import { SectionCard } from "@/components/ui/section-card";

function Section({ icon: Icon, title, children }: { icon: any; title: string; children: React.ReactNode }) {
  return (
    <SectionCard>
      <div className="flex items-center gap-2 mb-2">
        <Icon className="h-4 w-4" style={{ color: "var(--ios-blue)" }} />
        <h3 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>{title}</h3>
      </div>
      <div className="text-sm space-y-1.5" style={{ color: "var(--text-secondary)" }}>{children}</div>
    </SectionCard>
  );
}

function QA({ q, a }: { q: string; a: string }) {
  return (
    <div>
      <div className="font-medium" style={{ color: "var(--text-primary)" }}>{q}</div>
      <div className="text-xs leading-relaxed">{a}</div>
    </div>
  );
}

export default function Help() {
  return (
    <div className="space-y-4 max-w-3xl mx-auto">
      <PageHeader title="Help" subtitle="Getting started, common tasks, and troubleshooting" />

      <Section icon={Rocket} title="Quick start">
        <ol className="list-decimal pl-5 space-y-1 text-xs">
          <li>Sign in. Your role (Viewer / Operator / Engineer / Admin) controls what you can change.</li>
          <li>Open the <strong>Dashboard</strong> for live tag values, and <strong>Trend</strong> for history.</li>
          <li>Check <strong>Alarms</strong> for anything active; operators can acknowledge.</li>
          <li>Engineers configure devices, tags, and alarms under <strong>Configure</strong>.</li>
          <li>Review storage under <strong>Diagnose → Historian</strong>; tune per-tag logging on each tag.</li>
        </ol>
      </Section>

      <Section icon={ListChecks} title="How do I…">
        <QA q="Add a Modbus device?" a="Configure → Devices → Add. Set host/port/unit, then add register blocks and tags." />
        <QA q="Reduce historian storage?" a="Open a tag, set Historian logging to 'On change' with a deadband, or 'Periodic' for noisy tags. See Diagnose → Historian for the projected savings." />
        <QA q="Configure an alarm?" a="Alarms → Rules → New rule. Pick the tag, rule type, threshold, severity, and optional delays." />
        <QA q="Acknowledge an alarm?" a="Alarms → Active → Ack (Operator role or higher)." />
        <QA q="Write a setpoint?" a="Mark the register block Read+Write, then use the Write Console (Operator role or higher)." />
        <QA q="Add a user?" a="Global/Setup → Users → Add user (Admin only). Pick a role; the user sets their password on first login." />
      </Section>

      <Section icon={BookOpen} title="Key concepts">
        <QA q="Data quality (st)" a="Each value carries a status byte. 128+ is GOOD; lower values flag stale, timeout, or decode problems. Hover any quality badge for detail." />
        <QA q="Logging modes" a="Every-sample logs every poll; On-change logs only meaningful changes (deadband); Periodic logs on a fixed interval. Live values and alarms always see every reading regardless." />
        <QA q="Stale vs constant" a="A tag is STALE only when communication stops. A value that's constant but still polling is healthy, not stale." />
        <QA q="Roles" a="Hierarchical: Viewer ⊂ Operator ⊂ Engineer ⊂ Admin. Each includes everything below it." />
      </Section>

      <Section icon={Wrench} title="Troubleshooting">
        <QA q="A tag shows STALE" a="Communication to its device has stopped or slowed past the stale threshold. Check the device connection under Diagnose → Health and the device's network reachability." />
        <QA q="Alarms aren't firing" a="Confirm the rule is enabled, the threshold and rule type are correct, and the tag is reading GOOD quality. Check on/off delays aren't masking brief events." />
        <QA q="A button is greyed out" a="Your role doesn't permit that action. Hover the control for the required role, or ask an admin to adjust your role." />
        <QA q="The Historian shows huge storage" a="Tags are on every-sample. Switch steady tags to On-change and noisy ones to Periodic; watch the projection drop." />
      </Section>

      <Section icon={LifeBuoy} title="Support">
        <p className="text-xs">
          For help beyond this guide, open <Link to="/about" className="hover:underline" style={{ color: "var(--ios-blue)" }}>About</Link>,
          click <strong>Copy diagnostics for support</strong>, and send that to your system administrator along with a description of the issue.
        </p>
      </Section>

      <div className="text-center text-xs pt-2" style={{ color: "var(--text-secondary)" }}>
        <Link to="/about" className="inline-flex items-center gap-1 hover:underline" style={{ color: "var(--ios-blue)" }}>
          <Info className="h-3 w-3" /> Version & system info
        </Link>
      </div>
    </div>
  );
}
