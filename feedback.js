/* ============ FEEDBACK WIDGET v1.0 ============ */
(function () {
  'use strict';

  // ---- KONFIG ----
  const ACCESS_KEY = '2cbdf2f1-4b1f-41d2-883b-5a67d0489e8e';
  const SUBJECT = 'Alternative Flashcards — feedback';
  const COOLDOWN_MS = 60 * 1000; // 60 s między wiadomościami
  const COOLDOWN_KEY = 'fbx_last_sent';

  // ---- i18n ----
  const I18N = {
    pl: {
      btn: '💬 Opinia',
      title: 'Wyślij opinię',
      msgLabel: 'Twoja opinia',
      msgPh: 'Co działa? Co poprawić? Co dodać?',
      emailLabel: 'Email (opcjonalnie — jeśli chcesz odpowiedź)',
      emailPh: 'ty@example.com',
      cancel: 'Anuluj',
      send: 'Wyślij',
      sending: 'Wysyłam…',
      ok: '✅ Dziękuję! Opinia wysłana.',
      errGeneric: '❌ Coś poszło nie tak. Spróbuj ponownie.',
      netErr: '❌ Błąd sieci. Spróbuj ponownie.',
      cooldown: '⏳ Poczekaj chwilę przed kolejną wiadomością.',
      tooShort: 'Wpisz co najmniej 5 znaków.',
    },
    en: {
      btn: '💬 Feedback',
      title: 'Send feedback',
      msgLabel: 'Your feedback',
      msgPh: 'What works? What to improve? What to add?',
      emailLabel: 'Email (optional — if you want a reply)',
      emailPh: 'you@example.com',
      cancel: 'Cancel',
      send: 'Send',
      sending: 'Sending…',
      ok: '✅ Thanks! Feedback sent.',
      errGeneric: '❌ Something went wrong. Please try again.',
      netErr: '❌ Network error. Try again.',
      cooldown: '⏳ Please wait before sending another message.',
      tooShort: 'Please enter at least 5 characters.',
    },
    es: {
      btn: '💬 Comentario',
      title: 'Enviar comentario',
      msgLabel: 'Tu comentario',
      msgPh: '¿Qué funciona? ¿Qué mejorar? ¿Qué añadir?',
      emailLabel: 'Email (opcional — si quieres respuesta)',
      emailPh: 'tu@ejemplo.com',
      cancel: 'Cancelar',
      send: 'Enviar',
      sending: 'Enviando…',
      ok: '✅ ¡Gracias! Comentario enviado.',
      errGeneric: '❌ Algo salió mal. Inténtalo de nuevo.',
      netErr: '❌ Error de red. Inténtalo de nuevo.',
      cooldown: '⏳ Espera antes de enviar otro mensaje.',
      tooShort: 'Introduce al menos 5 caracteres.',
    },
  };

  function detectLang() {
    // 1) próbuj localStorage (różne możliwe klucze w Twojej apce)
    const keys = ['lang', 'language', 'i18n', 'locale', 'app-lang'];
    for (const k of keys) {
      const v = localStorage.getItem(k);
      if (v && I18N[v]) return v;
    }
    // 2) próbuj <html lang="...">
    const htmlLang = (document.documentElement.lang || '').slice(0, 2).toLowerCase();
    if (I18N[htmlLang]) return htmlLang;
    // 3) nawigator
    const nav = (navigator.language || 'pl').slice(0, 2).toLowerCase();
    return I18N[nav] ? nav : 'pl';
  }

  const L = I18N[detectLang()];

  // ---- UTIL ----
  function el(tag, attrs = {}, children = []) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === 'class') n.className = v;
      else if (k === 'style') n.style.cssText = v;
      else if (k.startsWith('on') && typeof v === 'function') n[k] = v;
      else if (v !== false && v != null) n.setAttribute(k, v);
    }
    for (const c of [].concat(children)) {
      n.append(typeof c === 'string' ? document.createTextNode(c) : c);
    }
    return n;
  }

  async function getSupabaseUser() {
    try {
      // Preferuj globalnego klienta Supabase, jeśli apka go ma
      const sb = window.supabase?.auth ? window.supabase : window.supabaseClient;
      if (sb?.auth?.getUser) {
        const { data } = await sb.auth.getUser();
        return data?.user ?? null;
      }
      // Fallback: localStorage sesji Supabase (typowy klucz)
      for (let i = 0; i < localStorage.length; i++) {
        const key = localStorage.key(i);
        if (key && key.startsWith('sb-') && key.endsWith('-auth-token')) {
          const raw = localStorage.getItem(key);
          if (raw) {
            const parsed = JSON.parse(raw);
            if (parsed?.user?.email) return parsed.user;
          }
        }
      }
    } catch (_) { /* ignore */ }
    return null;
  }

  // ---- BUILD UI ----
  function build() {
    // FAB
    const fab = el('button', {
      class: 'fbx-fab',
      type: 'button',
      'aria-label': L.btn,
      title: L.btn,
    }, [L.btn]);

    // Modal (ukryty na start)
    const modal = el('div', { class: 'fbx-overlay', hidden: true, role: 'dialog', 'aria-modal': 'true' });

    const box = el('div', { class: 'fbx-modal fbx-relative' });

    const close = el('button', {
      class: 'fbx-close', type: 'button', 'aria-label': 'Close',
    }, ['×']);

    const h2 = el('h2', {}, [L.title]);

    const form = el('form', { novalidate: true });

    // Pole wiadomości
    const fldMsg = el('div', { class: 'fbx-field' });
    const lblMsg = el('label', { for: 'fbx-msg' }, [L.msgLabel]);
    const txtMsg = el('textarea', {
      id: 'fbx-msg', name: 'message', required: true,
      minlength: '5', maxlength: '4000', placeholder: L.msgPh,
    });
    fldMsg.append(lblMsg, txtMsg);

    // Pole email
    const fldEmail = el('div', { class: 'fbx-field' });
    const lblEmail = el('label', { for: 'fbx-email' }, [L.emailLabel]);
    const inpEmail = el('input', {
      id: 'fbx-email', name: 'email', type: 'email',
      placeholder: L.emailPh, autocomplete: 'email',
    });
    fldEmail.append(lblEmail, inpEmail);

    // Honeypot (ukryte — boty wypełnią, ludzie nie)
    const hp = el('input', {
      type: 'text', name: 'company_website_url', tabindex: '-1',
      autocomplete: 'off', class: 'fbx-hp', 'aria-hidden': 'true',
    });

    // Przyciski
    const actions = el('div', { class: 'fbx-actions' });
    const btnCancel = el('button', { class: 'fbx-btn fbx-cancel', type: 'button' }, [L.cancel]);
    const btnSend = el('button', { class: 'fbx-btn fbx-submit', type: 'submit' }, [L.send]);
    actions.append(btnCancel, btnSend);

    // Status
    const status = el('p', { class: 'fbx-status', role: 'status', 'aria-live': 'polite' });

    form.append(fldMsg, fldEmail, hp, actions, status);
    box.append(close, h2, form);
    modal.append(box);

    // ---- Handlers ----
    function open() {
      modal.hidden = false;
      setTimeout(() => txtMsg.focus(), 50);
    }
    function closeModal() {
      modal.hidden = true;
      status.textContent = '';
      status.className = 'fbx-status';
    }

    fab.addEventListener('click', open);
    close.addEventListener('click', closeModal);
    btnCancel.addEventListener('click', closeModal);
    modal.addEventListener('click', (e) => { if (e.target === modal) closeModal(); });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && !modal.hidden) closeModal();
    });

    form.addEventListener('submit', async (e) => {
      e.preventDefault();

      // Honeypot
      if (hp.value) { closeModal(); return; }

      const message = txtMsg.value.trim();
      const email = inpEmail.value.trim();

      if (message.length < 5) {
        status.textContent = L.tooShort;
        status.className = 'fbx-status err';
        return;
      }

      // Cooldown
      const last = Number(localStorage.getItem(COOLDOWN_KEY) || 0);
      if (Date.now() - last < COOLDOWN_MS) {
        status.textContent = L.cooldown;
        status.className = 'fbx-status err';
        return;
      }

      btnSend.disabled = true;
      btnCancel.disabled = true;
      status.textContent = L.sending;
      status.className = 'fbx-status';

      // Kontekst
      const user = await getSupabaseUser();

      // Payload Web3Forms
      const payload = {
        access_key: ACCESS_KEY,
        subject: SUBJECT,
        from_name: 'Alternative Flashcards',
        message: message,
        email: email || (user?.email ?? ''),
        // dodatkowy kontekst w polu "page"
        page: location.pathname + location.search,
        user_id: user?.id ?? '',
        user_email: user?.email ?? '',
        user_agent: navigator.userAgent,
        lang: detectLang(),
        botcheck: '',
      };

      try {
        const res = await fetch('https://api.web3forms.com/submit', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
          body: JSON.stringify(payload),
        });
        const data = await res.json();

        if (res.ok && data.success) {
          status.textContent = L.ok;
          status.className = 'fbx-status ok';
          localStorage.setItem(COOLDOWN_KEY, String(Date.now()));
          form.reset();
          setTimeout(() => {
            closeModal();
          }, 1800);
        } else {
          status.textContent = data.message ? `${L.errGeneric} (${data.message})` : L.errGeneric;
          status.className = 'fbx-status err';
        }
      } catch (err) {
        status.textContent = L.netErr;
        status.className = 'fbx-status err';
      } finally {
        btnSend.disabled = false;
        btnCancel.disabled = false;
      }
    });

    // Wstaw do DOM
    document.body.append(fab, modal);
  }

  // Start
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', build);
  } else {
    build();
  }
})();