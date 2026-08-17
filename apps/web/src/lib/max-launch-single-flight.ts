const activeLaunches = new Map<string, Promise<unknown>>();

/** Reuse one in-flight launch per project inside the current browser tab. */
export function runMaxLaunchSingleFlight<T>(
  projectId: string,
  task: () => Promise<T>,
): Promise<T> {
  const active = activeLaunches.get(projectId) as Promise<T> | undefined;
  if (active) return active;

  const next = task();
  activeLaunches.set(projectId, next);
  const clear = () => {
    if (activeLaunches.get(projectId) === next) activeLaunches.delete(projectId);
  };
  void next.then(clear, clear);
  return next;
}
