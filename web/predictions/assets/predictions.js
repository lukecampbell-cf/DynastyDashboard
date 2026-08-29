(() => {
  const form = document.querySelector('[data-card-form]');
  if (!form) return;
  const markets = [...form.querySelectorAll('[data-market]')];
  const count = form.querySelector('[data-pick-count]');
  const modes = [...form.querySelectorAll('[data-mode]')];

  const update = () => {
    const checked = form.querySelectorAll('input[type="radio"]:checked');
    count.textContent = String(checked.length);
    markets.forEach(card => card.classList.toggle('has-pick', !!card.querySelector('input:checked')));
    form.querySelector('button[type="submit"]').disabled = checked.length === 0 || checked.length > 6;
  };
  form.addEventListener('change', update);
  modes.forEach(button => button.addEventListener('click', () => {
    modes.forEach(item => item.classList.toggle('active', item === button));
    const quick = button.dataset.mode === 'quick';
    markets.forEach(card => card.classList.toggle('build-only', quick && card.dataset.quick !== '1'));
  }));
  update();
})();
