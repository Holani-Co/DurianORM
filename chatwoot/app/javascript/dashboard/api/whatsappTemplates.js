/* global axios */

import ApiClient from './ApiClient';

class WhatsappTemplatesAPI extends ApiClient {
  constructor() {
    super('whatsapp_templates', { accountScoped: true });
  }

  submit(id) {
    return axios.post(`${this.url}/${id}/submit`);
  }

  sync(inboxId) {
    return axios.post(`${this.url}/sync`, { inbox_id: inboxId });
  }

  uploadSample(inboxId, file) {
    const formData = new FormData();
    formData.append('inbox_id', inboxId);
    formData.append('file', file);
    return axios.post(`${this.url}/upload_sample`, formData);
  }
}

export default new WhatsappTemplatesAPI();
