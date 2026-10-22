// Smooth scroll
function scrollTo(selector) {
    const element = document.querySelector(selector);
    if (element) {
        element.scrollIntoView({ behavior: 'smooth' });
    }
}

// Privacy Policy Modal
function closePrivacy() {
    const modal = document.getElementById('privacy-modal');
    modal.style.display = 'none';
}

// Privacy triggers
document.addEventListener('DOMContentLoaded', () => {
    const privacyTriggers = document.querySelectorAll('.privacy-trigger');
    const modal = document.getElementById('privacy-modal');

    privacyTriggers.forEach(trigger => {
        trigger.addEventListener('click', (e) => {
            e.preventDefault();
            modal.style.display = 'block';
        });
    });
});

// Close modal when clicking outside
window.addEventListener('click', (event) => {
    const modal = document.getElementById('privacy-modal');
    if (event.target === modal) {
        modal.style.display = 'none';
    }
});

// Navbar blur effect on scroll
window.addEventListener('scroll', () => {
    const navbar = document.querySelector('.navbar');
    if (window.scrollY > 50) {
        navbar.style.background = 'rgba(255, 255, 255, 0.85)';
        navbar.style.boxShadow = '0 4px 12px rgba(0, 0, 0, 0.08)';
    } else {
        navbar.style.background = 'rgba(255, 255, 255, 0.7)';
        navbar.style.boxShadow = 'none';
    }
});

console.log('🧮 BillBolo - Premium Landing Page Loaded');
