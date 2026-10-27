document.addEventListener("submit", (event) => {
  const form = event.target;
  if (!(form instanceof HTMLFormElement) || !form.dataset.confirm) return;
  if (!window.confirm(form.dataset.confirm)) event.preventDefault();
});
