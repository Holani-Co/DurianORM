/**
 * vue-i18n `postTranslation` hook for white-labeling.
 *
 * Runs on every rendered translation and replaces "Chatwoot" with the
 * configured installation name (INSTALLATION_NAME / window.globalConfig). This
 * is the global counterpart to useBranding's `replaceInstallationName` — it
 * covers every user-facing string without wrapping each call site, and follows
 * the same switch (so it's a no-op when the name is unset or still "Chatwoot").
 *
 * Case-sensitive on purpose: it rebrands the displayed brand word "Chatwoot"
 * but leaves lowercase technical references (e.g. "chatwoot.com" URLs) alone.
 *
 * @param {*} translated - the translated value from vue-i18n
 * @returns {*} the value with the brand word swapped (strings only)
 */
export const installationNamePostTranslation = translated => {
  if (typeof translated !== 'string') return translated;
  const name = window.globalConfig?.INSTALLATION_NAME;
  if (!name || name === 'Chatwoot') return translated;
  return translated.replace(/Chatwoot/g, name);
};
