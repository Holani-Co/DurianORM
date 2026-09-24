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
    // Media samples (esp. videos up to 16 MB) are read into memory and relayed
    // to Meta's upload API, which is slow on a small VM — give it room so a
    // valid-but-large file isn't killed mid-upload (surfaces as a generic error).
    return axios.post(`${this.url}/upload_sample`, formData, {
      timeout: 180000,
    });
  }
}

export default new WhatsappTemplatesAPI();
