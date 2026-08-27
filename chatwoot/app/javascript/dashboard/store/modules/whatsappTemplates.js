import * as MutationHelpers from 'shared/helpers/vuex/mutationHelpers';
import types from '../mutation-types';
import WhatsappTemplatesAPI from '../../api/whatsappTemplates';

export const state = {
  records: [],
  uiFlags: {
    isFetching: false,
    isCreating: false,
    isSubmitting: false,
    isUploading: false,
    isSyncing: false,
    isDeleting: false,
  },
};

export const getters = {
  getTemplates: currentState => currentState.records,
  getUIFlags: currentState => currentState.uiFlags,
};

export const actions = {
  get: async ({ commit }) => {
    commit(types.SET_WHATSAPP_TEMPLATE_UI_FLAG, { isFetching: true });
    try {
      const response = await WhatsappTemplatesAPI.get();
      commit(types.SET_WHATSAPP_TEMPLATES, response.data);
    } finally {
      commit(types.SET_WHATSAPP_TEMPLATE_UI_FLAG, { isFetching: false });
    }
  },
  create: async ({ commit }, template) => {
    commit(types.SET_WHATSAPP_TEMPLATE_UI_FLAG, { isCreating: true });
    try {
      const response = await WhatsappTemplatesAPI.create({
        whatsapp_template: template,
      });
      commit(types.ADD_WHATSAPP_TEMPLATE, response.data);
      return response.data;
    } finally {
      commit(types.SET_WHATSAPP_TEMPLATE_UI_FLAG, { isCreating: false });
    }
  },
  update: async ({ commit }, { id, ...template }) => {
    commit(types.SET_WHATSAPP_TEMPLATE_UI_FLAG, { isCreating: true });
    try {
      const response = await WhatsappTemplatesAPI.update(id, {
        whatsapp_template: template,
      });
      commit(types.EDIT_WHATSAPP_TEMPLATE, response.data);
      return response.data;
    } finally {
      commit(types.SET_WHATSAPP_TEMPLATE_UI_FLAG, { isCreating: false });
    }
  },
  submit: async ({ commit }, id) => {
    commit(types.SET_WHATSAPP_TEMPLATE_UI_FLAG, { isSubmitting: true });
    try {
      const response = await WhatsappTemplatesAPI.submit(id);
      commit(types.EDIT_WHATSAPP_TEMPLATE, response.data);
      return response.data;
    } finally {
      commit(types.SET_WHATSAPP_TEMPLATE_UI_FLAG, { isSubmitting: false });
    }
  },
  uploadSample: async ({ commit }, { inboxId, file }) => {
    commit(types.SET_WHATSAPP_TEMPLATE_UI_FLAG, { isUploading: true });
    try {
      const response = await WhatsappTemplatesAPI.uploadSample(inboxId, file);
      return response.data.handle;
    } finally {
      commit(types.SET_WHATSAPP_TEMPLATE_UI_FLAG, { isUploading: false });
    }
  },
  sync: async ({ commit }, inboxId) => {
    commit(types.SET_WHATSAPP_TEMPLATE_UI_FLAG, { isSyncing: true });
    try {
      const response = await WhatsappTemplatesAPI.sync(inboxId);
      commit(types.REPLACE_INBOX_WHATSAPP_TEMPLATES, {
        inboxId,
        templates: response.data,
      });
    } finally {
      commit(types.SET_WHATSAPP_TEMPLATE_UI_FLAG, { isSyncing: false });
    }
  },
  delete: async ({ commit }, id) => {
    commit(types.SET_WHATSAPP_TEMPLATE_UI_FLAG, { isDeleting: true });
    try {
      await WhatsappTemplatesAPI.delete(id);
      commit(types.DELETE_WHATSAPP_TEMPLATE, id);
    } finally {
      commit(types.SET_WHATSAPP_TEMPLATE_UI_FLAG, { isDeleting: false });
    }
  },
};

export const mutations = {
  [types.SET_WHATSAPP_TEMPLATE_UI_FLAG](currentState, data) {
    currentState.uiFlags = { ...currentState.uiFlags, ...data };
  },
  [types.SET_WHATSAPP_TEMPLATES]: MutationHelpers.set,
  [types.ADD_WHATSAPP_TEMPLATE]: MutationHelpers.create,
  [types.EDIT_WHATSAPP_TEMPLATE]: MutationHelpers.update,
  [types.DELETE_WHATSAPP_TEMPLATE]: MutationHelpers.destroy,
  [types.REPLACE_INBOX_WHATSAPP_TEMPLATES](
    currentState,
    { inboxId, templates }
  ) {
    const otherInboxTemplates = currentState.records.filter(
      template => template.inbox_id !== inboxId
    );
    currentState.records = [...templates, ...otherInboxTemplates];
  },
};

export default {
  namespaced: true,
  actions,
  state,
  getters,
  mutations,
};
