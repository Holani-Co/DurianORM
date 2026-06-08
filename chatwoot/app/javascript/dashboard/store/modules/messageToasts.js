/**
 * Message-toast store module.
 *
 * Holds a small queue of "new incoming message" toasts that slide in from the
 * bottom-right of the dashboard. Toasts are ephemeral (auto-dismiss after a
 * few seconds) and stack up to MAX_VISIBLE — older ones drop out so a burst of
 * messages doesn't bury the screen.
 *
 * Toasts originate from helper/actionCable.js#onMessageCreated, which decides
 * whether each incoming message deserves a toast (skips outgoing / self-viewed
 * conversations) and dispatches `pushMessageToast`.
 */

const MAX_VISIBLE = 3;
const DEFAULT_DURATION_MS = 6000;

// Tiny counter for unique toast ids. Module-level so it survives across calls
// without needing Date.now() / random. Resets on full page reload, which is
// fine — toasts are session-scoped by design.
let toastIdSeq = 0;

const state = {
  // Newest first. Each toast: { id, conversationId, senderName, channelType,
  // contentPreview }
  toasts: [],
};

export const getters = {
  visibleToasts: ({ toasts }) => toasts.slice(0, MAX_VISIBLE),
};

export const actions = {
  pushMessageToast({ commit }, payload) {
    toastIdSeq += 1;
    const toast = {
      id: toastIdSeq,
      conversationId: payload.conversationId,
      senderName: payload.senderName || 'New message',
      channelType: payload.channelType || '',
      contentPreview: payload.contentPreview || '',
    };
    commit('PUSH_TOAST', toast);

    // Auto-dismiss after DEFAULT_DURATION_MS. The setTimeout id isn't tracked
    // because the user may dismiss manually first — DISMISS_TOAST is idempotent
    // (no-op if the toast is already gone), so a stale timer fire is harmless.
    setTimeout(() => commit('DISMISS_TOAST', toast.id), DEFAULT_DURATION_MS);
  },

  dismissMessageToast({ commit }, id) {
    commit('DISMISS_TOAST', id);
  },
};

export const mutations = {
  PUSH_TOAST(s, toast) {
    // Newest at the front. Cap the array so a burst of incoming messages
    // (busy inbox / webhook loop) doesn't grow it unbounded. MAX_VISIBLE * 3
    // gives headroom for transition animations on toasts being dismissed
    // while new ones arrive. setTimeout callbacks for dropped toasts fire
    // harmlessly (DISMISS_TOAST is idempotent on missing ids).
    s.toasts = [toast, ...s.toasts].slice(0, MAX_VISIBLE * 3);
  },
  DISMISS_TOAST(s, id) {
    s.toasts = s.toasts.filter(t => t.id !== id);
  },
};

export default {
  namespaced: true,
  state,
  getters,
  actions,
  mutations,
};
