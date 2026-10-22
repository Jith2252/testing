(() => {
  'use strict';

  const SOURCE_TYPES = ['mp4', 'webm', 'm3u8', 'mpd', 'unknown'];
  const allowedSchemes = new Set(['http:', 'https:']);
  const suffixes = [
    ['.m3u8', 'm3u8'],
    ['.mpd', 'mpd'],
    ['.mp4', 'mp4'],
    ['.webm', 'webm'],
  ];

  const parseSource = (source) => {
    if (typeof source !== 'string' || !source.trim()) return null;
    try {
      return new URL(source.trim(), 'http://source.invalid');
    } catch {
      return null;
    }
  };

  const hasExplicitScheme = (source) => /^[a-z][a-z0-9+.-]*:/i.test(String(source || '').trim());

  const detectSourceType = (source) => {
    const parsed = parseSource(source);
    if (!parsed) return 'unknown';
    const path = parsed.pathname.toLowerCase();
    const match = suffixes.find(([suffix]) => path.endsWith(suffix));
    return match ? match[1] : 'unknown';
  };

  const isHttpUrl = (source) => {
    const parsed = parseSource(source);
    return Boolean(parsed && hasExplicitScheme(source) && allowedSchemes.has(parsed.protocol) && parsed.hostname);
  };

  const isMixedContent = (source, pageProtocol = window.location.protocol) => {
    const parsed = parseSource(source);
    return Boolean(parsed && String(pageProtocol).toLowerCase() === 'https:' && parsed.protocol === 'http:');
  };

  const validateSourceUrl = (source, pageProtocol = window.location.protocol) => {
    const value = typeof source === 'string' ? source.trim() : '';
    const parsed = parseSource(value);
    if (!parsed) return { valid: false, url: value, type: 'unknown', mixed_content: false, error: 'Enter a source URL.' };
    if (!hasExplicitScheme(value) || !allowedSchemes.has(parsed.protocol)) {
      return { valid: false, url: value, type: 'unknown', mixed_content: false, error: 'Only http:// and https:// URLs are supported.' };
    }
    if (!parsed.hostname) {
      return { valid: false, url: value, type: 'unknown', mixed_content: false, error: 'Enter a complete URL with a valid host.' };
    }
    const mixedContent = isMixedContent(parsed.href, pageProtocol);
    return {
      valid: true,
      url: parsed.href,
      type: detectSourceType(parsed.href),
      mixed_content: mixedContent,
      error: mixedContent ? 'This HTTP URL is blocked as mixed content on an HTTPS page.' : null,
    };
  };

  window.SourceUtils = Object.freeze({
    SOURCE_TYPES: Object.freeze(SOURCE_TYPES),
    detectSourceType,
    isHttpUrl,
    isMixedContent,
    validateSourceUrl,
  });
})();
