/* BetBook – frontend scripts */

document.addEventListener('DOMContentLoaded', () => {

  // Auto-dismiss flash messages after 5 seconds
  document.querySelectorAll('.flash').forEach(el => {
    setTimeout(() => el.remove(), 5000);
  });

  // Bet page: live payout preview
  const amountInput = document.getElementById('amount');
  const payoutBox   = document.getElementById('payout-preview');
  const payoutVal   = document.getElementById('payout-value');
  const odds        = window.GAME_ODDS || {};

  function updatePayout() {
    const amount = parseFloat(amountInput.value);
    const picked = document.querySelector('input[name="pick"]:checked');
    if (!picked || isNaN(amount) || amount <= 0) {
      payoutBox.style.display = 'none';
      return;
    }
    const o = odds[picked.value];
    if (!o) { payoutBox.style.display = 'none'; return; }
    payoutBox.style.display = 'block';
    payoutVal.textContent   = '$' + (amount * o).toFixed(2);
  }

  if (amountInput) {
    amountInput.addEventListener('input', updatePayout);
    document.querySelectorAll('input[name="pick"]').forEach(r => {
      r.addEventListener('change', updatePayout);
    });
  }

  // Admin: close settle modal on overlay click
  const overlay = document.getElementById('settle-modal');
  if (overlay) {
    overlay.addEventListener('click', e => {
      if (e.target === overlay) overlay.style.display = 'none';
    });
  }

});
