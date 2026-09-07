"use client";

type AlertTone = "info" | "success" | "warning" | "error";

export function Alert({
  tone = "info",
  title,
  children,
  action,
  onDismiss,
}: {
  tone?: AlertTone;
  title?: string;
  children: React.ReactNode;
  action?: React.ReactNode;
  onDismiss?: () => void;
}) {
  const role = tone === "error" || tone === "warning" ? "alert" : "status";
  return (
    <div className={`alert alert-${tone}`} role={role}>
      <div className="alert-body">
        {title ? <p className="alert-title">{title}</p> : null}
        <div className="alert-text">{children}</div>
      </div>
      {(action || onDismiss) && (
        <div className="alert-actions">
          {action}
          {onDismiss ? (
            <button type="button" className="secondary alert-dismiss" onClick={onDismiss}>
              Dismiss
            </button>
          ) : null}
        </div>
      )}
    </div>
  );
}
