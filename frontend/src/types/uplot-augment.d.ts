import "uplot";

declare module "uplot" {
  // InduVista stashes the owning tag id on marker series so the cursor/
  // hit-testing can map a hovered series back to its tag. uPlot ignores
  // unknown keys at runtime; this just makes the assignment type-safe.
  interface Series {
    __tagId?: number;
  }
}
