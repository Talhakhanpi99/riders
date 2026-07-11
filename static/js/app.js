const statusRing = document.querySelector("#statusRing");
const statusText = document.querySelector("#statusText");
const responseText = document.querySelector("#assistantResponse");
const commandInput = document.querySelector("#commandInput");

async function runCommand(text, requireWakeWord = false) {
  if (!text) return;
  statusRing.classList.add("listening");
  statusText.textContent = "Thinking";
  try {
    const response = await fetch("/api/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, require_wake_word: requireWakeWord }),
    });
    if (!response.ok) throw new Error("Command request failed");
    const payload = await response.json();
    responseText.textContent = payload.response || "No response received.";
    statusText.textContent = payload.success ? "Done" : "Ready";
  } catch (error) {
    responseText.textContent = "I could not reach the assistant. Please try again.";
    statusText.textContent = "Offline";
  } finally {
    statusRing.classList.remove("listening");
  }
}

document.querySelector("#sendButton").addEventListener("click", () => runCommand(commandInput.value.trim()));
commandInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") runCommand(commandInput.value.trim());
});
document.querySelector("#listenButton").addEventListener("click", () => {
  statusText.textContent = "Listening";
  commandInput.focus();
});
document.querySelectorAll("[data-command]").forEach((button) => {
  button.addEventListener("click", () => runCommand(button.dataset.command));
});
