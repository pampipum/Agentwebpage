const yearEl = document.querySelector('#year');
const revealItems = document.querySelectorAll('.reveal');

if (yearEl) {
  yearEl.textContent = String(new Date().getFullYear());
}

if (revealItems.length > 0) {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    revealItems.forEach((item) => item.classList.add('in-view'));
  } else {
    const observer = new IntersectionObserver(
      (entries, obs) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add('in-view');
            obs.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.15 }
    );

    revealItems.forEach((item) => observer.observe(item));
  }
}
