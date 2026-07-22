const form = document.querySelector("#settingsForm");
const permissionsList = document.querySelector("#permissionsList");

function applyTheme(theme = "dark") {
  document.body.dataset.theme = theme === "light" ? "light" : "dark";
}

async function loadSettings() {
  const response = await fetch("/api/settings");
  const settings = await response.json();
  applyTheme(settings.theme);
  Object.entries(settings).forEach(([key, value]) => {
    const field = form.elements[key];
    if (!field) return;
    if (field.type === "checkbox") {
      field.checked = Boolean(value);
    } else {
      field.value = value;
    }
  });
}

async function loadPermissions() {
  if (!permissionsList) return;
  const response = await fetch("/api/permissions");
  const permissions = await response.json();
  permissionsList.innerHTML = permissions
    .map(
      (item) => `
        <article class="permission-item">
          <strong>${item.key}</strong>
          <p>${item.rationale}</p>
          <p>${item.granted ? "Ready" : "Permission needed"}</p>
        </article>
      `
    )
    .join("");
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = {};
  new FormData(form).forEach((value, key) => {
    payload[key] = value;
  });
  payload.offline_mode = form.elements.offline_mode.checked;
  payload.cloud_mode = form.elements.cloud_mode.checked;
  payload.confirm_before_call = form.elements.confirm_before_call.checked;
  payload.confirm_before_message = form.elements.confirm_before_message.checked;
  payload.voice_profile_enabled = form.elements.voice_profile_enabled.checked;
  payload.voice_training_complete = form.elements.voice_training_complete.checked;
  payload.speech_speed = Number(payload.speech_speed);
  payload.wake_timeout_seconds = Number(payload.wake_timeout_seconds);
  await fetch("/api/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  await loadSettings();
});

loadSettings();
loadPermissions();
