(() => {
  const form = document.querySelector('[data-card-form]');
  if (!form) return;
  const markets = [...form.querySelectorAll('[data-market]')];
  const count = form.querySelector('[data-pick-count]');
  const modes = [...form.querySelectorAll('[data-mode]')];

  const update = () => {
    const checked = form.querySelectorAll('input[type="radio"]:checked:not(:disabled)');
    count.textContent = String(checked.length);
    markets.forEach(card => card.classList.toggle('has-pick', !!card.querySelector('input:checked:not(:disabled)')));
    form.querySelector('button[type="submit"]').disabled = checked.length === 0 || checked.length > 6;
  };
  const setMode = mode => {
    const quick = mode === 'quick';
    modes.forEach(item => item.classList.toggle('active', item.dataset.mode === mode));
    markets.forEach(card => {
      const hidden = quick && card.dataset.quick !== '1';
      card.classList.toggle('build-only', hidden);
      card.querySelectorAll('input').forEach(input => { input.disabled = hidden; });
    });
    update();
  };
  form.addEventListener('change', update);
  modes.forEach(button => button.addEventListener('click', () => setMode(button.dataset.mode)));
  setMode('quick');
})();
