(() => {
  const input = document.getElementById("id_keyword");
  const button = document.getElementById("suggest-topic");
  const generateButton = document.getElementById("generate-draft");
  const characterCount = document.getElementById("topic-character-count");
  const status = document.getElementById("topic-suggestion-status");
  const filter = document.getElementById("status-filter");
  const csrfToken = document.querySelector("input[name=csrfmiddlewaretoken]")?.value;
  if (!input || !button || !generateButton || !characterCount || !status || !csrfToken) return;

  const updateTopicState = () => {
    const length = input.value.trim().length;
    characterCount.textContent = `${input.value.length} / 255`;
    generateButton.disabled = length < 2;
    characterCount.classList.toggle("text-danger", input.value.length > 255);
  };

  const setLoadingContent = (element, className, text) => {
    const spinner = document.createElement("span");
    spinner.className = className;
    spinner.setAttribute("aria-hidden", "true");
    element.replaceChildren(spinner, document.createTextNode(text));
  };

  input.addEventListener("input", updateTopicState);
  updateTopicState();

  input.form.addEventListener("submit", () => {
    generateButton.disabled = true;
    setLoadingContent(generateButton, "button-spinner", " Creating your draft…");
  });

  filter?.addEventListener("change", () => filter.form.requestSubmit());

  button.addEventListener("click", async () => {
    button.disabled = true;
    setLoadingContent(button, "suggest-spinner", " Asking Gemini…");
    setLoadingContent(status, "suggest-spinner", " Coming up with a fresh idea…");
    try {
      const response = await fetch(button.dataset.suggestUrl, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
          "X-CSRFToken": csrfToken,
        },
        body: new URLSearchParams({ current_topic: input.value }),
        credentials: "same-origin",
      });
      const contentType = response.headers.get("content-type") || "";
      if (!contentType.includes("application/json")) {
        throw new Error("Your session may have expired. Refresh the page and try again.");
      }
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Gemini could not suggest a topic.");

      input.value = result.topic;
      input.dispatchEvent(new Event("input", { bubbles: true }));
      updateTopicState();
      input.focus();
      input.setSelectionRange(input.value.length, input.value.length);
      status.textContent =
        "Gemini suggested this topic. Edit it if you like, then generate your draft.";
    } catch (error) {
      status.textContent = error.message || "Could not get a topic suggestion. Please try again.";
    } finally {
      button.disabled = false;
      button.textContent = "✧ Suggest a topic";
    }
  });
})();
