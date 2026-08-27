import ApiClient from './ApiClient';

class WhatsappConsentsAPI extends ApiClient {
  constructor() {
    super('whatsapp_consents', { accountScoped: true });
  }
}

export default new WhatsappConsentsAPI();
