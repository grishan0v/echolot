// The time limit. The form works without any of this. After a press the
// browser comes back to the limit by the address (#time-limit); under "All
// settings" the limit is inside a fold, which not every browser opens by
// itself for an address that names something in it.
(function () {
  var el = location.hash && document.getElementById(location.hash.slice(1));
  var fold = el && el.closest && el.closest('details');
  if (!fold || fold.open) return;
  while (fold) { fold.open = true; fold = fold.parentElement && fold.parentElement.closest('details'); }
  el.scrollIntoView({ block: 'start' });
})();
