import * as MutationHelpers from 'shared/helpers/vuex/mutationHelpers';
import types from '../mutation-types';
import WhatsappConsentsAPI from '../../api/whatsappConsents';

export const state = {
  records: [],
  uiFlags: { isFetching: false, isCreating: false, isImporting: false },
};

export const getters = {
  getConsents: currentState => currentState.records,
  getUIFlags: currentState => currentState.uiFlags,
};

export const actions = {
  get: async ({ commit }) => {
    commit(types.SET_WHATSAPP_CONSENT_UI_FLAG, { isFetching: true });
    try {
      const response = await WhatsappConsentsAPI.get();
      commit(types.SET_WHATSAPP_CONSENTS, response.data);
    } finally {
      commit(types.SET_WHATSAPP_CONSENT_UI_FLAG, { isFetching: false });
    }
  },
  create: async ({ commit }, consent) => {
    commit(types.SET_WHATSAPP_CONSENT_UI_FLAG, { isCreating: true });
    try {
      const response = await WhatsappConsentsAPI.create({
        whatsapp_consent: consent,
      });
      commit(types.ADD_WHATSAPP_CONSENT, response.data);
      return response.data;
    } finally {
      commit(types.SET_WHATSAPP_CONSENT_UI_FLAG, { isCreating: false });
    }
  },
  import: async ({ commit }, { file, inboxId, label }) => {
    commit(types.SET_WHATSAPP_CONSENT_UI_FLAG, { isImporting: true });
    try {
      const formData = new FormData();
      formData.append('import_file', file);
      formData.append('inbox_id', inboxId);
      formData.append('label', label);
      await WhatsappConsentsAPI.importConsents(formData);
    } finally {
      commit(types.SET_WHATSAPP_CONSENT_UI_FLAG, { isImporting: false });
    }
  },
};

export const mutations = {
  [types.SET_WHATSAPP_CONSENT_UI_FLAG](currentState, data) {
    currentState.uiFlags = { ...currentState.uiFlags, ...data };
  },
  [types.SET_WHATSAPP_CONSENTS]: MutationHelpers.set,
  [types.ADD_WHATSAPP_CONSENT]: MutationHelpers.create,
};

export default { namespaced: true, actions, state, getters, mutations };
