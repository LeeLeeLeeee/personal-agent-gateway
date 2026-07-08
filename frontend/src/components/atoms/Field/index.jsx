export function InputField({ as = "input", className = "", ...props }) {
  const Component = as;
  return <Component className={["input-field", className].filter(Boolean).join(" ")} {...props} />;
}
