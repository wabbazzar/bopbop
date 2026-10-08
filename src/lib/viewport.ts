export type ViewportFrame = { height: number; top: number; keyboard: boolean };

// iOS can resize/pan only the visual viewport. Some browsers resize both.
// Remember the unobstructed height so either behavior can enter typing mode.
export function watchViewport(update: (frame: ViewportFrame) => void): () => void {
  const visual = window.visualViewport;
  let baseline = window.innerHeight;
  let width = window.innerWidth;
  let keyboard = false;
  let previous: ViewportFrame | undefined;
  let frame = 0;
  let settled: ReturnType<typeof setTimeout>;
  function measure() {
    frame = 0;
    const layout = Math.max(window.innerHeight, document.documentElement.clientHeight);
    const height = visual?.height ?? layout;
    const zoomed = Math.abs((visual?.scale ?? 1) - 1) > .05;
    const editing = document.activeElement?.matches('textarea, input:not([type="button"]):not([type="submit"]), [contenteditable="true"]') ?? false;
    if (Math.abs(window.innerWidth - width) > 80) {
      baseline = layout; width = window.innerWidth;
    }
    if (!zoomed && !editing && !keyboard) baseline = layout;
    baseline = Math.max(baseline, layout);
    keyboard = !zoomed && (editing || keyboard) && baseline - height > Math.max(100, Math.min(150, baseline * .18));
    // Let native pinch zoom magnify/pan the existing layout; resizing the app
    // to zoomed CSS dimensions would reflow it underneath the user's fingers.
    // Safari can report the restored height before its panning offset settles.
    // Clamp that transient offset so the composer cannot slip below the screen.
    const top = Math.min(Math.max(0, visual?.offsetTop ?? 0), Math.max(0, layout - height));
    const next = { height: zoomed ? layout : height, top: zoomed ? 0 : top, keyboard };
    // Focus events also measure the viewport, but must not reposition a
    // conversation (e.g. an expanded trace) when geometry hasn't changed.
    if (previous && Math.abs(next.height - previous.height) < .5 && Math.abs(next.top - previous.top) < .5 && next.keyboard === previous.keyboard) return;
    previous = next;
    update(next);
  }
  function schedule() { if (!frame) frame = requestAnimationFrame(measure); }
  function focus() {
    schedule(); clearTimeout(settled);
    settled = setTimeout(schedule, 350); // final iOS keyboard animation frame
  }
  measure();
  visual?.addEventListener('resize', schedule);
  visual?.addEventListener('scroll', schedule);
  window.addEventListener('resize', schedule);
  document.addEventListener('focusin', focus);
  document.addEventListener('focusout', focus);
  return () => {
    cancelAnimationFrame(frame); clearTimeout(settled);
    visual?.removeEventListener('resize', schedule);
    visual?.removeEventListener('scroll', schedule);
    window.removeEventListener('resize', schedule);
    document.removeEventListener('focusin', focus);
    document.removeEventListener('focusout', focus);
  };
}
