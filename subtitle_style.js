(() => {
  'use strict';

  const STORAGE_KEY = 'local-video-player:subtitle-style';
  // Match subtitle_style.py and GET /config: opacity is a fraction from 0 to 1.
  const defaults = { fontSize: 24, color: '#ffffff', backgroundOpacity: 0.5, position: 'bottom' };
  const overlay = document.querySelector('#subtitleOverlay');
  const fontSizeInput = document.querySelector('#subtitleFontSize');
  const fontSizeValue = document.querySelector('#subtitleFontSizeValue');
  const colorInput = document.querySelector('#subtitleColor');
  const backgroundOpacityInput = document.querySelector('#subtitleBackgroundOpacity');
  const backgroundOpacityValue = document.querySelector('#subtitleBackgroundOpacityValue');
  const positionSelect = document.querySelector('#subtitlePosition');

  const boundedInteger = (value, min, max, fallback) => {
    if ((typeof value !== 'string' && typeof value !== 'number') || String(value).trim() === '') return fallback;
    const number = Number(value);
    return Number.isFinite(number) ? Math.min(max, Math.max(min, Math.round(number))) : fallback;
  };

  const boundedNumber = (value, min, max, fallback) => {
    if ((typeof value !== 'string' && typeof value !== 'number') || String(value).trim() === '') return fallback;
    const number = Number(value);
    return Number.isFinite(number) ? Math.min(max, Math.max(min, number)) : fallback;
  };

  const normalize = (value) => {
    const saved = value && typeof value === 'object' && !Array.isArray(value) ? value : {};
    return {
      fontSize: boundedInteger(saved.fontSize, 12, 48, defaults.fontSize),
      color: typeof saved.color === 'string' && /^#[0-9a-f]{6}$/i.test(saved.color) ? saved.color.toLowerCase() : defaults.color,
      backgroundOpacity: boundedNumber(saved.backgroundOpacity, 0, 1, defaults.backgroundOpacity),
      position: ['bottom', 'middle', 'top'].includes(saved.position) ? saved.position : defaults.position,
    };
  };

  let settings = defaults;
  try {
    settings = normalize(JSON.parse(localStorage.getItem(STORAGE_KEY)));
  } catch {
    // Invalid or unavailable storage should not prevent subtitle styling.
  }

  const apply = () => {
    overlay.style.fontSize = `${settings.fontSize}px`;
    overlay.style.color = settings.color;
    overlay.style.setProperty('--subtitle-background-opacity', String(settings.backgroundOpacity));
    overlay.dataset.position = settings.position;
    fontSizeInput.value = String(settings.fontSize);
    fontSizeValue.textContent = `${settings.fontSize} px`;
    colorInput.value = settings.color;
    backgroundOpacityInput.value = String(Math.round(settings.backgroundOpacity * 100));
    backgroundOpacityValue.textContent = `${Math.round(settings.backgroundOpacity * 100)}%`;
    positionSelect.value = settings.position;
  };

  const update = (changes) => {
    settings = normalize({ ...settings, ...changes });
    apply();
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
    } catch {
      // Settings still work for this page when storage is blocked or full.
    }
  };

  fontSizeInput.addEventListener('input', () => update({ fontSize: fontSizeInput.value }));
  colorInput.addEventListener('input', () => update({ color: colorInput.value }));
  backgroundOpacityInput.addEventListener('input', () => update({ backgroundOpacity: Number(backgroundOpacityInput.value) / 100 }));
  positionSelect.addEventListener('change', () => update({ position: positionSelect.value }));
  apply();
})();
