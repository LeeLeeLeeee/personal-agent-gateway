import { useState } from "react";
import { Button } from "../../atoms/Button/index.jsx";
import { InputField } from "../../atoms/Field/index.jsx";

export function Composer({ busy, onSend }) {
  const [message, setMessage] = useState("");

  async function send() {
    const trimmed = message.trim();
    if (!trimmed || busy) return;
    setMessage("");
    await onSend(trimmed);
  }

  return (
    <div className="composer">
      <InputField
        as="textarea"
        rows="2"
        placeholder="Message the agent, or describe a local action..."
        disabled={busy}
        value={message}
        onChange={(event) => setMessage(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            send();
          }
        }}
      />
      <Button variant="primary" disabled={busy} onClick={send}>Send</Button>
    </div>
  );
}
