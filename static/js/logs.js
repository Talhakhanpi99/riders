const logList = document.querySelector("#logList");

function addText(element, tagName, text) {
  const child = document.createElement(tagName);
  child.textContent = text;
  element.appendChild(child);
}

async function loadRecentCommands() {
  try {
    const response = await fetch("/api/commands/recent");
    if (!response.ok) throw new Error("History request failed");
    const commands = await response.json();
    logList.replaceChildren();
    if (!commands.length) {
      addText(logList, "p", "No commands yet.");
      return;
    }
    commands.forEach((item) => {
      const entry = document.createElement("article");
      entry.className = "log-item";
      addText(entry, "strong", item.command);
      addText(entry, "p", item.response);
      addText(entry, "p", `${item.execution_ms} ms · ${item.success ? "success" : "failed"} · ${item.created_at}`);
      logList.appendChild(entry);
    });
  } catch (error) {
    logList.replaceChildren();
    addText(logList, "p", "Recent commands could not be loaded.");
  }
}

loadRecentCommands();
