(() => {
  "use strict";
  const carousel = document.querySelector("[data-carousel]");
  if (!carousel) return;
  const slides = [...carousel.querySelectorAll(".lp-slide")];
  const controls = [...carousel.querySelectorAll("[data-slide]")];
  const pause = carousel.querySelector("[data-pause]");
  const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
  let current = 0;
  let paused = motion.matches;
  let hovering = false;
  let timer;
  // Reserve the tallest slide so rotation never moves the CTAs or page below it.
  function sizeSlides() {
    const mobile = window.matchMedia("(max-width: 760px)").matches;
    slides.forEach((slide) => {
      slide.hidden = false;
      slide.style.minHeight = "";
      slide.querySelector(".lp-hero-copy").style.minHeight = "";
      slide.querySelector(".lp-preview").style.minHeight = "";
    });
    const height = Math.max(...slides.map((slide) => slide.offsetHeight));
    const copyHeight = Math.max(
      ...slides.map(
        (slide) => slide.querySelector(".lp-hero-copy").offsetHeight,
      ),
    );
    const previewHeight = Math.max(
      ...slides.map((slide) => slide.querySelector(".lp-preview").offsetHeight),
    );
    slides.forEach((slide, i) => {
      if (mobile) {
        slide.querySelector(".lp-hero-copy").style.minHeight =
          `${copyHeight}px`;
        slide.querySelector(".lp-preview").style.minHeight =
          `${previewHeight}px`;
      } else {
        slide.style.minHeight = `${height}px`;
      }
      slide.hidden = i !== current;
    });
  }
  let resizeFrame;
  window.addEventListener("resize", () => {
    window.cancelAnimationFrame(resizeFrame);
    resizeFrame = window.requestAnimationFrame(sizeSlides);
  });
  sizeSlides();
  document.fonts.ready.then(sizeSlides);
  function schedule() {
    window.clearTimeout(timer);
    if (
      !paused &&
      !hovering &&
      !document.hidden &&
      (document.activeElement === pause ||
        !carousel.contains(document.activeElement))
    ) {
      timer = window.setTimeout(() => {
        show((current + 1) % slides.length);
      }, 6000);
    }
  }
  function show(index) {
    current = index;
    slides.forEach((slide, i) => {
      slide.hidden = i !== current;
    });
    controls.forEach((control, i) => {
      control.setAttribute("aria-pressed", String(i === current));
    });
    schedule();
  }
  function updatePause() {
    pause.textContent = paused ? "▶" : "Ⅱ";
    pause.setAttribute(
      "aria-label",
      paused ? "Play slideshow" : "Pause slideshow",
    );
    schedule();
  }
  controls.forEach((control, i) =>
    control.addEventListener("click", () => {
      show(i);
    }),
  );
  pause.addEventListener("click", () => {
    paused = !paused;
    updatePause();
  });
  carousel.querySelector(".lp-slide-controls").addEventListener("mouseenter", () => {
    hovering = true;
    schedule();
  });
  carousel.querySelector(".lp-slide-controls").addEventListener("mouseleave", () => {
    hovering = false;
    schedule();
  });
  carousel.addEventListener("focusin", schedule);
  carousel.addEventListener("focusout", () => {
    window.setTimeout(schedule, 0);
  });
  document.addEventListener("visibilitychange", schedule);
  motion.addEventListener("change", () => {
    paused = motion.matches;
    updatePause();
  });
  updatePause();
})();
