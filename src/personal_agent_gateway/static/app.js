const form = document.querySelector("#chat-form");
const message = document.querySelector("#message");
const output = document.querySelector("#output");

form?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message: message?.value ?? "" }),
  });
  output.textContent = JSON.stringify(await response.json(), null, 2);
});
