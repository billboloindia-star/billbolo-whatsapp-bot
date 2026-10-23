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

// Scroll-triggered reveal animations
document.addEventListener('DOMContentLoaded', () => {
    const revealEls = document.querySelectorAll('.reveal');

    if (!('IntersectionObserver' in window) || revealEls.length === 0) {
        revealEls.forEach(el => el.classList.add('is-visible'));
        return;
    }

    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add('is-visible');
                observer.unobserve(entry.target);
            }
        });
    }, { threshold: 0.15, rootMargin: '0px 0px -40px 0px' });

    revealEls.forEach(el => observer.observe(el));
});

// Multi-language support
const SUPPORTED_LANGS = ['en', 'hi', 'bn', 'ta', 'te', 'mr'];
const LANG_STORAGE_KEY = 'billbolo-lang';
const FONT_SCRIPT_BY_LANG = {
    hi: 'lang-devanagari',
    mr: 'lang-devanagari',
    bn: 'lang-bengali',
    ta: 'lang-tamil',
    te: 'lang-telugu',
};

function getSavedLang() {
    try {
        const saved = localStorage.getItem(LANG_STORAGE_KEY);
        return SUPPORTED_LANGS.includes(saved) ? saved : 'en';
    } catch {
        return 'en';
    }
}

function applyTranslations(translations) {
    document.querySelectorAll('[data-i18n]').forEach(el => {
        const key = el.getAttribute('data-i18n');
        if (translations[key]) {
            el.textContent = translations[key];
        }
    });
}

function applyLangScriptClass(lang) {
    document.body.classList.remove('lang-devanagari', 'lang-bengali', 'lang-tamil', 'lang-telugu');
    const scriptClass = FONT_SCRIPT_BY_LANG[lang];
    if (scriptClass) {
        document.body.classList.add(scriptClass);
    }
}

async function loadLanguage(lang) {
    document.documentElement.lang = lang;
    applyLangScriptClass(lang);

    if (lang === 'en') {
        return;
    }

    try {
        const res = await fetch(`locales/${lang}.json`);
        if (!res.ok) throw new Error(`Failed to load ${lang}.json`);
        const translations = await res.json();
        applyTranslations(translations);
    } catch (err) {
        console.error('Language load failed, falling back to English:', err);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    const switcher = document.getElementById('langSwitcher');
    const currentLang = getSavedLang();

    if (switcher) {
        switcher.value = currentLang;
        switcher.addEventListener('change', () => {
            const newLang = switcher.value;
            try {
                localStorage.setItem(LANG_STORAGE_KEY, newLang);
            } catch {
                // localStorage unavailable (private browsing etc) - language just won't persist
            }
            loadLanguage(newLang);
        });
    }

    if (currentLang !== 'en') {
        loadLanguage(currentLang);
    }
});

console.log('🧮 BillBolo - Premium Landing Page Loaded');
