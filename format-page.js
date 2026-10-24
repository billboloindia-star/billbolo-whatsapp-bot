// Quotation Format page interactions: template filter + FAQ accordion

document.addEventListener('DOMContentLoaded', () => {
    // Format filter
    const filterButtons = document.querySelectorAll('.format-filter-btn');
    const cards = document.querySelectorAll('.format-card');

    filterButtons.forEach((btn) => {
        btn.addEventListener('click', () => {
            filterButtons.forEach((b) => b.classList.remove('active'));
            btn.classList.add('active');
            const filter = btn.dataset.filter;
            cards.forEach((card) => {
                const show = filter === 'all' || card.dataset.format === filter;
                card.style.display = show ? '' : 'none';
            });
        });
    });

    // FAQ accordion
    document.querySelectorAll('.faq-item').forEach((item) => {
        item.addEventListener('click', () => {
            const isOpen = item.getAttribute('aria-expanded') === 'true';
            document.querySelectorAll('.faq-item').forEach((i) => i.setAttribute('aria-expanded', 'false'));
            item.setAttribute('aria-expanded', isOpen ? 'false' : 'true');
        });
    });
});
