import axios from 'axios';
import ApiClient from './ApiClient';

class WhatsappConsentsAPI extends ApiClient {
  constructor() {
    super('whatsapp_consents', { accountScoped: true });
  }

  importConsents(formData) {
    return axios.post(`${this.url}/import`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
  }
}

export default new WhatsappConsentsAPI();
