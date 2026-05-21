/**
 * Phase 19 — Theme switcher (simplified).
 *
 * Two-button manual control: Light · Dark. The underlying useTheme
 * hook still supports "system" if you set it via DevTools / storage
 * directly, but the visible control is a deliberate manual choice —
 * industrial operators work in fixed environments (control rooms)
 * where OS auto-switching is rarely useful.
 *
 * Visual: pill-shaped track with a sliding white knob that animates
 * between the two positions. Sun and moon icons on each side serve
 * both as labels and click targets. iOS Settings → Display style.
 *
 * Width: ~62px. Fits cleanly alongside the existing TimeFormatSelector
 * and badges in the app header.
 */
import { Sun, Moon } from "lucide-react";
import { cn } from "@/lib/utils";
import { useTheme } from "@/lib/theme";


export function ThemeToggle({ className }: { className?: string }) {
  const { resolved, setTheme } = useTheme();
  // The visible toggle reflects the *resolved* theme (so "system→dark"
  // still shows the moon as active) and any click commits an *explicit*
  // light or dark choice (no more system mode after first click).
  const isDark = resolved === "dark";

  return (
    <div
      className={cn("relative inline-flex items-center cursor-pointer", className)}
      role="switch"
      aria-checked={isDark}
      aria-label={`Theme: ${isDark ? "dark" : "light"}`}
      title={`Switch to ${isDark ? "light" : "dark"} mode`}
      tabIndex={0}
      onClick={() => setTheme(isDark ? "light" : "dark")}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          setTheme(isDark ? "light" : "dark");
        }
      }}
      style={{
        width: 56,
        height: 26,
        borderRadius: 999,
        backgroundColor: isDark ? "var(--ios-gray-3)" : "var(--ios-gray-5)",
        transition: "background-color 0.2s ease",
        padding: 2,
      }}
    >
      {/* Sliding knob */}
      <div
        style={{
          position: "absolute",
          top: 2,
          left: isDark ? 32 : 2,
          width: 22,
          height: 22,
          borderRadius: 999,
          backgroundColor: "#FFFFFF",
          boxShadow: "0 1px 3px rgba(0,0,0,0.15)",
          transition: "left 0.22s cubic-bezier(0.4, 0, 0.2, 1)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        {isDark
          ? <Moon style={{ width: 13, height: 13, color: "#3A3A3C" }} strokeWidth={2.2} />
          : <Sun  style={{ width: 13, height: 13, color: "#FF9500" }} strokeWidth={2.2} />}
      </div>

      {/* Background indicator icons (visible on the non-active side, very faint) */}
      <Sun
        style={{
          position: "absolute",
          left: 7, top: 6,
          width: 14, height: 14,
          color: "var(--ios-gray-1)",
          opacity: isDark ? 0.5 : 0,
          transition: "opacity 0.18s",
          pointerEvents: "none",
        }}
        strokeWidth={2}
      />
      <Moon
        style={{
          position: "absolute",
          right: 7, top: 6,
          width: 14, height: 14,
          color: "var(--ios-gray-1)",
          opacity: isDark ? 0 : 0.5,
          transition: "opacity 0.18s",
          pointerEvents: "none",
        }}
        strokeWidth={2}
      />
    </div>
  );
}
