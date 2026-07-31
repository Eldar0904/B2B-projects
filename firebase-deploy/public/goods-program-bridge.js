(function () {
  function apiBase() {
    var el = document.getElementById('goodsProgramApiInput');
    var fromInput = el && el.value ? String(el.value).trim().replace(/\/+$/, '') : '';
    var fromConfig =
      typeof window !== 'undefined' && window.GOODS_PROGRAM_API_BASE
        ? String(window.GOODS_PROGRAM_API_BASE).replace(/\/+$/, '')
        : '';
    return fromInput || fromConfig || 'http://localhost:8000/api/v1/matching';
  }

  function healthUrl() {
    if (window.GOODS_PROGRAM_HEALTH_URL) {
      return String(window.GOODS_PROGRAM_HEALTH_URL).replace(/\/+$/, '');
    }
    try {
      return new URL(apiBase()).origin + '/health';
    } catch (e) {
      return 'http://localhost:8000/health';
    }
  }

  function setStatus(text, ok) {
    var status = document.getElementById('goodsProgramStatus');
    if (!status) return;
    status.textContent = text;
    status.style.color = ok ? 'var(--green)' : 'var(--red, #dc2626)';
  }

  function refreshIframe() {
    var frame = document.getElementById('goodsProgramFrame');
    if (!frame) return;
    var api = encodeURIComponent(apiBase());
    frame.src = 'goods-matching/index.html?api=' + api + '&t=' + Date.now();
  }

  async function checkHealth() {
    var page = window.location;
    if (page.protocol === 'file:') {
      setStatus(
        'Дашборд открыт как файл (file://). Запустите start-dashboard.bat и откройте http://localhost:5500/… — иначе браузер блокирует запросы к API.',
        false
      );
      return;
    }
    if (page.protocol === 'https:') {
      setStatus(
        'Сайт открыт по HTTPS (Firebase). Браузер не даст ходить на http://localhost:8000 с этой страницы. Локально: start-dashboard.bat + start-goods-program.bat. Для онлайн-подбора нужен API в облаке с HTTPS (см. README).',
        false
      );
      return;
    }

    setStatus('Проверка API…', true);
    var url = healthUrl();
    try {
      var r = await fetch(url, { mode: 'cors' });
      if (r.ok) {
        setStatus('API доступен — ' + url, true);
      } else {
        setStatus('API ответил с кодом ' + r.status + ' (' + url + ')', false);
      }
    } catch (e) {
      setStatus(
        'API недоступен (' +
          url +
          '). 1) Окно start-goods-program.bat открыто и в нём есть строка Uvicorn running. 2) В браузере откройте ' +
          url +
          ' — должно быть {"status":"ok"}. 3) Дашборд только через http://localhost:5500, не file://.',
        false
      );
    }
  }

  function bind() {
    var refreshBtn = document.getElementById('goodsProgramRefreshBtn');
    var openBtn = document.getElementById('goodsProgramOpenFullBtn');
    var apiInput = document.getElementById('goodsProgramApiInput');

    if (apiInput && window.GOODS_PROGRAM_API_BASE) {
      apiInput.value = window.GOODS_PROGRAM_API_BASE;
    }

    if (refreshBtn) {
      refreshBtn.addEventListener('click', function () {
        checkHealth();
        refreshIframe();
      });
    }
    if (apiInput) {
      apiInput.addEventListener('change', refreshIframe);
      apiInput.addEventListener('blur', refreshIframe);
    }
    if (openBtn) {
      openBtn.addEventListener('click', function (e) {
        e.preventDefault();
        window.open('http://localhost:3000/matching', '_blank', 'noopener');
      });
    }

    refreshIframe();
    checkHealth();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bind);
  } else {
    bind();
  }
})();
