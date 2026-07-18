const statusRing = document.querySelector("#statusRing");
const statusText = document.querySelector("#statusText");
const responseText = document.querySelector("#assistantResponse");
const commandInput = document.querySelector("#commandInput");
let polling = false;
let offlineListening = false;
let androidAvailable = false;
let speechSpeed = 1.0;
let lastUpdateId = 0;

function speakText(text) {
  if ('speechSynthesis' in window) {
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.rate = speechSpeed || 1.0;
    
    // Choose a high-quality Siri-like voice if available
    const voices = window.speechSynthesis.getVoices();
    if (voices.length > 0) {
      const preferred = voices.find(v => v.lang.startsWith('en') && (v.name.includes('Google') || v.name.includes('Natural') || v.name.includes('Female')));
      if (preferred) {
        utterance.voice = preferred;
      }
    }
    
    window.speechSynthesis.speak(utterance);
  }
}

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
    if (payload.response) {
      speakText(payload.response);
    }
  } catch { responseText.textContent = "I could not reach the assistant."; setState("Offline"); }
}

async function startListening() {
  if (polling) return;
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

async function toggleOfflineListening() {
  const endpoint = offlineListening ? "/api/offline-listener/stop" : "/api/offline-listener/start";
  try {
    const payload = await (await fetch(endpoint, {method: "POST"})).json();
    if (!payload.started && !payload.stopped) throw new Error(payload.message || "Offline listener could not be changed.");
    offlineListening = Boolean(payload.started) && !payload.stopped;
    document.querySelector("#offlineListenButton").textContent = offlineListening ? "Stop Offline Listening" : "Start Offline Listening";
    responseText.textContent = payload.message || (offlineListening ? "Offline listening started." : "Offline listening stopped.");
    setState(offlineListening ? "Offline listening" : "Ready", offlineListening);
  } catch (error) {
    responseText.textContent = error.message || "Offline listener could not be changed.";
  }
}

async function pollForSpeech() {
  if (!polling) return;
  try {
    const event = await (await fetch("/api/listen/result")).json();
    if (event.status === "starting" || event.status === "listening") {
      setState(event.status === "starting" ? "Starting microphone" : "Listening", true);
      setTimeout(pollForSpeech, 100);
      return;
    }
    if (event.status === "wake_detected") {
      responseText.textContent = event.response;
      if (event.response) {
        speakText(event.response);
      }
      polling = false;
      setTimeout(startListening, 150);
      return;
    }
    responseText.textContent = event.response || event.message || "No speech was recognised.";
    if (event.response) {
      speakText(event.response);
    }
    if (event.transcript) commandInput.value = event.transcript;
    setState(event.success ? "Done" : "Ready");
  } catch {
    responseText.textContent = "Listening failed. Please try again.";
    setState("Ready");
  }
  polling = false;
}

async function pollStatusUpdates() {
  try {
    const res = await (await fetch("/api/status")).json();
    androidAvailable = res.android_available;
    if (res.settings && res.settings.speech_speed) {
      speechSpeed = parseFloat(res.settings.speech_speed);
    }
    // Check if background assistant processed a command
    if (res.last_update_id !== undefined && res.last_update_id > lastUpdateId) {
      // First update on load should not trigger speaking/alerting
      if (lastUpdateId !== 0) {
        if (res.last_response) {
          responseText.textContent = res.last_response;
          speakText(res.last_response);
        }
        if (res.last_transcript) {
          commandInput.value = res.last_transcript;
        }
      }
      lastUpdateId = res.last_update_id;
    }
  } catch (err) {
    console.error("Could not fetch status update:", err);
  }
  setTimeout(pollStatusUpdates, 1000);
}

document.querySelector("#sendButton").addEventListener("click", () => runCommand(commandInput.value.trim()));
commandInput.addEventListener("keydown", (event) => { if (event.key === "Enter") runCommand(commandInput.value.trim()); });
document.querySelector("#listenButton").addEventListener("click", startListening);
document.querySelector("#offlineListenButton").addEventListener("click", toggleOfflineListening);
document.querySelectorAll("[data-command]").forEach((button) => button.addEventListener("click", () => runCommand(button.dataset.command)));

// Ensure voices are loaded for SpeechSynthesis
if ('speechSynthesis' in window) {
  window.speechSynthesis.getVoices();
  window.speechSynthesis.onvoiceschanged = () => {
    window.speechSynthesis.getVoices();
  };
}

requestStartupPermissions().finally(() => {
  pollStatusUpdates();
});
