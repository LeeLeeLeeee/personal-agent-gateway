export function Button({ children, className = "", size = "", variant = "", type = "button", ...props }) {
  const classes = ["btn", variant === "primary" ? "btn-primary" : "", variant === "destructive" ? "btn-destructive" : "", size, className]
    .filter(Boolean)
    .join(" ");
  return (
    <button type={type} className={classes} data-testid={props["data-testid"] || undefined} {...props}>
      {children}
    </button>
  );
}
