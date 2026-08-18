import MailDraftsAPI from '../../api/mailDrafts';

// Durian — team-shared email drafts saved from the compose flow. Backs the
// left-nav "Drafts" folder; resuming one reopens the composer pre-filled.
export const state = {
  records: [],
  uiFlags: { isFetching: false },
};

export const getters = {
  getMailDrafts: _state => _state.records,
  getMailDraftsCount: _state => _state.records.length,
  getUIFlags: _state => _state.uiFlags,
};

export const actions = {
  get: async ({ commit }) => {
    commit('setUIFlag', { isFetching: true });
    try {
      const { data } = await MailDraftsAPI.get();
      commit('setMailDrafts', data);
    } finally {
      commit('setUIFlag', { isFetching: false });
    }
  },
  create: async ({ commit }, params) => {
    const { data } = await MailDraftsAPI.create(params);
    commit('upsertMailDraft', data);
    return data;
  },
  update: async ({ commit }, { id, ...params }) => {
    const { data } = await MailDraftsAPI.update(id, params);
    commit('upsertMailDraft', data);
    return data;
  },
  delete: async ({ commit }, id) => {
    await MailDraftsAPI.delete(id);
    commit('removeMailDraft', id);
  },
};

export const mutations = {
  setUIFlag($state, flag) {
    $state.uiFlags = { ...$state.uiFlags, ...flag };
  },
  setMailDrafts($state, records) {
    $state.records = records;
  },
  upsertMailDraft($state, record) {
    const index = $state.records.findIndex(r => r.id === record.id);
    if (index === -1) {
      $state.records.unshift(record);
    } else {
      $state.records.splice(index, 1, record);
    }
  },
  removeMailDraft($state, id) {
    $state.records = $state.records.filter(r => r.id !== id);
  },
};

export default { namespaced: true, state, getters, actions, mutations };
