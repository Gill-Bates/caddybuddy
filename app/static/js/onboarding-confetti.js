(() => {
    "use strict";

    if (!document.querySelector("[data-onboarding-confetti]")) {
        return;
    }

    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
        return;
    }

    if (typeof window.confetti !== "function") {
        return;
    }

    if (document.querySelector(".onboarding-confetti-banner")) {
        return;
    }

    const banner = document.createElement("div");
    banner.className = "onboarding-confetti-banner";
    banner.setAttribute("role", "status");
    banner.setAttribute("aria-live", "polite");
    banner.textContent = "Congratulations!";
    document.body.appendChild(banner);

    setTimeout(() => {
        banner.classList.add("is-hiding");
    }, 100);

    setTimeout(() => {
        banner.remove();
    }, 2400);

    const colors = ["#16a34a", "#22c55e", "#86efac", "#ffffff", "#bbf7d0"];
    const end = Date.now() + 800;

    const frame = () => {
        window.confetti({
            particleCount: 6,
            angle: 60,
            spread: 50,
            startVelocity: 48,
            origin: { x: 0.12, y: 0.82 },
            colors,
            decay: 0.92,
        });
        window.confetti({
            particleCount: 6,
            angle: 120,
            spread: 50,
            startVelocity: 48,
            origin: { x: 0.88, y: 0.82 },
            colors,
            decay: 0.92,
        });
        if (Date.now() < end) {
            requestAnimationFrame(frame);
        }
    };

    frame();
})();
