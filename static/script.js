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
  const oddsData    = window.GAME_ODDS || { moneyline: {}, spread: {} };

  window.updatePayout = function() {
    const amount  = parseFloat(amountInput ? amountInput.value : 0);
    const picked  = document.querySelector('input[name="pick"]:checked');
    const betType = (document.getElementById('bet_type') || {}).value || 'moneyline';

    if (!picked || isNaN(amount) || amount <= 0 || !payoutBox) return;

    const odds = (oddsData[betType] || {})[picked.value];
    if (!odds) { payoutBox.style.display = 'none'; return; }

    payoutBox.style.display = 'block';
    payoutVal.textContent   = '$' + (amount * odds).toFixed(2);
  };

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

// Called from bet.html when user clicks a pick section
function setBetType(type) {
  const el = document.getElementById('bet_type');
  if (el) el.value = type;
  if (typeof updatePayout === 'function') updatePayout();
}
