const menuToggle = document.querySelector('.menu-toggle');
const menu = document.querySelector('#menu');
const form = document.querySelector('.contact-form');
const formNote = document.querySelector('.form-note');
const yearEl = document.querySelector('#year');
const revealItems = document.querySelectorAll('.reveal');

if (yearEl) {
  yearEl.textContent = String(new Date().getFullYear());
}

if (menuToggle && menu) {
  menuToggle.addEventListener('click', () => {
    const expanded = menuToggle.getAttribute('aria-expanded') === 'true';
    menuToggle.setAttribute('aria-expanded', String(!expanded));
    menu.classList.toggle('is-open');
  });

  menu.querySelectorAll('a').forEach((link) => {
    link.addEventListener('click', () => {
      menu.classList.remove('is-open');
      menuToggle.setAttribute('aria-expanded', 'false');
    });
  });
}

if (form && formNote) {
  form.addEventListener('submit', (event) => {
    event.preventDefault();

    if (!form.checkValidity()) {
      formNote.textContent = 'Please complete all fields with a valid email address.';
      return;
    }

    form.reset();
    formNote.textContent = 'Thanks. Pilot request captured. We will follow up shortly.';
  });
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
      { threshold: 0.12 }
    );

    revealItems.forEach((item) => observer.observe(item));
  }
}
