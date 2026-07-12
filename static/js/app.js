const statusRing = document.querySelector("#statusRing");
const statusText = document.querySelector("#statusText");
const responseText = document.querySelector("#assistantResponse");
const commandInput = document.querySelector("#commandInput");
let polling = false;

async function requestStartupPermissions() {
  try {
    await fetch("/api/permissions/request", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({keys: ["microphone", "camera", "notifications", "location", "phone", "send_sms", "read_sms", "contacts"]}),
    });
  } catch {
    // Individual actions will ask again if permission was not granted.
  }
}

function setState(text, active = false) {
  statusText.textContent = text;
  statusRing.classList.toggle("listening", active);
}

async function runCommand(text, requireWakeWord = false) {
  if (!text) return;
  setState("Thinking", true);
  try {
    const response = await fetch("/api/command", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({text, require_wake_word: requireWakeWord})});
    const payload = await response.json();
    responseText.textContent = payload.response || "No response received.";
    setState(payload.success ? "Done" : "Ready");
  } catch { responseText.textContent = "I could not reach the assistant."; setState("Offline"); }
}

async function startListening() {
  if (polling) return;
  // Set this before the request so a button tap cannot create a second session.
  polling = true;
  setState("Starting microphone", true);
  try {
    const response = await fetch("/api/listen/start", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({timeout_seconds: 5})});
    const state = await response.json();
    if (!state.started) throw new Error(state.message || "Microphone could not start.");
    pollForSpeech();
  } catch (error) {
    polling = false;
    responseText.textContent = error.message || "Microphone could not start.";
    setState("Ready");
  }
}

async function pollForSpeech() {
  if (!polling) return;
  try {
    const event = await (await fetch("/api/listen/result")).json();
    if (event.status === "starting" || event.status === "listening") {
      setState(event.status === "starting" ? "Starting microphone" : "Listening", true);
      setTimeout(pollForSpeech, 400);
      return;
    }
    if (event.status === "wake_detected") {
      responseText.textContent = event.response;
      polling = false;
      setTimeout(startListening, 150);
      return;
    }
    responseText.textContent = event.response || event.message || "No speech was recognised.";
    if (event.transcript) commandInput.value = event.transcript;
    setState(event.success ? "Done" : "Ready");
  } catch {
    responseText.textContent = "Listening failed. Please try again.";
    setState("Ready");
  }
  polling = false;
}

document.querySelector("#sendButton").addEventListener("click", () => runCommand(commandInput.value.trim()));
commandInput.addEventListener("keydown", (event) => { if (event.key === "Enter") runCommand(commandInput.value.trim()); });
document.querySelector("#listenButton").addEventListener("click", startListening);
document.querySelectorAll("[data-command]").forEach((button) => button.addEventListener("click", () => runCommand(button.dataset.command)));
requestStartupPermissions().finally(startListening);
