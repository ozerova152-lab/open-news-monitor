const button = document.querySelector('[data-copy]');
const prompt = document.querySelector('#prompt');
const status = document.querySelector('[data-status]');

if (button && prompt && status && navigator.clipboard?.writeText) {
  button.hidden = false;
  button.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(prompt.textContent);
      status.textContent = status.dataset.ok;
    } catch {
      status.textContent = status.dataset.error;
    }
  });
}
