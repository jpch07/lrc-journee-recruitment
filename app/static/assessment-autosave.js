// Request-local form state. Only an acknowledged payload is considered saved.
export function assessmentAutosave({read, write, version, status, onConflict, delay = 700}) {
  const signature = value => JSON.stringify(value);
  let acknowledged = signature(read()), timer = null, flight = null, conflict = false, disposed = false;
  const dirty = () => signature(read()) !== acknowledged;
  const save = async (forceVersion = null) => {
    clearTimeout(timer);
    if (disposed || (conflict && forceVersion == null)) return false;
    if (flight) {
      const ok = await flight;
      return ok && !disposed ? save(forceVersion) : false;
    }
    if (!dirty() && forceVersion == null) { status("Saved", "saved"); return true; }
    const submitted = read(), sent = signature(submitted);
    status("Saving", "saving");
    flight = (async () => {
      try {
        const result = await write({...submitted, base_version: forceVersion ?? version});
        version = result.version;
        acknowledged = sent;
        conflict = false;
        if (!disposed) { onConflict(false); status(dirty() ? "Saving" : "Saved", dirty() ? "saving" : "saved"); }
        return true;
      } catch (error) {
        if (error.status === 409) conflict = true;
        if (!disposed) { onConflict(conflict); status(conflict ? "Conflict" : "Error", conflict ? "conflict" : "error"); }
        return false;
      }
    })();
    const ok = await flight;
    flight = null;
    // New edits can arrive during this write, including a return to the previous value.
    return ok && dirty() && !disposed ? save() : ok;
  };
  const schedule = () => {
    if (disposed) return;
    clearTimeout(timer);
    if (!dirty() && !flight) { status("Saved", "saved"); return; }
    status(conflict ? "Conflict" : "Saving", conflict ? "conflict" : "saving");
    timer = setTimeout(() => save(), delay);
  };
  return {save, schedule, dirty, dispose() { disposed = true; clearTimeout(timer); }};
}
