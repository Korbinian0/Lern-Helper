const uploadForm = document.querySelector("#upload-form");
const fileInput = document.querySelector("#file-input");

if (uploadForm && fileInput) {
  const dropzone = uploadForm;
  const selectedFile = document.querySelector("#selected-file");
  const submitButton = document.querySelector("#submit-upload");
  const message = document.querySelector("#upload-message");

  document.querySelector("#choose-file").addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => {
    const file = fileInput.files[0];
    selectedFile.hidden = !file;
    selectedFile.textContent = file ? `${file.name} · ${(file.size / 1024 / 1024).toFixed(1)} MB` : "";
    submitButton.disabled = !file;
  });
  for (const eventName of ["dragenter", "dragover"]) {
    dropzone.addEventListener(eventName, event => { event.preventDefault(); dropzone.classList.add("is-dragging"); });
  }
  for (const eventName of ["dragleave", "drop"]) {
    dropzone.addEventListener(eventName, event => { event.preventDefault(); dropzone.classList.remove("is-dragging"); });
  }
  dropzone.addEventListener("drop", event => {
    if (event.dataTransfer.files.length) {
      fileInput.files = event.dataTransfer.files;
      fileInput.dispatchEvent(new Event("change"));
    }
  });
  uploadForm.addEventListener("submit", async event => {
    event.preventDefault();
    if (!fileInput.files.length) return;
    submitButton.disabled = true;
    submitButton.innerHTML = "Wird hochgeladen …";
    message.textContent = "Datei wird sicher in die Warteschlange gelegt.";
    message.className = "form-message";
    try {
      const response = await fetch("/api/upload", { method: "POST", body: new FormData(uploadForm) });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || "Upload fehlgeschlagen.");
      window.location.assign("/dashboard");
    } catch (error) {
      message.textContent = error.message;
      message.classList.add("error");
      submitButton.disabled = false;
      submitButton.innerHTML = 'Karten generieren <span>→</span>';
    }
  });
}

const settingsForm = document.querySelector("#settings-form");
if (settingsForm) {
  const modelMessage = document.querySelector("#model-message");
  const output = document.querySelector("#temperature-value");
  document.querySelector("#temperature").addEventListener("input", event => { output.value = event.target.value; });
  document.querySelector("#load-models").addEventListener("click", async () => {
    modelMessage.textContent = "Modelle werden geladen …";
    modelMessage.className = "form-message";
    try {
      const response = await fetch("/api/models");
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || "Modelle konnten nicht geladen werden.");
      for (const id of ["model", "embedding_model"]) {
        const select = document.getElementById(id);
        const current = select.value;
        select.replaceChildren(...(id === "embedding_model" ? [new Option("Nicht verwenden", "")] : []));
        for (const model of body.models) select.add(new Option(model, model));
        select.value = body.models.includes(current) ? current : (id === "model" ? body.models[0] || "" : "");
      }
      modelMessage.textContent = `${body.models.length} Modelle verfügbar.`;
      modelMessage.classList.add("success");
    } catch (error) {
      modelMessage.textContent = error.message;
      modelMessage.classList.add("error");
    }
  });
  settingsForm.addEventListener("submit", async event => {
    event.preventDefault();
    const message = document.querySelector("#settings-message");
    const payload = Object.fromEntries(new FormData(settingsForm));
    for (const key of ["chunk_size", "cards_per_chunk", "context_length"]) payload[key] = Number(payload[key]);
    payload.temperature = Number(payload.temperature);
    message.textContent = "Verbindung wird geprüft und Einstellungen werden gespeichert …";
    message.className = "form-message";
    try {
      const response = await fetch("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || "Speichern fehlgeschlagen.");
      message.textContent = "Verbindung erfolgreich. Einstellungen gespeichert.";
      message.classList.add("success");
    } catch (error) {
      message.textContent = error.message;
      message.classList.add("error");
    }
  });
}

document.addEventListener("click", async event => {
  const flip = event.target.closest(".flip-card");
  if (flip) flip.classList.toggle("flipped");
  const editButton = event.target.closest(".edit-toggle");
  if (editButton) {
    const form = editButton.closest(".study-card").querySelector(".card-edit-form");
    form.hidden = !form.hidden;
  }
  const deleteButton = event.target.closest(".delete-job");
  if (deleteButton && window.confirm("Diesen Auftrag und die gespeicherte Datei endgültig löschen?")) {
    const response = await fetch(`/api/jobs/${deleteButton.dataset.jobId}`, { method: "DELETE" });
    if (response.ok) deleteButton.closest(".job-row").remove();
  }
});

document.addEventListener("submit", async event => {
  const form = event.target.closest(".card-edit-form");
  if (!form) return;
  event.preventDefault();
  const card = form.closest(".study-card");
  const jobId = window.location.pathname.split("/").pop();
  const payload = Object.fromEntries(new FormData(form));
  const status = form.querySelector(".edit-message");
  try {
    const response = await fetch(`/api/jobs/${jobId}/cards/${card.dataset.cardId}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || "Speichern fehlgeschlagen.");
    form.hidden = true;
    card.querySelector(".flip-front p").textContent = body.question;
    card.querySelector(".flip-back p").textContent = body.answer;
  } catch (error) {
    status.textContent = error.message;
    status.classList.add("error");
  }
});