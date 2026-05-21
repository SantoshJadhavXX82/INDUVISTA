/**
 * Phase 19 — SwipeCarousel.
 *
 * iOS-style swipe-between-pages component. Wraps embla-carousel-react
 * with sensible defaults for our use case (horizontal, snap to page,
 * touch swipe + mouse drag, page-dot indicators at bottom).
 *
 * Use case: mobile Dashboard splits into Overview / Process / Devices
 * cards that the operator swipes between with their thumb. Each "page"
 * is rendered as a child of SwipeCarousel. Page dots auto-generate.
 *
 * Why embla: 5kb gzipped, native iOS scroll inertia, no jank on
 * direction changes, supports keyboard arrow navigation out of the box.
 *
 * Install: npm install embla-carousel-react
 */
import { useEffect, useState, useCallback } from "react";
import useEmblaCarousel from "embla-carousel-react";


export interface SwipeCarouselProps {
  /** Each child becomes one swipeable page. */
  children: React.ReactNode[];
  /** Index to start at. Defaults to 0. */
  startIndex?: number;
  /** Called when the visible slide changes (useful for syncing tabs). */
  onSlideChange?: (index: number) => void;
  className?: string;
}


export function SwipeCarousel({
  children, startIndex = 0, onSlideChange, className,
}: SwipeCarouselProps) {
  const [emblaRef, emblaApi] = useEmblaCarousel({
    align: "center",
    loop: false,
    startIndex,
    skipSnaps: false,    // every slide is a snap point
    duration: 22,         // iOS-like snap speed (~220ms)
  });

  const [selectedIndex, setSelectedIndex] = useState(startIndex);

  // Sync internal state + parent callback when slide changes
  useEffect(() => {
    if (!emblaApi) return;
    const onSelect = () => {
      const idx = emblaApi.selectedScrollSnap();
      setSelectedIndex(idx);
      onSlideChange?.(idx);
    };
    emblaApi.on("select", onSelect);
    onSelect();   // initial sync
    return () => { emblaApi.off("select", onSelect); };
  }, [emblaApi, onSlideChange]);

  const scrollTo = useCallback((idx: number) => {
    emblaApi?.scrollTo(idx);
  }, [emblaApi]);

  return (
    <div className={className}>
      <div ref={emblaRef} style={{ overflow: "hidden" }}>
        <div style={{ display: "flex" }}>
          {children.map((child, i) => (
            <div
              key={i}
              style={{
                flex: "0 0 100%",
                minWidth: 0,
                paddingLeft: 4,
                paddingRight: 4,
              }}
            >
              {child}
            </div>
          ))}
        </div>
      </div>
      <PageDots
        count={children.length}
        active={selectedIndex}
        onPick={scrollTo}
      />
    </div>
  );
}


function PageDots({
  count, active, onPick,
}: { count: number; active: number; onPick: (i: number) => void }) {
  return (
    <div className="flex justify-center gap-1.5 mt-3" role="tablist" aria-label="Page indicator">
      {Array.from({ length: count }).map((_, i) => (
        <button
          key={i}
          type="button"
          onClick={() => onPick(i)}
          role="tab"
          aria-selected={i === active}
          aria-label={`Go to page ${i + 1}`}
          style={{
            width: i === active ? 18 : 7,
            height: 7,
            borderRadius: 999,
            backgroundColor: i === active ? "var(--ios-blue)" : "var(--ios-gray-3)",
            border: "none",
            cursor: "pointer",
            transition: "width 0.2s ease, background-color 0.2s ease",
            padding: 0,
          }}
        />
      ))}
    </div>
  );
}
