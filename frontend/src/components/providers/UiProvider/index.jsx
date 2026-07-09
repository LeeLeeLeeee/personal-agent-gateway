import { createContext, useCallback, useContext, useRef, useState } from "react";
import { Button } from "../../atoms/Button/index.jsx";

const UiContext = createContext({
  // Defaults keep components working (and unit-testable) without a provider mounted.
  confirm: (opts) => Promise.resolve(window.confirm(opts?.message || "Are you sure?")),
  toast: () => {}
});

export function useConfirm() {
  return useContext(UiContext).confirm;
}

export function useToast() {
  return useContext(UiContext).toast;
}

export function UiProvider({ children }) {
  const [dialog, setDialog] = useState(null);
  const [toasts, setToasts] = useState([]);
  const idRef = useRef(0);

  const confirm = useCallback((opts = {}) => new Promise((resolve) => {
    setDialog({ opts, resolve });
  }), []);

  const dismiss = useCallback((id) => {
    setToasts((list) => list.filter((item) => item.id !== id));
  }, []);

  const toast = useCallback((message, kind = "info") => {
    const id = (idRef.current += 1);
    setToasts((list) => [...list, { id, message, kind }]);
    window.setTimeout(() => dismiss(id), 4000);
  }, [dismiss]);

  function resolveDialog(result) {
    setDialog((current) => {
      if (current) current.resolve(result);
      return null;
    });
  }

  return (
    <UiContext.Provider value={{ confirm, toast }}>
      {children}

      {dialog ? (
        <div className="confirm-backdrop" onClick={() => resolveDialog(false)}>
          <div
            className="confirm-modal"
            role="dialog"
            aria-modal="true"
            aria-label={dialog.opts.title || "Confirm"}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="confirm-head mono">{dialog.opts.title || "CONFIRM"}</div>
            <div className="confirm-body">{dialog.opts.message || "Are you sure?"}</div>
            <div className="confirm-actions">
              <Button size="btn-sm" onClick={() => resolveDialog(false)}>{dialog.opts.cancelLabel || "Cancel"}</Button>
              <Button
                size="btn-sm"
                variant={dialog.opts.danger ? "destructive" : "primary"}
                onClick={() => resolveDialog(true)}
              >
                {dialog.opts.confirmLabel || "Confirm"}
              </Button>
            </div>
          </div>
        </div>
      ) : null}

      {toasts.length ? (
        <div className="toast-host" aria-live="polite">
          {toasts.map((item) => (
            <div key={item.id} className={`toast toast-${item.kind}`} role="status">
              <span className="toast-msg">{item.message}</span>
            </div>
          ))}
        </div>
      ) : null}
    </UiContext.Provider>
  );
}
