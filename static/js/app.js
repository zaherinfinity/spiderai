// AI Translate Video — frontend helpers
(function () {
  const toggle = document.getElementById("navToggle");
  const links = document.querySelector(".nav-links");
  if (toggle && links) {
    toggle.addEventListener("click", () => links.classList.toggle("open"));
  }

  document.querySelectorAll(".flash").forEach((el) => {
    setTimeout(() => {
      el.style.opacity = "0";
      el.style.transition = "opacity 0.4s";
      setTimeout(() => el.remove(), 400);
    }, 4500);
  });

  const dz = document.getElementById("dropzone");
  const fileInput = document.getElementById("videoFile");
  if (dz && fileInput) {
    ["dragenter", "dragover"].forEach((ev) => {
      dz.addEventListener(ev, (e) => {
        e.preventDefault();
        dz.classList.add("dragover");
      });
    });
    ["dragleave", "drop"].forEach((ev) => {
      dz.addEventListener(ev, (e) => {
        e.preventDefault();
        dz.classList.remove("dragover");
      });
    });
    dz.addEventListener("drop", (e) => {
      if (e.dataTransfer.files.length) {
        fileInput.files = e.dataTransfer.files;
        updateFileLabel(e.dataTransfer.files[0].name);
      }
    });
    dz.addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", () => {
      if (fileInput.files[0]) updateFileLabel(fileInput.files[0].name);
    });
  }

  function updateFileLabel(name) {
    const el = document.getElementById("fileName");
    if (el) el.textContent = name;
  }
})();

function pollJob(jobId) {
  const statusEl = document.getElementById("jobStatus");
  const fillEl = document.getElementById("progressFill");
  const pctEl = document.getElementById("progressPct");
  const msgEl = document.getElementById("progressMsg");
  const resultEl = document.getElementById("jobResult");
  const errBox = document.getElementById("jobError");
  const errText = document.getElementById("jobErrorText");

  const steps = ["pending", "downloading", "transcribing", "translating", "tts", "mixing", "uploading", "completed"];

  function setStep(status) {
    document.querySelectorAll(".step").forEach((s) => {
      const key = s.dataset.step;
      s.classList.remove("active", "done");
      const idx = steps.indexOf(key);
      const cur = steps.indexOf(status);
      if (key === status) s.classList.add("active");
      else if (idx >= 0 && cur >= 0 && idx < cur) s.classList.add("done");
      if (status === "completed") s.classList.add("done");
    });
  }

  function showResult(watchUrl, downloadUrl) {
    if (!resultEl) return;
    const watch = watchUrl || "/media/" + jobId;
    const dl = downloadUrl || "/download/" + jobId;
    resultEl.innerHTML =
      '<div class="video-box">' +
      '<video id="resultVideo" controls playsinline preload="metadata" src="' + watch + '">' +
      "Your browser does not support video playback.</video></div>" +
      '<div style="text-align:center;margin-top:1rem;display:flex;flex-wrap:wrap;gap:0.75rem;justify-content:center">' +
      '<a class="btn btn-primary" href="' + dl + '">Download video</a>' +
      '<a class="btn btn-ghost" href="' + watch + '" target="_blank" rel="noopener">Open in new tab</a>' +
      "</div>";
    const v = document.getElementById("resultVideo");
    if (v) {
      v.addEventListener("error", function () {
        if (msgEl) msgEl.textContent = "Video file could not be loaded. Try Download.";
      });
    }
  }

  async function tick() {
    try {
      const res = await fetch("/api/job/" + jobId, { cache: "no-store" });
      const data = await res.json();
      const status = data.status || "pending";
      const pct = data.progress || 0;
      const msg = data.message || status;

      if (fillEl) fillEl.style.width = pct + "%";
      if (pctEl) pctEl.textContent = pct + "%";
      if (msgEl) msgEl.textContent = msg;
      if (statusEl) statusEl.textContent = status;
      setStep(status);

      if (status === "completed") {
        if (fillEl) fillEl.style.width = "100%";
        if (pctEl) pctEl.textContent = "100%";
        if (msgEl) msgEl.textContent = data.message || "Ready";
        const watch = data.output_url || (data.has_file ? "/media/" + jobId : null);
        const dl = data.download_url || (data.has_file ? "/download/" + jobId : null);
        if (watch || data.has_file) {
          showResult(watch, dl);
        } else {
          // file not ready yet — keep polling briefly
          setTimeout(tick, 1500);
          return;
        }
        return;
      }

      if (status === "failed") {
        if (msgEl) msgEl.textContent = data.error || msg;
        if (errBox) errBox.style.display = "block";
        if (errText) errText.textContent = data.error || msg || "Processing failed";
        return;
      }

      setTimeout(tick, 1800);
    } catch (e) {
      setTimeout(tick, 3000);
    }
  }
  tick();
}
